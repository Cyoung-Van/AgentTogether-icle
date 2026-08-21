"""Build an auditable public Agent/Model evaluation dataset.

The source files remain immutable evidence.  This module writes normalized,
append-friendly JSONL projections plus a manifest with source hashes.  It does
not invent scores or merge model/harness identities by fuzzy matching.
"""

from __future__ import annotations

import hashlib
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Tuple
from itertools import combinations
from urllib.parse import unquote, urlparse


class DatasetError(ValueError):
    """Raised when public data violates the dataset contract."""


def normalize_id(value: str) -> str:
    """Return a conservative stable identifier without fuzzy aliases."""
    if not isinstance(value, str) or not value.strip():
        raise DatasetError("identity value must be a non-empty string")
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise DatasetError(f"identity cannot be normalized: {value!r}")
    return normalized


def stable_record_id(prefix: str, *parts: object) -> str:
    """Create a readable ID with a hash of the exact, non-normalized identity."""
    exact = "\x1f".join(str(part) for part in parts)
    digest = hashlib.sha256(exact.encode("utf-8")).hexdigest()[:16]
    readable = normalize_id("-".join(str(part) for part in parts))[:48]
    return f"{normalize_id(prefix)}-{readable}-{digest}"


_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
    "deepseek": "deepseek",
    "kimi": "moonshot",
    "moonshot-ai": "moonshot",
    "moonshot": "moonshot",
    "meta": "meta",
    "z-ai": "zai",
    "zai": "zai",
    "minimax": "minimax",
    "xai": "xai",
    "x-ai": "xai",
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "mistral-ai": "mistral",
    "mistral": "mistral",
}


def canonical_model_id(model: str, provider: Optional[str] = None) -> str:
    """Keep provider identity without merging distinct model revisions."""
    if "/" in model:
        raw_provider, model_leaf = model.split("/", 1)
        if provider:
            model = model_leaf
        else:
            provider = raw_provider
            model = model_leaf
    model_id = normalize_id(model)
    if not provider:
        provider = infer_provider(model)
    if not provider:
        return model_id
    provider_raw = normalize_id(provider)
    provider_id = _PROVIDER_ALIASES.get(provider_raw, provider_raw)
    if model_id == provider_id or model_id.startswith(provider_id + "-"):
        return model_id
    return f"{provider_id}-{model_id}"


def infer_provider(model: str) -> Optional[str]:
    """Infer only explicit vendor-branded model families; never fuzzy-match."""
    model_id = normalize_id(model)
    prefixes = (
        ("gpt-", "openai"),
        ("o1", "openai"),
        ("o3", "openai"),
        ("o4", "openai"),
        ("claude-", "anthropic"),
        ("gemini-", "google"),
        ("deepseek-", "deepseek"),
        ("kimi-", "moonshot"),
        ("qwen", "alibaba"),
        ("glm-", "zai"),
        ("grok-", "xai"),
        ("minimax-", "minimax"),
        ("llama-", "meta"),
        ("muse-", "meta"),
        ("mistral-", "mistral"),
        ("devstral-", "mistral"),
        ("codestral-", "mistral"),
        ("amazon-", "amazon"),
    )
    for prefix, provider in prefixes:
        if model_id.startswith(prefix):
            return provider
    return None


