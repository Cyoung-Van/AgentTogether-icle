"""icle core schemas (P0): the project's own protocol objects.

Validation discipline (proven in agent-eval-prototype): unknown enums and
unsafe path references are rejected; no silent normalization. These schemas
contain no credential fields by construction.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any


class SchemaError(ValueError):
    pass


def _require(data: Any, fields: list[str], schema: str) -> dict:
    if not isinstance(data, dict):
        raise SchemaError(f"{schema}: document must be an object")
    for field in fields:
        if field not in data:
            raise SchemaError(f"{schema}: missing required field {field!r}")
    return data


def _text(data: dict, field: str, schema: str, *, allow_empty: bool = False) -> str:
    value = data[field]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise SchemaError(f"{schema}: {field} must be a non-empty string")
    return value


def _enum(data: dict, field: str, allowed: tuple[str, ...], schema: str) -> str:
    value = _text(data, field, schema)
    if value not in allowed:
        raise SchemaError(
            f"{schema}: {field} must be one of {', '.join(allowed)}; got {value!r}"
        )
    return value


def _safe_ref(data: dict, field: str, schema: str) -> str:
    value = _text(data, field, schema)
    pure = PurePath(value)
    if pure.is_absolute() or ".." in pure.parts:
        raise SchemaError(f"{schema}: {field} must be a safe relative reference")
    return value


def _sha(data: dict, field: str, schema: str, *, nullable: bool = False) -> str | None:
    value = data[field]
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise SchemaError(f"{schema}: {field} must be a sha256: digest")
    return value


def _version(data: dict, expected: str, schema: str) -> None:
    if data.get("schema_version") != expected:
        raise SchemaError(
            f"{schema}: schema_version must be {expected!r}; got {data.get('schema_version')!r}"
        )


# ---------------------------------------------------------------- enums
EXECUTION_PROVIDERS = ("direct_cli", "provider_api", "deepseek_harness", "scripted", "future")
CONTEXT_MODES = (
    "FULL_HISTORY",
    "SELECTED_HISTORY",
    "SUMMARY",
    "PROJECT_STATE",
    "ARTIFACT_ONLY",
    "CLEAN",
)
EPISODE_OUTCOMES = ("accepted", "edited", "rejected", "redo", "abandoned", "unknown")
JUDGMENT_KINDS = ("prefer_a", "prefer_b", "tie", "inconclusive")
RESULT_MARKS = ("accept", "edit", "reject", "redo")
REASON_TAGS = (
    "correctness",
    "completeness",
    "style",
    "speed",
    "cost",
    "initiative",
    "understanding",
    "maintainability",
)
JUDGE_TYPES = ("human", "llm", "llm+human")
REPLAY_STATUSES = ("created", "restored", "running", "completed", "failed", "cancelled")
LEDGER_KINDS = (
    "episode",
    "replay_run",
    "judgment",
    "result_mark",
    "user_rating",
    "implicit_signal",
)
IMPLICIT_SIGNALS = (
    "adopted",
    "adopted_with_minor_edits",
    "adopted_with_major_edits",
    "requested_rework",
    "switched_agent",
    "abandoned_result",
)


# ---------------------------------------------------------------- objects


def validate_agent_revision(data: Any) -> dict:
    """icle-agent-revision/v0.1 — which exact agent configuration did the work."""
    schema = "icle-agent-revision/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        [
            "schema_version",
            "agent_id",
            "revision_id",
            "model",
            "cli",
            "provider",
            "persona_sha256",
            "memory_sha256",
            "tools_sha256",
            "execution_provider",
            "created_at",
        ],
        schema,
    )
    _text(data, "agent_id", schema)
    _text(data, "revision_id", schema)
    _text(data, "model", schema)
    _text(data, "cli", schema)
    _text(data, "provider", schema)
    _sha(data, "persona_sha256", schema, nullable=True)
    _sha(data, "memory_sha256", schema, nullable=True)
    _sha(data, "tools_sha256", schema, nullable=True)
    _enum(data, "execution_provider", EXECUTION_PROVIDERS, schema)
    _text(data, "created_at", schema)
    return data


def validate_task_episode(data: Any) -> dict:
    """icle-task-episode/v0.1 — one real thing a user once had an agent do."""
    schema = "icle-task-episode/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        [
            "schema_version",
            "episode_id",
            "project_id",
            "source_agent_revision",
            "source_session",
            "task_start",
            "created_at",
        ],
        schema,
    )
    _text(data, "episode_id", schema)
    _text(data, "project_id", schema)
    _text(data, "source_session", schema)
    _text(data, "created_at", schema)

    start = data["task_start"]
    if not isinstance(start, dict):
        raise SchemaError(f"{schema}: task_start must be an object")
    for field in ("original_user_request", "execution_provider"):
        if field not in start:
            raise SchemaError(f"{schema}: task_start missing {field!r}")
    _text(start, "original_user_request", schema)
    if start.get("execution_provider") not in EXECUTION_PROVIDERS:
        raise SchemaError(f"{schema}: task_start.execution_provider invalid")
    snapshot = start.get("project_snapshot", {})
    if not isinstance(snapshot, dict):
        raise SchemaError(f"{schema}: task_start.project_snapshot must be an object")
    if "git_revision" in snapshot and not isinstance(snapshot["git_revision"], str):
        raise SchemaError(f"{schema}: project_snapshot.git_revision must be a string")
    if "workspace_sha256" in snapshot:
        if not str(snapshot["workspace_sha256"]).startswith("sha256:"):
            raise SchemaError(f"{schema}: project_snapshot.workspace_sha256 invalid")

    validate_agent_revision(data["source_agent_revision"])
    for section in ("timeline", "artifacts"):
        if section in data and not isinstance(data[section], list):
            raise SchemaError(f"{schema}: {section} must be an array")
    if "outcome" in data:
        if data["outcome"] not in EPISODE_OUTCOMES:
            raise SchemaError(f"{schema}: outcome invalid")
    return data


def validate_replay_run(data: Any) -> dict:
    """icle-replay-run/v0.1 — re-executing an episode from T0 with another agent."""
    schema = "icle-replay-run/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        [
            "schema_version",
            "replay_id",
            "episode_id",
            "target_agent_revision",
            "context_bundle",
            "status",
            "created_at",
        ],
        schema,
    )
    _text(data, "replay_id", schema)
    _text(data, "episode_id", schema)
    validate_agent_revision(data["target_agent_revision"])
    bundle = data["context_bundle"]
    if not isinstance(bundle, dict) or bundle.get("mode") not in CONTEXT_MODES:
        raise SchemaError(f"{schema}: context_bundle.mode invalid")
    if "sha256" in bundle and not str(bundle["sha256"]).startswith("sha256:"):
        raise SchemaError(f"{schema}: context_bundle.sha256 invalid")
    _enum(data, "status", REPLAY_STATUSES, schema)
    if "result_ref" in data:
        _safe_ref(data, "result_ref", schema)
    _text(data, "created_at", schema)
    return data


def validate_judgment(data: Any) -> dict:
    """icle-judgment/v0.1 — user/LLM evaluation evidence (never a hidden override)."""
    schema = "icle-judgment/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        [
            "schema_version",
            "judgment_id",
            "subject",
            "kind",
            "judge",
            "reason_tags",
            "created_at",
        ],
        schema,
    )
    _text(data, "judgment_id", schema)
    subject = data["subject"]
    if not isinstance(subject, dict):
        raise SchemaError(f"{schema}: subject must be an object")
    if subject.get("type") not in {"episode", "replay_pair"}:
        raise SchemaError(f"{schema}: subject.type must be episode or replay_pair")
    for key in ("a", "b") if subject["type"] == "replay_pair" else ("id",):
        if not isinstance(subject.get(key), str) or not subject[key]:
            raise SchemaError(f"{schema}: subject.{key} must be a non-empty string")
    _enum(data, "kind", JUDGMENT_KINDS, schema)
    _enum(data, "judge", JUDGE_TYPES, schema)
    tags = data["reason_tags"]
    if not isinstance(tags, list) or not all(tag in REASON_TAGS for tag in tags):
        raise SchemaError(f"{schema}: reason_tags must be known tags")
    if data["judge"] != "human":
        for field in ("judge_model", "judge_revision", "prompt_sha256", "input_sha256"):
            if field not in data:
                raise SchemaError(f"{schema}: LLM judgment requires {field}")
        if not str(data.get("prompt_sha256", "")).startswith("sha256:"):
            raise SchemaError(f"{schema}: prompt_sha256 invalid")
        if not str(data.get("input_sha256", "")).startswith("sha256:"):
            raise SchemaError(f"{schema}: input_sha256 invalid")
    _text(data, "created_at", schema)
    return data


def validate_experience_record(data: Any) -> dict:
    """icle-experience-record/v0.1 — one append-only ledger event."""
    schema = "icle-experience-record/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        [
            "schema_version",
            "record_id",
            "kind",
            "agent_revision",
            "payload_sha256",
            "created_at",
        ],
        schema,
    )
    _text(data, "record_id", schema)
    _enum(data, "kind", LEDGER_KINDS, schema)
    validate_agent_revision(data["agent_revision"])
    _sha(data, "payload_sha256", schema)
    if "episode_id" in data and not isinstance(data["episode_id"], str):
        raise SchemaError(f"{schema}: episode_id must be a string")
    if "content_ref" in data:
        _safe_ref(data, "content_ref", schema)
    _text(data, "created_at", schema)
    return data


def validate_execution_provider(data: Any) -> dict:
    """icle-execution-provider/v0.1 — pluggable execution backend descriptor."""
    schema = "icle-execution-provider/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        ["schema_version", "name", "version", "capabilities", "experimental"],
        schema,
    )
    _enum(data, "name", EXECUTION_PROVIDERS, schema)
    _text(data, "version", schema)
    caps = data["capabilities"]
    if not isinstance(caps, dict) or not all(
        isinstance(key, str) and isinstance(value, bool) for key, value in caps.items()
    ):
        raise SchemaError(f"{schema}: capabilities must map names to booleans")
    if not isinstance(data["experimental"], bool):
        raise SchemaError(f"{schema}: experimental must be boolean")
    return data


def validate_context_bundle(data: Any) -> dict:
    """icle-context-bundle/v0.1 — what the replay agent is allowed to see."""
    schema = "icle-context-bundle/v0.1"
    _version(data, schema, schema)
    _require(
        data,
        ["schema_version", "mode", "refs", "sha256", "created_at"],
        schema,
    )
    _enum(data, "mode", CONTEXT_MODES, schema)
    refs = data["refs"]
    if not isinstance(refs, list):
        raise SchemaError(f"{schema}: refs must be an array")
    for ref in refs:
        pure = PurePath(ref) if isinstance(ref, str) else PurePath("")
        if pure.is_absolute() or ".." in pure.parts:
            raise SchemaError(f"{schema}: refs contain an unsafe reference")
    _sha(data, "sha256", schema)
    _text(data, "created_at", schema)
    return data
