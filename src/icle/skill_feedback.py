"""Skill 自我评估(§30,v0.4):记录用户对 LLM 产物的 accept/edit/reject 行为。

数据落 experience-store/skill-feedback.jsonl(append-only,每行一个事件):
  {ts, skill, mode, action, session_id, candidate_id, detail}

统计口径(获取真实接受率,指导 skill 迭代):
  analyze-session: action ∈ accept|edit|reject(+boundary_edit/task_merge/task_split 预留)
  plan-task:       action ∈ accept|edit|reject|strategy_change
  replan-task:     action ∈ apply|reject

接受率 = accept / (accept + edit + reject);edit 计为「部分接受」单独统计。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_DIR = "skill-feedback.jsonl"
ACTIONS = {"accept", "edit", "reject", "boundary_edit", "task_merge", "task_split",
           "strategy_change", "apply", "plan_edit", "plan_delete", "plan_add", "replan_needed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillFeedbackError(ValueError):
    pass


def record_feedback(store: str | Path, *, skill: str, mode: str, action: str,
                    session_id: str = "", candidate_id: str = "", detail: str = "") -> dict:
    """append-only 记录一次用户行为。"""
    if action not in ACTIONS:
        raise SkillFeedbackError(f"unknown feedback action: {action}")
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    event = {
        "schema_version": "icle-skill-feedback/v0.1",
        "ts": _now(),
        "skill": skill,
        "mode": mode,
        "action": action,
        "session_id": session_id,
        "candidate_id": candidate_id,
        "detail": detail[:500],
    }
    with (store / FEEDBACK_DIR).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def feedback_stats(store: str | Path) -> dict:
    """按 skill/mode 统计 action 分布与接受率(§30:Intelligence Skill Evaluation)。"""
    path = Path(store) / FEEDBACK_DIR
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (event.get("skill", "?"), event.get("mode", "?"))
            bucket = buckets.setdefault(key, {})
            bucket[event.get("action", "?")] = bucket.get(event.get("action", "?"), 0) + 1

    stats = []
    for (skill, mode), counts in sorted(buckets.items()):
        accept = counts.get("accept", 0)
        edit = counts.get("edit", 0)
        reject = counts.get("reject", 0)
        total = accept + edit + reject
        stats.append({
            "skill": skill, "mode": mode,
            "accept": accept, "edit": edit, "reject": reject,
            "total": total,
            "accept_rate": round(accept / total, 3) if total else None,
            "edited_rate": round((accept + edit) / total, 3) if total else None,
        })
    return {"schema_version": "icle-skill-stats/v0.1", "stats": stats}