def merge_repository_metadata(
    harnesses: Sequence[dict[str, Any]],
    metadata_by_url: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach repository health metadata without changing stable identities."""
    merged: list[dict[str, Any]] = []
    for harness in harnesses:
        record = dict(harness)
        source_url = str(record.get("source_url") or "")
        metadata = metadata_by_url.get(source_url)
        if metadata is not None:
            license_data = metadata.get("license") or {}
            record["repository"] = {
                "stars": metadata.get("stargazers_count"),
                "forks": metadata.get("forks_count"),
                "open_issues": metadata.get("open_issues_count"),
                "pushed_at": metadata.get("pushed_at"),
                "archived": metadata.get("archived"),
                "license": license_data.get("spdx_id"),
            }
        merged.append(record)
    return merged


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"invalid JSON source {path}: {exc}") from exc


def _jsonl(path: Path) -> Iterable[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetError(f"cannot read source {path}: {exc}") from exc
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSONL {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise DatasetError(f"JSONL row must be an object: {path}:{lineno}")
        yield value


def _parse_detail_url_identity(detail_url: Any) -> dict[str, Any]:
    """Preserve version labels encoded by the upstream leaderboard URL."""
    if not isinstance(detail_url, str) or not detail_url.strip():
        return {
            "detail_url": None,
            "detail_url_identity_status": "not_available",
            "agent_version": None,
            "agent_version_label": None,
            "agent_version_evidence_status": "not_available",
            "model_revision": None,
            "model_revision_evidence_status": "not_available",
            "provider_route": None,
        }
    try:
        parts = [unquote(part) for part in urlparse(detail_url).path.split("/") if part]
    except ValueError:
        parts = []
    if len(parts) < 3 or "leaderboard" not in parts:
        return {
            "detail_url": detail_url,
            "detail_url_identity_status": "unparsed_source_url",
            "agent_version": None,
            "agent_version_label": None,
            "agent_version_evidence_status": "unparsed",
            "model_revision": None,
            "model_revision_evidence_status": "unparsed",
            "provider_route": None,
        }
    agent_version_label = parts[-2]
    model_route = parts[-1]
    model_revision, separator, provider_route = model_route.rpartition("@")
    if not separator:
        model_revision, provider_route = model_route, None
    version_is_exact = normalize_id(agent_version_label) not in {
        "unknown", "stable", "latest", "default", "none", "unspecified"
    }
    return {
        "detail_url": detail_url,
        "detail_url_identity_status": "parsed_from_source_url",
        "agent_version": agent_version_label if version_is_exact else None,
        "agent_version_label": agent_version_label,
        "agent_version_evidence_status": "parsed_exact_label" if version_is_exact else "non_revision_label",
        "model_revision": model_revision or None,
        "model_revision_evidence_status": "parsed_source_label" if model_revision else "not_available",
        "provider_route": provider_route or None,
    }


def parse_agent_psychometrics(
    path: Path,
    *,
    source_id: str,
    benchmark_id: str,
    identity_map: Optional[dict[str, dict[str, str]]] = None,
) -> Tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expand an Agent Psychometrics response matrix into long-form outcomes."""
    pairs: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    seen_subjects: set[str] = set()
    for row in _jsonl(path):
        subject_raw = str(row.get("subject_id") or "").strip()
        mapped = (identity_map or {}).get(subject_raw) or {}
        agent_display = str(row.get("agent") or mapped.get("harness") or "").strip()
        model_display = str(row.get("model") or mapped.get("model") or "").strip()
        responses = row.get("responses")
        if not subject_raw:
            raise DatasetError(f"missing subject_id in {path}")
        if row.get("agent") and row.get("model"):
            identity_status = "source_metadata"
        elif agent_display and model_display:
            identity_status = "upstream_mapping"
        else:
            unresolved = stable_record_id("unresolved", source_id, subject_raw)
            agent_display = unresolved
            model_display = unresolved
            identity_status = "unresolved"
        if not isinstance(responses, dict):
            raise DatasetError(f"responses must be an object for {subject_raw}")
        subject_id = normalize_id(subject_raw)
        if subject_id in seen_subjects:
            raise DatasetError(f"duplicate subject_id in {path}: {subject_raw}")
        seen_subjects.add(subject_id)
        agent_id = normalize_id(agent_display)
        model_id = canonical_model_id(
            model_display, row.get("model_org") or mapped.get("model_org")
        )
        observation_id = stable_record_id("obs", source_id, subject_raw)
        detail_identity = _parse_detail_url_identity(row.get("detail_url"))
        pair = {
            "schema_version": "experience-evaluation-agent-model-observation/v0.1",
            "observation_id": observation_id,
            "source_id": source_id,
            "benchmark_id": benchmark_id,
            "subject_id": subject_id,
            "agent_id": agent_id,
            "agent_display_name": agent_display,
            "agent_version": detail_identity["agent_version"],
            "agent_version_label": detail_identity["agent_version_label"],
            "agent_version_evidence_status": detail_identity["agent_version_evidence_status"],
            "agent_org": row.get("agent_org"),
            "model_id": model_id,
            "model_revision": detail_identity["model_revision"],
            "model_revision_evidence_status": detail_identity["model_revision_evidence_status"],
            "provider_route": detail_identity["provider_route"],
            "model_display_name": model_display,
            "model_org": row.get("model_org"),
            "reasoning_effort": None,
            "observed_at": row.get("date"),
            "reported_accuracy": row.get("accuracy"),
            "reported_rank": row.get("rank"),
            "task_count": len(responses),
            "evidence_granularity": "task_level",
            "source_record_id": subject_raw,
            "identity_status": identity_status,
            "detail_url": detail_identity["detail_url"],
            "detail_url_identity_status": detail_identity["detail_url_identity_status"],
            "response_semantics": "aggregated_binary_task_response",
        }
        pairs.append(pair)
        for task_id, outcome in sorted(responses.items()):
            if outcome not in (0, 1, False, True, None):
                raise DatasetError(
                    f"outcome for {subject_raw}/{task_id} must be 0, 1, or null"
                )
            outcomes.append(
                {
                    "schema_version": "experience-evaluation-task-outcome/v0.1",
                    "outcome_id": stable_record_id(
                        "outcome", source_id, subject_raw, task_id
                    ),
                    "source_id": source_id,
                    "benchmark_id": benchmark_id,
                    "observation_id": observation_id,
                    "subject_id": subject_id,
                    "agent_id": agent_id,
                    "model_id": model_id,
                    "task_id": str(task_id),
                    "outcome": None if outcome is None else int(bool(outcome)),
                    "outcome_status": "unknown" if outcome is None else "observed",
                    "response_semantics": "aggregated_binary_task_response",
                }
            )
    return pairs, outcomes


def parse_tb21_submission(path: Path, *, source_id: str) -> dict[str, Any]:
    """Normalize one official Terminal-Bench 2.1 submission summary."""
    row = _read_json(path)
    source_filter = row.get("source_filter") or {}
    metadata = row.get("metadata") or {}
    metrics = row.get("metrics") or {}
    agent_raw = str(source_filter.get("agent") or "").strip()
    model_raw = str(source_filter.get("model_name") or "").strip()
    effort = str(source_filter.get("reasoning_effort") or metadata.get("reasoning_effort") or "").strip()
    if not agent_raw or not model_raw:
        raise DatasetError(f"TB2.1 submission lacks agent/model: {path}")
    agent_display = (metadata.get("agent_display") or {}).get("label") or agent_raw
    model_display = (metadata.get("model_display") or {}).get("label") or model_raw
    agent_id = normalize_id(agent_raw)
    model_id = canonical_model_id(model_raw, (metadata.get("model_org") or {}).get("label"))
    source_record_id = path.stem
    observation_id = stable_record_id("obs", source_id, source_record_id)
    return {
        "schema_version": "experience-evaluation-agent-model-observation/v0.1",
        "observation_id": observation_id,
        "source_id": source_id,
        "benchmark_id": "terminal-bench-2.1",
        "subject_id": normalize_id(f"{agent_raw}-{model_raw}-{effort or 'unspecified'}"),
        "agent_id": agent_id,
        "agent_display_name": agent_display,
        "agent_version": source_filter.get("agent_version"),
        "agent_org": (metadata.get("agent_org") or {}).get("label"),
        "model_id": model_id,
        "model_display_name": model_display,
        "model_org": (metadata.get("model_org") or {}).get("label"),
        "reasoning_effort": effort or None,
        "observed_at": metadata.get("date"),
        "reported_accuracy": metrics.get("accuracy"),
        "accuracy_stderr": metrics.get("accuracy_stderr"),
        "n_trials": metrics.get("n_trials"),
        "pass_at_2": metrics.get("pass_at_2"),
        "pass_at_3": metrics.get("pass_at_3"),
        "pass_at_4": metrics.get("pass_at_4"),
        "pass_at_5": metrics.get("pass_at_5"),
        "uncached_input_tokens": metrics.get("uncached_input_tokens"),
        "cached_input_tokens": metrics.get("cached_input_tokens"),
        "output_tokens": metrics.get("output_tokens"),
        "total_cost_usd": metrics.get("total_cost_usd"),
        "avg_trial_duration_sec": metrics.get("avg_trial_duration_sec"),
        "reward_hacks_percent": metrics.get("reward_hacks"),
        "trial_ids": list(row.get("trials") or []),
        "source_jobs": list(row.get("source_jobs") or []),
        "source_url": (metadata.get("pr_url") or {}).get("url")
        if isinstance(metadata.get("pr_url"), dict)
        else metadata.get("pr_url"),
        "evidence_granularity": "submission_summary",
        "source_record_id": source_record_id,
        "identity_status": "source_metadata",
    }


def parse_livebench_subtasks(
    table_path: Path,
    categories_path: Path,
    *,
    source_id: str,
) -> list[dict[str, Any]]:
    """Parse LiveBench's model-by-subtask table as Matrix A observations."""
    categories = _read_json(categories_path)
    category_by_item = {
        str(item): str(category)
        for category, items in categories.items()
        for item in items
    }
    records: list[dict[str, Any]] = []
    with table_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            source_model_id = str(row.get("model") or "").strip()
            if not source_model_id:
                raise DatasetError(f"LiveBench row lacks model: {table_path}")
            for item_id, raw_score in row.items():
                if item_id == "model":
                    continue
                raw = str(raw_score or "").strip()
                score = None if not raw else float(raw)
                records.append(
                    {
                        "schema_version": "experience-evaluation-matrix-a-observation/v0.1",
                        "record_id": stable_record_id("a", source_id, source_model_id, item_id),
                        "matrix": "A",
                        "source_id": source_id,
                        "benchmark_id": "livebench",
                        "benchmark_item_id": item_id,
                        "category": category_by_item.get(item_id, "Unmapped"),
                        "model_id": canonical_model_id(source_model_id),
                        "source_model_id": source_model_id,
                        "score": score,
                        "score_status": "unknown" if score is None else "observed",
                        "score_scale": "0_100",
                        "evidence_granularity": "subtask_score",
                    }
                )
    return records


_NON_COMPARABLE_MODEL_LABELS = {"multiple", "mixed", "various", "unknown", "unspecified"}

_EXACT_MATCH_FIELDS = (
    "benchmark_version",
    "task_revision",
    "fixture_hash",
    "verifier_revision",
    "environment_revision",
    "resource_limits",
    "timeout_policy",
    "context_policy",
    "attempt_policy",
    "provider_route",
    "model_revision",
    "reasoning_effort",
)


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _row_value(obs: dict[str, Any], outcome: dict[str, Any], field: str) -> Any:
    value = outcome.get(field)
    return obs.get(field) if _is_missing(value) else value


def _frozen_match_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _model_identity_is_comparable(model_id: Any) -> bool:
    normalized = normalize_id(str(model_id)) if not _is_missing(model_id) else "unknown"
    return not any(
        normalized == label or normalized.endswith(f"-{label}")
        for label in _NON_COMPARABLE_MODEL_LABELS
    )


def _exact_configuration(
    obs: dict[str, Any], outcome: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    config = {field: _row_value(obs, outcome, field) for field in _EXACT_MATCH_FIELDS}
    reasons: list[str] = []
    if _is_missing(config["benchmark_version"]):
        reasons.append("benchmark_version_unknown")
    if _is_missing(config["task_revision"]) and _is_missing(config["fixture_hash"]):
        reasons.append("task_revision_and_fixture_unknown")
    for field in (
        "verifier_revision",
        "environment_revision",
        "resource_limits",
        "timeout_policy",
        "context_policy",
        "attempt_policy",
        "provider_route",
        "model_revision",
        "reasoning_effort",
    ):
        if _is_missing(config[field]):
            reasons.append(f"{field}_unknown")
    if _is_missing(obs.get("agent_version")):
        reasons.append("harness_revision_unknown")
    return config, reasons


def _evidence_ref(obs: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_id": obs.get("observation_id"),
        "outcome_id": outcome.get("outcome_id"),
        "source_id": outcome.get("source_id") or obs.get("source_id"),
        "source_record_id": obs.get("source_record_id"),
        "observed_at": obs.get("observed_at"),
    }


def _provisional_revision_id(obs: dict[str, Any]) -> str:
    version = obs.get("agent_version")
    if not _is_missing(version):
        return f"{obs['agent_id']}@{version}"
    evidence_identity = stable_record_id(
        "provisional-revision",
        obs.get("source_id"),
        obs.get("source_record_id"),
        obs.get("observed_at"),
        obs.get("observation_id"),
    )
    return f"{obs['agent_id']}@{evidence_identity}"


def build_b_matched_dataset(
    observations: Sequence[dict[str, Any]],
    outcomes: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Build strict Exact blocks and separately retain weaker same-label blocks."""
    obs_by_id = {row["observation_id"]: row for row in observations}
    exact_grouped: dict[tuple[str, ...], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = {}
    provisional_grouped: dict[tuple[str, str, str, Optional[str]], list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    exact_eligible: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    rejected: list[dict[str, Any]] = []

    def reject(
        outcome: dict[str, Any], *, stage: str, reason: str, reasons: Optional[list[str]] = None
    ) -> None:
        rejected.append({
            "schema_version": "experience-evaluation-b-rejection/v0.2",
            "outcome_id": outcome.get("outcome_id"),
            "observation_id": outcome.get("observation_id"),
            "stage": stage,
            "reason": reason,
            "reasons": reasons or [reason],
        })

    for outcome in outcomes:
        obs = obs_by_id.get(outcome.get("observation_id"))
        if obs is None:
            reject(outcome, stage="input_validation", reason="missing_observation")
            continue
        if obs.get("identity_status") == "unresolved":
            reject(outcome, stage="identity_resolution", reason="identity_unresolved")
            continue
        if obs.get("evidence_granularity") != "task_level":
            reject(outcome, stage="evidence_granularity", reason="not_task_level")
            continue
        if outcome.get("outcome_status") != "observed" or outcome.get("outcome") not in (0, 1):
            reject(outcome, stage="outcome_validation", reason="outcome_not_observed")
            continue
        identity_checks = (
            ("benchmark_id", "benchmark_identity_mismatch"),
            ("model_id", "model_identity_mismatch"),
            ("agent_id", "harness_identity_mismatch"),
        )
        identity_conflict = next((
            reason
            for field, reason in identity_checks
            if not _is_missing(outcome.get(field))
            and not _is_missing(obs.get(field))
            and str(outcome[field]) != str(obs[field])
        ), None)
        if identity_conflict:
            reject(outcome, stage="record_consistency", reason=identity_conflict)
            continue
        if not _model_identity_is_comparable(obs.get("model_id")):
            reject(outcome, stage="model_identity", reason="model_identity_non_comparable")
            continue

        provisional_key = (
            str(outcome["benchmark_id"]),
            str(outcome["task_id"]),
            str(obs["model_id"]),
            obs.get("reasoning_effort"),
        )
        provisional_grouped.setdefault(provisional_key, []).append((obs, outcome))

        config, gate_reasons = _exact_configuration(obs, outcome)
        if gate_reasons:
            reject(
                outcome,
                stage="exact_gate_completeness",
                reason=gate_reasons[0],
                reasons=gate_reasons,
            )
            continue
        exact_key = (
            str(outcome["benchmark_id"]),
            str(outcome["task_id"]),
            str(obs["model_id"]),
            *(_frozen_match_value(config[field]) for field in _EXACT_MATCH_FIELDS),
        )
        row = (obs, outcome, config)
        exact_grouped.setdefault(exact_key, []).append(row)
        exact_eligible.append(row)

    provisional_blocks: list[dict[str, Any]] = []
    for key, rows in sorted(
        provisional_grouped.items(), key=lambda item: tuple(_frozen_match_value(v) for v in item[0])
    ):
        if len({obs["agent_id"] for obs, _ in rows}) < 2:
            continue
        by_revision: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for obs, outcome in rows:
            by_revision.setdefault(_provisional_revision_id(obs), []).append((obs, outcome))
        members = []
        for revision_id, evidence_rows in sorted(by_revision.items()):
            first_obs = evidence_rows[0][0]
            values = [int(outcome["outcome"]) for _, outcome in evidence_rows]
            members.append({
                "harness_id": first_obs["agent_id"],
                "harness_version": first_obs.get("agent_version"),
                "provisional_harness_revision_id": revision_id,
                "attempt_count": len(values),
                "mean_outcome": sum(values) / len(values),
                "outcomes": values,
                "evidence_refs": [_evidence_ref(obs, outcome) for obs, outcome in evidence_rows],
            })
        benchmark_id, task_id, model_id, effort = key
        provisional_blocks.append({
            "schema_version": "experience-evaluation-b-provisional-block/v0.1",
            "block_id": stable_record_id("b-provisional", *key),
            "benchmark_id": benchmark_id,
            "task_id": task_id,
            "model_id": model_id,
            "reasoning_effort": effort,
            "match_quality": "same_labels_only_not_exact",
            "members": members,
        })

    matched_blocks: list[dict[str, Any]] = []
    pairwise: list[dict[str, Any]] = []
    for key, rows in sorted(exact_grouped.items()):
        if len({obs["agent_id"] for obs, _, _ in rows}) < 2:
            for obs, outcome, config in rows:
                mismatch_reasons: set[str] = set()
                candidates = [
                    candidate
                    for candidate in exact_eligible
                    if candidate[1].get("benchmark_id") == outcome.get("benchmark_id")
                    and candidate[1].get("task_id") == outcome.get("task_id")
                    and candidate[0].get("agent_id") != obs.get("agent_id")
                ]
                if not candidates:
                    mismatch_reasons.add("no_different_harness_peer")
                for other_obs, _, other_config in candidates:
                    if other_obs.get("model_id") != obs.get("model_id"):
                        mismatch_reasons.add("model_id_mismatch")
                    for field in _EXACT_MATCH_FIELDS:
                        if _frozen_match_value(other_config[field]) != _frozen_match_value(config[field]):
                            mismatch_reasons.add(f"{field}_mismatch")
                reject(
                    outcome,
                    stage="exact_peer_matching",
                    reason="no_exact_peer",
                    reasons=["no_exact_peer", *sorted(mismatch_reasons)],
                )
            continue
        benchmark_id, task_id, model_id = key[:3]
        config = rows[0][2]
        by_harness: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        for obs, outcome, _ in rows:
            harness_revision_id = f"{obs['agent_id']}@{obs['agent_version']}"
            by_harness.setdefault(harness_revision_id, []).append((obs, outcome))
        members = []
        for revision_id, evidence_rows in sorted(by_harness.items()):
            values = [int(outcome["outcome"]) for _, outcome in evidence_rows]
            first_obs = evidence_rows[0][0]
            members.append({
                "harness_id": first_obs["agent_id"],
                "harness_version": first_obs["agent_version"],
                "harness_revision_id": revision_id,
                "attempt_count": len(values),
                "mean_outcome": sum(values) / len(values),
                "outcomes": values,
                "evidence_refs": [_evidence_ref(obs, outcome) for obs, outcome in evidence_rows],
            })
        block_id = stable_record_id("b-block", *key)
        matched_blocks.append({
            "schema_version": "experience-evaluation-b-matched-block/v0.2",
            "block_id": block_id,
            "benchmark_id": benchmark_id,
            "task_id": task_id,
            "model_id": model_id,
            **config,
            "match_quality": "exact_full_gate",
            "members": members,
        })
        for left, right in combinations(members, 2):
            if left["harness_id"] == right["harness_id"]:
                continue
            delta = left["mean_outcome"] - right["mean_outcome"]
            pairwise.append({
                "schema_version": "experience-evaluation-b-pairwise/v0.2",
                "comparison_id": stable_record_id(
                    "b-comparison", block_id, left["harness_revision_id"], right["harness_revision_id"]
                ),
                "block_id": block_id,
                "benchmark_id": benchmark_id,
                "task_id": task_id,
                "model_id": model_id,
                "reasoning_effort": config["reasoning_effort"],
                "harness_a": left["harness_revision_id"],
                "harness_b": right["harness_revision_id"],
                "attempt_count_a": left["attempt_count"],
                "attempt_count_b": right["attempt_count"],
                "outcome_a": left["mean_outcome"],
                "outcome_b": right["mean_outcome"],
                "delta_a_minus_b": delta,
                "result": "a" if delta > 0 else "b" if delta < 0 else "tie",
            })

    aggregate_groups: dict[tuple[str, str, str, str, Optional[str]], list[dict[str, Any]]] = {}
    for row in pairwise:
        key = (
            row["benchmark_id"], row["model_id"], row["harness_a"], row["harness_b"], row["reasoning_effort"]
        )
        aggregate_groups.setdefault(key, []).append(row)
    aggregates = []
    for key, rows in sorted(aggregate_groups.items()):
        benchmark_id, model_id, harness_a, harness_b, effort = key
        aggregates.append({
            "schema_version": "experience-evaluation-b-pair-summary/v0.1",
            "summary_id": stable_record_id("b-summary", *key),
            "benchmark_id": benchmark_id,
            "model_id": model_id,
            "reasoning_effort": effort,
            "harness_a": harness_a,
            "harness_b": harness_b,
            "matched_task_count": len(rows),
            "exact_model_match_count": len(rows),
            "linked_only_count": 0,
            "a_wins": sum(r["result"] == "a" for r in rows),
            "b_wins": sum(r["result"] == "b" for r in rows),
            "ties": sum(r["result"] == "tie" for r in rows),
            "mean_delta_a_minus_b": sum(r["delta_a_minus_b"] for r in rows) / len(rows),
            "evidence_tier": "exact_full_gate",
            "environment_consistency": "exact",
        })
    return {
        "matched_blocks": matched_blocks,
        "provisional_blocks": provisional_blocks,
        "pairwise_comparisons": pairwise,
        "pair_summaries": aggregates,
        "rejected": rejected,
    }


def _validate_unique(records: Sequence[dict[str, Any]], key: str, label: str) -> None:
    seen: set[str] = set()
    for record in records:
        value = record.get(key)
        if not isinstance(value, str) or not value:
            raise DatasetError(f"{label} record requires {key}")
        if value in seen:
            raise DatasetError(f"duplicate {label} {key}: {value}")
        seen.add(value)


def _write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_dataset(
    *,
    catalog_path: Path,
    agent_psychometrics_sources: Sequence[dict[str, Any]],
    tb21_submission_dir: Optional[Path],
    output_dir: Path,
) -> dict[str, Any]:
    """Build normalized catalogs, observations, outcomes, manifest and coverage."""
    catalog = _read_json(catalog_path)
    models = list(catalog.get("models") or [])
    harnesses = list(catalog.get("harnesses") or [])
    _validate_unique(models, "model_id", "model")
    _validate_unique(harnesses, "harness_id", "harness")

    observations: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    source_files: list[dict[str, Any]] = []

    for source in agent_psychometrics_sources:
        path = Path(source["path"])
        source_id = str(source["source_id"])
        benchmark_id = str(source["benchmark_id"])
        pairs, expanded = parse_agent_psychometrics(
            path,
            source_id=source_id,
            benchmark_id=benchmark_id,
            identity_map=source.get("identity_map"),
        )
        observations.extend(pairs)
        outcomes.extend(expanded)
        source_ids.add(source_id)
        source_files.append(
            {
                "source_id": source_id,
                "path": str(path),
                "sha256": sha256_file(path),
                "record_count": len(pairs),
                "task_outcome_count": len(expanded),
            }
        )

    if tb21_submission_dir is not None:
        tb_source_id = "terminal-bench-2.1-official-submissions"
        for path in sorted(Path(tb21_submission_dir).glob("*.json")):
            observations.append(parse_tb21_submission(path, source_id=tb_source_id))
            source_files.append(
                {
                    "source_id": tb_source_id,
                    "path": str(path),
                    "sha256": sha256_file(path),
                    "record_count": 1,
                    "task_outcome_count": 0,
                }
            )
        source_ids.add(tb_source_id)

    _validate_unique(observations, "observation_id", "observation")
    _validate_unique(outcomes, "outcome_id", "outcome")
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "model_catalog.jsonl", sorted(models, key=lambda x: x["model_id"]))
    _write_jsonl(output_dir / "harness_catalog.jsonl", sorted(harnesses, key=lambda x: x["harness_id"]))
    _write_jsonl(
        output_dir / "agent_model_observations.jsonl",
        sorted(observations, key=lambda x: x["observation_id"]),
    )
    _write_jsonl(
        output_dir / "task_outcomes.jsonl",
        sorted(outcomes, key=lambda x: x["outcome_id"]),
    )

    known_model_ids = {record["model_id"] for record in models}
    known_harness_ids = {record["harness_id"] for record in harnesses}
    resolved_observations = [
        record for record in observations if record.get("identity_status") != "unresolved"
    ]
    resolved_observation_ids = {record["observation_id"] for record in resolved_observations}
    observed_model_ids = {record["model_id"] for record in resolved_observations}
    observed_harness_ids = {record["agent_id"] for record in resolved_observations}
    benchmark_counts: dict[str, int] = {}
    identity_status_counts: dict[str, int] = {}
    granularity_counts: dict[str, int] = {}
    for record in observations:
        benchmark = str(record["benchmark_id"])
        benchmark_counts[benchmark] = benchmark_counts.get(benchmark, 0) + 1
        status = str(record.get("identity_status") or "unknown")
        identity_status_counts[status] = identity_status_counts.get(status, 0) + 1
        granularity = str(record.get("evidence_granularity") or "unknown")
        granularity_counts[granularity] = granularity_counts.get(granularity, 0) + 1
    coverage = {
        "schema_version": "experience-evaluation-coverage/v0.1",
        "catalog_models": len(models),
        "catalog_harnesses": len(harnesses),
        "agent_model_observations": len(observations),
        "task_outcome_records": len(outcomes),
        "task_outcomes_with_resolved_identity": sum(
            1 for record in outcomes if record["observation_id"] in resolved_observation_ids
        ),
        "unresolved_identity_observations": identity_status_counts.get("unresolved", 0),
        "identity_status": dict(sorted(identity_status_counts.items())),
        "evidence_granularity": dict(sorted(granularity_counts.items())),
        "benchmarks": dict(sorted(benchmark_counts.items())),
        "catalog_models_with_observations": len(known_model_ids & observed_model_ids),
        "catalog_harnesses_with_observations": len(known_harness_ids & observed_harness_ids),
        "observed_models_not_in_catalog": sorted(observed_model_ids - known_model_ids),
        "observed_harnesses_not_in_catalog": sorted(observed_harness_ids - known_harness_ids),
        "catalog_models_without_observations": sorted(known_model_ids - observed_model_ids),
        "catalog_harnesses_without_observations": sorted(known_harness_ids - observed_harness_ids),
    }
    (output_dir / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "experience-evaluation-dataset-manifest/v0.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_ids": sorted(source_ids),
        "source_files": sorted(source_files, key=lambda x: (x["source_id"], x["path"])),
        "outputs": {
            "model_catalog": "model_catalog.jsonl",
            "harness_catalog": "harness_catalog.jsonl",
            "agent_model_observations": "agent_model_observations.jsonl",
            "task_outcomes": "task_outcomes.jsonl",
            "coverage": "coverage.json",
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"manifest": manifest, "coverage": coverage}
