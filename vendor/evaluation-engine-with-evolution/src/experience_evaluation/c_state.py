"""Persistent, append-only dynamic correction state for canonical C observations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

from .canonical_measurement import AXIS_CONTRACT_ID, CANONICAL_SCALE_ID, validate_axis_subset
from .public_dataset import DatasetError, stable_record_id


C_STATE_SCHEMA = "experience-evaluation-c-state/v0.1"
C_REVISION_SCHEMA = "experience-evaluation-c-revision/v0.1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise DatasetError(f"invalid C observation timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise DatasetError("C observation timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


class CStateStore:
    """Bayesian dynamic state with decay, process noise, idempotency and hash chain."""

    def __init__(self, root: str | Path, config: dict[str, Any], *, read_only: bool = False):
        self.root = Path(root)
        self.config = self._validate_config(config)
        self.config_path = self.root / "config.json"
        if read_only and not self.root.is_dir():
            raise DatasetError("C state store does not exist")
        if not read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            existing = json.loads(self.config_path.read_text(encoding="utf-8"))
            if existing != self.config:
                raise DatasetError("CStateStore config differs from persisted contract")
        elif read_only:
            raise DatasetError("C state store config is missing")
        else:
            _atomic_write(
                self.config_path,
                json.dumps(self.config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            )
        self.revisions_path = self.root / "revisions.jsonl"
        self.rejections_path = self.root / "rejections.jsonl"
        self.current_path = self.root / "current.json"

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
        required = (
            "contract_id", "initial_mean", "initial_sd", "mean_half_life_days",
            "process_variance_per_day", "min_observation_sd",
        )
        if any(key not in config for key in required):
            raise DatasetError("C state config is incomplete")
        normalized = {
            "contract_id": str(config["contract_id"]),
            "initial_mean": float(config["initial_mean"]),
            "initial_sd": float(config["initial_sd"]),
            "mean_half_life_days": float(config["mean_half_life_days"]),
            "process_variance_per_day": float(config["process_variance_per_day"]),
            "min_observation_sd": float(config["min_observation_sd"]),
            "axis_contract_id": AXIS_CONTRACT_ID,
            "scale_id": CANONICAL_SCALE_ID,
        }
        if normalized["initial_sd"] <= 0:
            raise DatasetError("C initial_sd must be positive")
        if normalized["mean_half_life_days"] <= 0:
            raise DatasetError("C mean_half_life_days must be positive")
        if normalized["process_variance_per_day"] < 0:
            raise DatasetError("C process variance cannot be negative")
        if normalized["min_observation_sd"] <= 0:
            raise DatasetError("C min_observation_sd must be positive")
        return normalized

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"invalid C state JSONL {path}:{lineno}") from exc
            if not isinstance(row, dict):
                raise DatasetError(f"C state row must be object: {path}:{lineno}")
            rows.append(row)
        return rows

    def _write_jsonl(self, path: Path, rows: Sequence[dict[str, Any]]) -> None:
        _atomic_write(
            path,
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        )

    def _load_current(self) -> dict[str, dict[str, Any]]:
        if not self.current_path.exists():
            return {}
        doc = json.loads(self.current_path.read_text(encoding="utf-8"))
        return {
            f"{row['subject_id']}\x1f{row['dimension_id']}": row
            for row in doc.get("states", [])
        }

    def _write_current(self, states: dict[str, dict[str, Any]]) -> None:
        doc = {
            "schema_version": "experience-evaluation-c-state-projection/v0.1",
            "config_contract_id": self.config["contract_id"],
            "axis_contract_id": AXIS_CONTRACT_ID,
            "scale_id": CANONICAL_SCALE_ID,
            "states": sorted(states.values(), key=lambda row: (row["subject_id"], row["dimension_id"])),
        }
        _atomic_write(
            self.current_path,
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _revision_hash(revision: dict[str, Any]) -> str:
        return _sha({key: value for key, value in revision.items() if key != "revision_hash"})

    def verify_and_rebuild(self, *, rebuild_projection: bool = True) -> dict[str, Any]:
        revisions = self._read_jsonl(self.revisions_path)
        states: dict[str, dict[str, Any]] = {}
        previous_hash = "GENESIS"
        for expected_seq, revision in enumerate(revisions, start=1):
            if revision.get("revision_seq") != expected_seq:
                raise DatasetError("C revision sequence is not contiguous")
            if revision.get("previous_revision_hash") != previous_hash:
                raise DatasetError("C revision previous hash mismatch")
            actual_hash = self._revision_hash(revision)
            if revision.get("revision_hash") != actual_hash:
                raise DatasetError("C revision hash mismatch")
            previous_hash = actual_hash
            key = f"{revision['subject_id']}\x1f{revision['dimension_id']}"
            states[key] = {
                "schema_version": C_STATE_SCHEMA,
                "state_id": stable_record_id(
                    "c-state", revision["subject_id"], revision["dimension_id"]
                ),
                "subject_id": revision["subject_id"],
                "dimension_id": revision["dimension_id"],
                "mean": revision["posterior_mean"],
                "variance": revision["posterior_variance"],
                "sd": math.sqrt(revision["posterior_variance"]),
                "ci_low": revision["posterior_mean"] - 1.96 * math.sqrt(revision["posterior_variance"]),
                "ci_high": revision["posterior_mean"] + 1.96 * math.sqrt(revision["posterior_variance"]),
                "evidence_count": revision["evidence_count"],
                "last_observed_at": revision["observed_at"],
                "latest_revision_id": revision["revision_id"],
                "latest_revision_hash": revision["revision_hash"],
                "latest_source_bundle_id": revision["source_bundle_id"],
                "latest_calibration_contract_id": revision.get("calibration_contract_id"),
                "latest_warnings": revision.get("warnings", []),
                "latest_publication_scope": revision.get("publication_scope"),
                "latest_evidence_origin": revision.get("evidence_origin"),
                "axis_contract_id": AXIS_CONTRACT_ID,
                "scale_id": CANONICAL_SCALE_ID,
            }
        if rebuild_projection:
            self._write_current(states)
        return {
            "revision_count": len(revisions),
            "state_count": len(states),
            "head_revision_hash": previous_hash,
        }

    def get(self, subject_id: str, dimension_id: str) -> Optional[dict[str, Any]]:
        return self._load_current().get(f"{subject_id}\x1f{dimension_id}")

    def list_states(self) -> list[dict[str, Any]]:
        return sorted(
            self._load_current().values(),
            key=lambda row: (row["subject_id"], row["dimension_id"]),
        )

    def _reject(
        self,
        rejections: list[dict[str, Any]],
        cell: dict[str, Any],
        reason: str,
        source_bundle_id: str,
    ) -> None:
        rejections.append({
            "schema_version": "experience-evaluation-c-state-rejection/v0.1",
            "rejection_id": stable_record_id(
                "c-rejection", cell.get("cell_id"), reason, source_bundle_id
            ),
            "cell_id": cell.get("cell_id"),
            "subject_id": cell.get("subject_id"),
            "dimension_id": cell.get("dimension_id"),
            "reason": reason,
            "source_bundle_id": source_bundle_id,
        })

    def update(
        self,
        c_cells: Sequence[dict[str, Any]],
        *,
        observed_at: str,
        source_bundle_id: str,
    ) -> dict[str, Any]:
        observed_time = _parse_time(observed_at)
        integrity = self.verify_and_rebuild() if self.revisions_path.exists() else {
            "revision_count": 0, "head_revision_hash": "GENESIS"
        }
        revisions = self._read_jsonl(self.revisions_path)
        existing_rejections = self._read_jsonl(self.rejections_path)
        states = self._load_current()
        processed = {
            row["observation_id"]: row["observation_content_hash"]
            for row in revisions
        }
        new_revisions = []
        new_rejections = []
        duplicates = 0
        previous_hash = integrity["head_revision_hash"]
        for cell in sorted(
            c_cells,
            key=lambda row: (
                str(row.get("subject_id")), str(row.get("dimension_id")), str(row.get("cell_id"))
            ),
        ):
            cell_id = cell.get("cell_id")
            content_hash = _sha(cell)
            if not isinstance(cell_id, str) or not cell_id:
                self._reject(new_rejections, cell, "cell_id_missing", source_bundle_id)
                continue
            if cell_id in processed:
                if processed[cell_id] == content_hash:
                    duplicates += 1
                else:
                    self._reject(new_rejections, cell, "cell_id_content_conflict", source_bundle_id)
                continue
            if cell.get("status") != "available":
                self._reject(new_rejections, cell, "c_cell_unavailable", source_bundle_id)
                continue
            dimension = cell.get("dimension_id")
            try:
                validate_axis_subset([dimension])
            except DatasetError:
                self._reject(new_rejections, cell, "dimension_not_canonical", source_bundle_id)
                continue
            if cell.get("scale_id") != CANONICAL_SCALE_ID:
                self._reject(new_rejections, cell, "scale_mismatch", source_bundle_id)
                continue
            value = cell.get("value")
            stderr = cell.get("stderr")
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                self._reject(new_rejections, cell, "c_value_invalid", source_bundle_id)
                continue
            if stderr is None:
                stderr = self.config["min_observation_sd"]
            elif not isinstance(stderr, (int, float)) or stderr < 0 or not math.isfinite(float(stderr)):
                self._reject(new_rejections, cell, "c_stderr_invalid", source_bundle_id)
                continue
            subject_id = str(cell.get("subject_id") or "")
            if not subject_id:
                self._reject(new_rejections, cell, "subject_id_missing", source_bundle_id)
                continue
            key = f"{subject_id}\x1f{dimension}"
            previous_state = states.get(key)
            if previous_state is None:
                previous_mean = self.config["initial_mean"]
                previous_variance = self.config["initial_sd"] ** 2
                delta_days = 0.0
                evidence_count = 0
            else:
                previous_mean = float(previous_state["mean"])
                previous_variance = float(previous_state["variance"])
                previous_time = _parse_time(previous_state["last_observed_at"])
                delta_days = (observed_time - previous_time).total_seconds() / 86400.0
                if delta_days < 0:
                    self._reject(new_rejections, cell, "observation_precedes_current_state", source_bundle_id)
                    continue
                evidence_count = int(previous_state["evidence_count"])
            decay_factor = 0.5 ** (delta_days / self.config["mean_half_life_days"])
            # Decay reverts toward the configured prior mean, not toward a hard 0.
            # With initial_mean = 0 these agree; with a non-zero prior, pulling to 0
            # would invent a residual the contract never assumed.
            prior_mean = float(self.config["initial_mean"])
            predicted_mean = prior_mean + (previous_mean - prior_mean) * decay_factor
            predicted_variance = (
                previous_variance
                + self.config["process_variance_per_day"] * delta_days
            )
            observation_sd = max(float(stderr), self.config["min_observation_sd"])
            observation_variance = observation_sd ** 2
            posterior_variance = 1.0 / (
                1.0 / predicted_variance + 1.0 / observation_variance
            )
            posterior_mean = posterior_variance * (
                predicted_mean / predicted_variance
                + float(value) / observation_variance
            )
            seq = len(revisions) + len(new_revisions) + 1
            revision = {
                "schema_version": C_REVISION_SCHEMA,
                "revision_seq": seq,
                "revision_id": stable_record_id(
                    "c-revision", seq, cell_id, content_hash
                ),
                "previous_revision_hash": previous_hash,
                "subject_id": subject_id,
                "dimension_id": dimension,
                "observation_id": cell_id,
                "observation_content_hash": content_hash,
                "observation_value": float(value),
                "observation_stderr": float(stderr),
                "effective_observation_sd": observation_sd,
                "observed_at": observed_time.isoformat(),
                "source_bundle_id": source_bundle_id,
                "calibration_contract_id": cell.get("calibration_contract_id"),
                "c_generator_version": cell.get("c_generator_version"),
                "warnings": list(cell.get("warnings") or []),
                "publication_scope": cell.get("publication_scope"),
                "evidence_origin": cell.get("evidence_origin"),
                "delta_days": delta_days,
                "decay_factor": decay_factor,
                "previous_mean": previous_mean,
                "previous_variance": previous_variance,
                "predicted_mean": predicted_mean,
                "predicted_variance": predicted_variance,
                "posterior_mean": posterior_mean,
                "posterior_variance": posterior_variance,
                "evidence_count": evidence_count + 1,
                "axis_contract_id": AXIS_CONTRACT_ID,
                "scale_id": CANONICAL_SCALE_ID,
                "config_contract_id": self.config["contract_id"],
            }
            revision["revision_hash"] = self._revision_hash(revision)
            previous_hash = revision["revision_hash"]
            new_revisions.append(revision)
            processed[cell_id] = content_hash
            states[key] = {
                "schema_version": C_STATE_SCHEMA,
                "state_id": stable_record_id("c-state", subject_id, dimension),
                "subject_id": subject_id,
                "dimension_id": dimension,
                "mean": posterior_mean,
                "variance": posterior_variance,
                "sd": math.sqrt(posterior_variance),
                "ci_low": posterior_mean - 1.96 * math.sqrt(posterior_variance),
                "ci_high": posterior_mean + 1.96 * math.sqrt(posterior_variance),
                "evidence_count": evidence_count + 1,
                "last_observed_at": observed_time.isoformat(),
                "latest_revision_id": revision["revision_id"],
                "latest_revision_hash": revision["revision_hash"],
                "latest_source_bundle_id": source_bundle_id,
                "latest_calibration_contract_id": cell.get("calibration_contract_id"),
                "latest_warnings": list(cell.get("warnings") or []),
                "latest_publication_scope": cell.get("publication_scope"),
                "latest_evidence_origin": cell.get("evidence_origin"),
                "axis_contract_id": AXIS_CONTRACT_ID,
                "scale_id": CANONICAL_SCALE_ID,
            }
        all_revisions = revisions + new_revisions
        all_rejections = existing_rejections + new_rejections
        if new_revisions:
            self._write_jsonl(self.revisions_path, all_revisions)
        if new_rejections:
            self._write_jsonl(self.rejections_path, all_rejections)
        self._write_current(states)
        return {
            "schema_version": "experience-evaluation-c-state-update/v0.1",
            "updated": len(new_revisions),
            "duplicates": duplicates,
            "rejected": len(new_rejections),
            "revisions": new_revisions,
            "rejections": new_rejections,
            "state_count": len(states),
            "head_revision_hash": previous_hash,
        }
