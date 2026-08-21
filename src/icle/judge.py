"""Human Evaluation → Experience Ledger (P4) + rule-based Router (light).

Judgments and marks are stored as content files (judgments/, marks/) AND
hash-chained into the ExperienceLedger (payload_sha256 links them). Routing
reads the content files; the ledger proves they existed, unchanged.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import ExperienceLedger
from .schema import validate_judgment


class JudgeError(ValueError):
    pass


RESULT_MARKS = {"accept", "edit", "reject", "redo"}
IMPLICIT_SIGNALS = {
    "adopted",
    "adopted_with_minor_edits",
    "adopted_with_major_edits",
    "requested_rework",
    "switched_agent",
    "abandoned_result",
}
POSITIVE_MARKS = {"accept", "adopted", "adopted_with_minor_edits"}
NEGATIVE_MARKS = {"reject", "redo", "abandoned_result", "requested_rework"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _revision_of(agent_id: str) -> dict[str, Any]:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent_id,
        "revision_id": "light-router",
        "model": "unknown",
        "cli": "unknown",
        "provider": "unknown",
        "persona_sha256": None,
        "memory_sha256": None,
        "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": _now(),
    }


def record_judgment(
    store: str | Path,
    *,
    subject: dict[str, Any],
    kind: str,
    reason_tags: list[str],
    episode_id: str | None = None,
    agent_revision: dict[str, Any] | None = None,
    note: str = "",
    judge: str = "human",
    judge_model: str | None = None,
    judge_revision: str | None = None,
    prompt_sha256: str | None = None,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    """Record a pairwise/episode judgment: content file + ledger chain.

    Human judgments never need provenance; LLM judgments MUST carry model,
    revision, prompt and input hashes (schema-enforced).
    """
    store = Path(store)
    judgment = {
        "schema_version": "icle-judgment/v0.1",
        "judgment_id": "j-" + uuid.uuid4().hex[:12],
        "subject": subject,
        "kind": kind,
        "judge": judge,
        "reason_tags": reason_tags,
        "note": note,
        "created_at": _now(),
    }
    if judge != "human":
        judgment.update(
            {
                "judge_model": judge_model,
                "judge_revision": judge_revision,
                "prompt_sha256": prompt_sha256,
                "input_sha256": input_sha256,
            }
        )
    validate_judgment(judgment)
    text = json.dumps(judgment, ensure_ascii=False, indent=2) + "\n"
    ledger = ExperienceLedger(store / "ledger")
    revision = agent_revision or _revision_of("human")
    record = ExperienceLedger.new_record(
        record_id=judgment["judgment_id"],
        kind="judgment",
        agent_revision=revision,
        payload=text,
        episode_id=episode_id or subject.get("id") or subject.get("a"),
    )
    entry = ledger.append_with_content(
        record,
        content_path=store / "judgments" / f"{judgment['judgment_id']}.json",
        content=text,
    )
    return {"judgment": judgment, "ledger_seq": entry["seq"]}


def record_result_mark(
    store: str | Path,
    *,
    episode_id: str,
    mark: str,
    agent_id: str,
    note: str = "",
) -> dict[str, Any]:
    """Record accept/edit/reject/redo or an implicit behavioral signal."""
    if mark not in RESULT_MARKS and mark not in IMPLICIT_SIGNALS:
        raise JudgeError(f"unknown result mark: {mark}")
    store = Path(store)
    document = {
        "schema_version": "icle-result-mark/v0.1",
        "mark_id": "m-" + uuid.uuid4().hex[:12],
        "episode_id": episode_id,
        "agent_id": agent_id,
        "mark": mark,
        "note": note,
        "created_at": _now(),
    }
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    ledger = ExperienceLedger(store / "ledger")
    kind = "result_mark" if mark in RESULT_MARKS else "implicit_signal"
    record = ExperienceLedger.new_record(
        record_id=document["mark_id"],
        kind=kind,
        agent_revision=_revision_of(agent_id),
        payload=text,
        episode_id=episode_id,
    )
    entry = ledger.append_with_content(
        record,
        content_path=store / "marks" / f"{document['mark_id']}.json",
        content=text,
    )
    return {"mark": mark, "ledger_seq": entry["seq"]}


# ------------------------------------------------------------- light router


def _replay_agent(store: Path, replay_id: str) -> str | None:
    from .replay import assert_replay_id  # S2: 防穿越

    try:
        assert_replay_id(replay_id)
    except Exception:
        return None
    path = store / "replays" / replay_id / "replay.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))["target_agent_revision"]["agent_id"]


def route_suggest(
    store: str | Path,
    *,
    project_id: str | None = None,
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic evidence-based suggestion (NOT learned scheduling).

    per agent: adopt_rate = positive/(positive+negative);
    win_rate = pairwise wins/(wins+losses);
    score = 0.6*adopt_rate + 0.4*win_rate (missing parts default to 0.5 with
    'insufficient-data' flag); agents with zero evidence sort last.
    """
    store = Path(store)
    per_agent: dict[str, dict[str, Any]] = {}

    def slot(agent: str) -> dict[str, Any]:
        return per_agent.setdefault(
            agent, {"positive": 0, "negative": 0, "wins": 0, "losses": 0}
        )

    for path in sorted((store / "marks").glob("m-*.json")) if (store / "marks").is_dir() else []:
        doc = json.loads(path.read_text(encoding="utf-8"))
        entry = slot(doc["agent_id"])
        if doc["mark"] in POSITIVE_MARKS:
            entry["positive"] += 1
        elif doc["mark"] in NEGATIVE_MARKS:
            entry["negative"] += 1
        # 'edit' and rework-adjacent marks count as neither by design
    if (store / "judgments").is_dir():
        for path in sorted((store / "judgments").glob("j-*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            subject = doc["subject"]
            if subject.get("type") != "replay_pair" or doc["kind"] not in {"prefer_a", "prefer_b"}:
                continue
            winner_id = subject["a"] if doc["kind"] == "prefer_a" else subject["b"]
            loser_id = subject["b"] if doc["kind"] == "prefer_a" else subject["a"]
            winner = _replay_agent(store, winner_id)
            loser = _replay_agent(store, loser_id)
            if winner:
                slot(winner)["wins"] += 1
            if loser:
                slot(loser)["losses"] += 1

    if candidates:
        for agent in candidates:
            slot(agent)

    ranking = []
    for agent, entry in per_agent.items():
        outcomes = entry["positive"] + entry["negative"]
        pairs = entry["wins"] + entry["losses"]
        adopt_rate = entry["positive"] / outcomes if outcomes else None
        win_rate = entry["wins"] / pairs if pairs else None
        flags = []
        if adopt_rate is None:
            flags.append("no-outcome-data")
        if win_rate is None:
            flags.append("no-pairwise-data")
        score = None
        if outcomes or pairs:
            score = round(
                0.6 * (adopt_rate if adopt_rate is not None else 0.5)
                + 0.4 * (win_rate if win_rate is not None else 0.5),
                3,
            )
        ranking.append(
            {
                "agent": agent,
                "score": score,
                "adopt_rate": adopt_rate,
                "win_rate": win_rate,
                "outcomes": outcomes,
                "pairwise": pairs,
                "flags": flags or ["measured"],
            }
        )
    ranking.sort(key=lambda item: (item["score"] is None, -(item["score"] or 0), item["agent"]))
    return {
        "schema_version": "icle-agent-shell-preference/v0.1",
        "kind": "agent_shell_preference",
        "policy": "agent-shell-preference-v0.1: 0.6*adopt_rate + 0.4*win_rate; not capability",
        "project_id": project_id,
        "preferences": ranking,
        "ranking": ranking,
    }
