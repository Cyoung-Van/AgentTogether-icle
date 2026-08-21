"""Immutable RunBundle ingest. This project does not execute Agents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from .public_dataset import DatasetError, sha256_file, stable_record_id


RUN_BUNDLE_SCHEMA = "experience-evaluation-run-bundle/v0.1"
RUN_BUNDLE_STATUSES = {"completed", "subject_failure", "infrastructure_failure", "invalid"}
REQUIRED_FIELDS = (
    "run_id",
    "task_spec_id",
    "task_revision",
    "subject_id",
    "started_at",
    "ended_at",
    "status",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_for_hash(bundle: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in bundle.items() if key != "content_hash"}


def compute_content_hash(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload_for_hash(bundle)).encode("utf-8")).hexdigest()


def validate_run_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(bundle, dict):
        raise DatasetError("RunBundle must be an object")
    if bundle.get("schema_version") != RUN_BUNDLE_SCHEMA:
        raise DatasetError(f"RunBundle schema must be {RUN_BUNDLE_SCHEMA}")
    for field in REQUIRED_FIELDS:
        value = bundle.get(field)
        if not isinstance(value, str) or not value:
            raise DatasetError(f"RunBundle requires {field}")
    if bundle.get("status") not in RUN_BUNDLE_STATUSES:
        raise DatasetError(f"RunBundle status is invalid: {bundle.get('status')}")
    outcome = bundle.get("outcome")
    if not isinstance(outcome, dict):
        raise DatasetError("RunBundle requires outcome object")
    metrics = outcome.get("metrics")
    if metrics is None:
        metrics = {}
    if not isinstance(metrics, dict):
        raise DatasetError("RunBundle outcome.metrics must be an object")
    expected = compute_content_hash(bundle)
    content_hash = bundle.get("content_hash")
    if content_hash is None:
        return {**bundle, "content_hash": expected}
    if content_hash != expected:
        raise DatasetError(
            f"RunBundle hash mismatch for {bundle['run_id']}: expected {expected}, got {content_hash}"
        )
    return bundle


def to_execution_result(bundle: dict[str, Any]) -> dict[str, Any]:
    validated = validate_run_bundle(bundle)
    outcome = validated["outcome"]
    return {
        "result_id": validated["run_id"],
        "subject_id": validated["subject_id"],
        "task_id": validated["task_spec_id"],
        "task_revision": validated["task_revision"],
        "status": validated["status"],
        "metrics": dict(outcome.get("metrics") or {}),
        "provenance": {
            "run_bundle_schema": RUN_BUNDLE_SCHEMA,
            "content_hash": validated["content_hash"],
            "agent_revision_id": validated.get("agent_revision_id"),
            "runner": validated.get("runner") or {},
            "started_at": validated["started_at"],
            "ended_at": validated["ended_at"],
        },
    }


def load_run_bundles(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    if path.is_dir():
        files = sorted(item for item in path.iterdir() if item.is_file() and item.suffix in {".json", ".jsonl"})
        rows = []
        for item in files:
            rows.extend(load_run_bundles(item))
        return rows
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        rows = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid RunBundle JSONL {path}:{lineno}: {exc}") from exc
            rows.append(validate_run_bundle(value))
        return rows
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"invalid RunBundle JSON {path}: {exc}") from exc
    if isinstance(value, list):
        return [validate_run_bundle(item) for item in value]
    if isinstance(value, dict):
        return [validate_run_bundle(value)]
    raise DatasetError(f"RunBundle file must be an object or array: {path}")


class RunBundleStore:
    """Idempotent on-disk ingest. Same run_id+hash is a no-op; hash conflict fails."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.jsonl"

    def _index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        rows = []
        for lineno, line in enumerate(self.index_path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise DatasetError(f"RunBundle index row must be an object: {self.index_path}:{lineno}")
            rows.append(row)
        return rows

    def ingest(self, bundles: Sequence[dict[str, Any]]) -> dict[str, Any]:
        index = self._index()
        by_run = {row["run_id"]: row for row in index}
        accepted = []
        duplicates = 0
        for bundle in bundles:
            validated = validate_run_bundle(bundle)
            existing = by_run.get(validated["run_id"])
            if existing is not None:
                if existing.get("content_hash") == validated["content_hash"]:
                    duplicates += 1
                    continue
                raise DatasetError(
                    f"RunBundle conflict for {validated['run_id']}: same run_id, different content_hash"
                )
            relative = f"bundles/{validated['run_id']}.json"
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(validated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            record = {
                "schema_version": "experience-evaluation-run-bundle-index/v0.1",
                "ingest_id": stable_record_id("run-ingest", validated["run_id"], validated["content_hash"]),
                "run_id": validated["run_id"],
                "content_hash": validated["content_hash"],
                "path": relative,
                "file_sha256": sha256_file(path),
                "subject_id": validated["subject_id"],
                "task_spec_id": validated["task_spec_id"],
            }
            index.append(record)
            by_run[validated["run_id"]] = record
            accepted.append(record)
        if accepted:
            self.index_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in index),
                encoding="utf-8",
            )
        return {
            "accepted": len(accepted),
            "duplicates": duplicates,
            "index_count": len(index),
            "records": accepted,
        }

    def execution_results(self) -> list[dict[str, Any]]:
        results = []
        for row in self._index():
            bundle = json.loads((self.root / row["path"]).read_text(encoding="utf-8"))
            results.append(to_execution_result(bundle))
        return results
