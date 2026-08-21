"""Active Replay (P7): replay budget + information-value recommendations.

The system should only spend agent execution cost on the MOST informative
comparisons. Budget is a hard cap enforced outside the LLM/recommendation
layer; suggestions explain exactly which evidence gap a replay would close.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .adapters import resolve_binary
from .localtime import local_day_key
from .replay import DIRECT_CLI_AGENTS, list_replays

DEFAULT_DAILY_BUDGET = 5


class BudgetError(ValueError):
    pass


def _today() -> str:
    return local_day_key()


def _budget_path(store: str | Path) -> Path:
    return Path(store) / "replay_budget.json"


def _lock_budget(store: Path):
    """M2: 预算读写用独立 lock 文件的排他锁串行化,堵住 TOCTOU 竞态。

    锁文件与数据文件分离 —— _atomic_write 用 os.replace 替换数据文件
    inode,若锁直接开在数据文件上会因 replace 而失效。
    """
    lock_path = Path(store) / "replay_budget.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle
    except Exception:
        handle.close()
        raise


def get_budget(store: str | Path) -> dict[str, Any]:
    path = _budget_path(store)
    if not path.is_file():
        return {"per_day": DEFAULT_DAILY_BUDGET, "used": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def set_budget(store: str | Path, per_day: int) -> dict[str, Any]:
    if isinstance(per_day, bool) or not isinstance(per_day, int) or per_day < 1:
        raise BudgetError("per_day must be a positive integer")
    store = Path(store)
    handle = _lock_budget(store)
    try:
        budget = get_budget(store)
        budget["per_day"] = per_day
        _atomic_write(_budget_path(store), json.dumps(budget, indent=2) + "\n")
        return budget
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def remaining_budget(store: str | Path) -> int:
    budget = get_budget(store)
    used = budget["used"].get(_today(), 0)
    return max(0, budget["per_day"] - used)


def consume_budget(store: str | Path, *, reason: str) -> dict[str, Any]:
    """Hard-cap enforcement: refuses when the daily budget is exhausted.

    M2: 读-改-写在文件锁内完成,并发请求不再同时通过剩余检查。
    """
    store = Path(store)
    handle = _lock_budget(store)
    try:
        remaining = remaining_budget(store)
        if remaining <= 0:
            raise BudgetError(
                f"daily replay budget exhausted ({get_budget(store)['per_day']}/day); "
                "increase it explicitly or wait"
            )
        budget = get_budget(store)
        budget["used"][_today()] = budget["used"].get(_today(), 0) + 1
        _atomic_write(_budget_path(store), json.dumps(budget, indent=2) + "\n")
        return {"remaining": remaining - 1, "reason": reason}
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


# ------------------------------------------------------------- suggestions


def suggest_replays(
    store: str | Path, *, limit: int = 5, candidates: list[str] | None = None
) -> dict[str, Any]:
    """Rank (episode, agent) pairs by information value.

    Value drivers (deterministic):
    - episode already has >=1 replay (a second agent enables comparison): +0.5
    - episode has user marks (outcome known for source agent): +0.3
    - agent has NO replay on this episode yet: +0.2
    - pairwise data missing between the two agents on this episode: +0.2
    Suggestions never exceed the remaining daily budget.
    """
    store = Path(store)
    remaining = remaining_budget(store)
    episodes_dir = store / "episodes"
    if not episodes_dir.is_dir():
        return {"remaining_budget": remaining, "suggestions": []}
    episodes = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(episodes_dir.glob("ep-*.json"))
    ]
    replays = list_replays(store)
    marks_dir = store / "marks"
    marks = (
        [json.loads(p.read_text(encoding="utf-8")) for p in sorted(marks_dir.glob("m-*.json"))]
        if marks_dir.is_dir()
        else []
    )
    known_agents = sorted({r["agent"] for r in replays} | {m["agent_id"] for m in marks})
    installed = {name for name in DIRECT_CLI_AGENTS if resolve_binary(name)}
    # candidates = agents with evidence or an actually-resolvable oneshot CLI
    pool = candidates or sorted(set(known_agents) | installed)
    if len(pool) < 2:
        return {
            "remaining_budget": remaining,
            "suggestions": [],
            "note": "need >=2 candidate agents before comparisons make sense",
        }

    suggestions = []
    for episode in episodes:
        episode_replays = [r for r in replays if r["episode_id"] == episode["episode_id"]]
        replayed_agents = {r["agent"] for r in episode_replays}
        episode_marks = {m["agent_id"] for m in marks if m["episode_id"] == episode["episode_id"]}
        source_agent = episode["source_agent_revision"]["agent_id"]
        candidates_for_episode = set(pool) | {source_agent}
        for agent in sorted(candidates_for_episode - replayed_agents):
            value = 0.0
            reasons = []
            if episode_replays:
                value += 0.5
                reasons.append("comparison partner exists")
            if episode_marks:
                value += 0.3
                reasons.append("source outcome known")
            value += 0.2
            reasons.append(f"{agent} has no replay on this episode")
            if len(replayed_agents | {agent}) >= 2:
                value += 0.2
                reasons.append("pairwise data missing")
            suggestions.append(
                {
                    "episode_id": episode["episode_id"],
                    "agent": agent,
                    "value": round(value, 2),
                    "reason": "; ".join(reasons),
                    "request": episode["task_start"]["original_user_request"][:80],
                }
            )
    suggestions.sort(key=lambda item: (-item["value"], item["episode_id"], item["agent"]))
    return {
        "schema_version": "icle-replay-suggestion/v0.1",
        "remaining_budget": remaining,
        "suggestions": suggestions[: min(limit, remaining)] if remaining else [],
        "budget_note": "suggestions are capped by the remaining daily replay budget",
    }
