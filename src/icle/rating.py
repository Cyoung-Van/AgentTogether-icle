"""AgentRating (UI-18): manual 5-dimension agent evaluation → Experience Ledger.

Design (WebUI plan §16-18): a structured rating is NOT a capability score.
User ratings live in ratings/ as content files and are hash-chained into the
ledger (kind='user_rating') as evidence. Router MAY reference them; capability
evidence is never mutated by user preference ('user liked it' ≠ '+10 coding').

`task_id` is a Batch B concept (Task Core); until then ratings attach to the
episode the work came from. The schema keeps the same structure for LLM judges
(UI-30), distinguished by `source`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import ExperienceLedger
from .schema import validate_agent_revision

RATING_DIMENSIONS = (
    "requirement_fit",
    "correctness",
    "efficiency",
    "autonomy",
    "maintainability",
)
RATING_SOURCES = ("user", "llm")
WOULD_USE_AGAIN = ("yes", "maybe", "no")
SCHEMA = "icle-agent-rating/v0.1"


class RatingError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _revision_of(agent_id: str) -> dict[str, Any]:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent_id,
        "revision_id": "manual-rating",
        "model": "unknown",
        "cli": "unknown",
        "provider": "unknown",
        "persona_sha256": None,
        "memory_sha256": None,
        "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": _now(),
    }


def validate_rating(data: Any) -> dict:
    """Validate one rating document (unknown enums and out-of-range scores rejected)."""
    if not isinstance(data, dict):
        raise RatingError(f"{SCHEMA}: document must be an object")
    if data.get("schema_version") != SCHEMA:
        raise RatingError(f"{SCHEMA}: schema_version must be {SCHEMA!r}")
    for field in ("rating_id", "source", "agent_id", "episode_id", "created_at"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise RatingError(f"{SCHEMA}: {field} must be a non-empty string")
    if data["source"] not in RATING_SOURCES:
        raise RatingError(f"{SCHEMA}: source must be one of {', '.join(RATING_SOURCES)}")
    dimensions = data.get("dimensions", {})
    if not isinstance(dimensions, dict):
        raise RatingError(f"{SCHEMA}: dimensions must be an object")
    for dim in RATING_DIMENSIONS:
        value = dimensions.get(dim)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise RatingError(f"{SCHEMA}: dimensions.{dim} must be an integer 1..5")
    overall = data.get("overall_preference")
    if not isinstance(overall, int) or isinstance(overall, bool) or not 1 <= overall <= 5:
        raise RatingError(f"{SCHEMA}: overall_preference must be an integer 1..5")
    if data.get("would_use_again") not in WOULD_USE_AGAIN:
        raise RatingError(f"{SCHEMA}: would_use_again must be one of {', '.join(WOULD_USE_AGAIN)}")
    # M8: task_id 可选 —— 任务 accept 前 judge 时 episode_id 是占位引用,
    # task_id 提供真实的来源锚点,避免悬挂引用无从追溯。
    task_id = data.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        raise RatingError(f"{SCHEMA}: task_id must be a non-empty string or null")
    comment = data.get("comment", "")
    if not isinstance(comment, str):
        raise RatingError(f"{SCHEMA}: comment must be a string")
    if "agent_revision" in data:
        validate_agent_revision(data["agent_revision"])
    # LLM ratings must carry full provenance (same discipline as LLM judgments)
    if data["source"] == "llm":
        provenance = data.get("provenance")
        if not isinstance(provenance, dict):
            raise RatingError(f"{SCHEMA}: llm rating requires provenance")
        for field in ("provider", "model", "revision", "prompt_sha256"):
            if not isinstance(provenance.get(field), str) or not provenance[field]:
                raise RatingError(f"{SCHEMA}: llm rating provenance requires {field}")
        if not provenance["prompt_sha256"].startswith("sha256:"):
            raise RatingError(f"{SCHEMA}: llm rating prompt_sha256 must be a sha256: digest")
    return data


def record_rating(
    store: str | Path,
    *,
    agent_id: str,
    episode_id: str,
    dimensions: dict[str, int],
    overall_preference: int,
    would_use_again: str,
    comment: str = "",
    source: str = "user",
    agent_revision: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Record one rating: content file + ledger chain (append-only).

    LLM ratings (UI-30) MUST carry provenance; the API layer forwards it from
    the intelligence call so every rating is audit-ready. task_id (M8) anchors
    ratings created before the task was accepted (episode_id is a placeholder
    then); both fields are preserved so the reference never dangles silently.
    """
    document: dict[str, Any] = {
        "schema_version": SCHEMA,
        "rating_id": "r-" + uuid.uuid4().hex[:12],
        "source": source,
        "agent_id": agent_id,
        "episode_id": episode_id,
        "agent_revision": agent_revision or _revision_of(agent_id),
        "dimensions": {dim: dimensions[dim] for dim in RATING_DIMENSIONS},
        "overall_preference": overall_preference,
        "would_use_again": would_use_again,
        "comment": comment,
        "created_at": _now(),
    }
    if task_id is not None:
        document["task_id"] = task_id
    if provenance is not None:
        document["provenance"] = provenance
    validate_rating(document)
    store = Path(store)
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    ledger = ExperienceLedger(store / "ledger")
    record = ExperienceLedger.new_record(
        record_id=document["rating_id"],
        kind="user_rating",
        agent_revision=document["agent_revision"],
        payload=text,
        episode_id=episode_id,
    )
    entry = ledger.append_with_content(
        record,
        content_path=store / "ratings" / f"{document['rating_id']}.json",
        content=text,
    )
    return {"rating": document, "ledger_seq": entry["seq"]}


def list_ratings(store: str | Path, *, agent_id: str | None = None) -> list[dict[str, Any]]:
    store = Path(store)
    directory = store / "ratings"
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("r-*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if agent_id is None or doc.get("agent_id") == agent_id:
            out.append(doc)
    return out


def rating_summary(store: str | Path, agent_id: str) -> dict[str, Any]:
    """Per-dimension mean + count for an agent (evidence only, not capability)."""
    ratings = [r for r in list_ratings(store, agent_id=agent_id) if r.get("source") == "user"]
    if not ratings:
        return {"agent_id": agent_id, "count": 0, "dimensions": None, "overall_preference": None}
    dimension_totals = {dim: 0 for dim in RATING_DIMENSIONS}
    overall_total = 0
    for rating in ratings:
        for dim in RATING_DIMENSIONS:
            dimension_totals[dim] += rating["dimensions"].get(dim, 0)
        overall_total += rating.get("overall_preference", 0)
    return {
        "agent_id": agent_id,
        "count": len(ratings),
        "dimensions": {
            dim: round(dimension_totals[dim] / len(ratings), 2) for dim in RATING_DIMENSIONS
        },
        "overall_preference": round(overall_total / len(ratings), 2),
    }
