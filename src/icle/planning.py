"""PlanningEngine (v0.4 P12 / Batch 4): task analysis & planning v0.2.

计划书 §30-35:
- 不要只输出一个 Plan: 生成 Route Candidates(A/B/C 多方案,§30)
- Task Ledger(§31, 借鉴 Magentic-One 但简化): goal/known_facts/unknowns/
  constraints/assumptions/required_outputs/risk/completion_contract
- 拆分规则(§35): D1/D2→DIRECT, D3→DIRECT/PLAN_FIRST, D4→PLAN_FIRST/DECOMPOSE,
  D5→强制 PLAN_FIRST;LLM 只作建议
- PlanStep v0.2(§32): alternatives[]/estimated_cost/estimated_time/replan_on_failure
- Progress Ledger(§33): completed_steps/current_step/failures/blocked_by/
  remaining_plan/replan_reason;只在 step failed/verification failed/budget
  exceeded/agent unavailable 时触发 replan(不每一步自我反思)

Route Candidates 的成本估计复用 cost.plan_cost_quote;确定性规则,无 LLM。
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cost import plan_cost_quote

DIFFICULTIES = ("D1", "D2", "D3", "D4", "D5")
STRATEGIES = ("DIRECT", "PLAN_FIRST", "DECOMPOSE", "AUTHOR_REVIEWER", "PARALLEL_COMPARE", "HANDOFF")


class PlanningError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


# ---------------------------------------------------------------- strategy rules (§35)


def suggest_strategy(difficulty: str) -> list[str]:
    """确定性策略建议(§35);LLM 只作建议,规则优先。"""
    if difficulty in ("D1", "D2"):
        return ["DIRECT"]
    if difficulty == "D3":
        return ["DIRECT", "PLAN_FIRST"]
    if difficulty == "D4":
        return ["PLAN_FIRST", "DECOMPOSE"]
    if difficulty == "D5":
        return ["PLAN_FIRST"]  # 强制先计划
    return ["PLAN_FIRST"]


# ---------------------------------------------------------------- Task Ledger (§31)


def build_task_ledger(
    *,
    title: str,
    description: str,
    risk: str = "R1",
    known_facts: list[str] | None = None,
    unknowns: list[str] | None = None,
    constraints: list[str] | None = None,
    required_outputs: list[str] | None = None,
) -> dict[str, Any]:
    """Task Ledger(§31): 拆任务前先回答"要完成什么/已知什么/未知什么/约束/
    怎样算完成"。字段可手动填;缺省从描述抽取(尽力而为)。"""
    goal = description or title
    return {
        "schema_version": "icle-task-ledger/v0.1",
        "ledger_id": "tl-" + uuid.uuid4().hex[:8],
        "goal": goal,
        "known_facts": known_facts or _guess_known(description),
        "unknowns": unknowns or [],
        "constraints": constraints or [],
        "assumptions": [],
        "required_outputs": required_outputs or ["completion artifact"],
        "risk": risk,
        "completion_contract": "user accepts the result",
        "created_at": _now(),
    }


def _guess_known(description: str) -> list[str]:
    """从描述提取显而易见的已知事实(MVP 简单启发式)。"""
    facts = []
    lowered = description.lower()
    if any(word in lowered for word in ("refactor", "重构", "rewrite")):
        facts.append("existing code must keep working")
    if any(word in lowered for word in ("router", "module", "component", "模块")):
        facts.append("a specific module/component is involved")
    if any(word in lowered for word in ("test", "测试", "verify", "验证")):
        facts.append("verification is required")
    return facts


# ---------------------------------------------------------------- Route Candidates (§30)


def plan_candidates(
    store: str | Path,
    *,
    title: str,
    description: str,
    difficulty: str = "D3",
    risk: str = "R1",
    agents: list[str] | None = None,
) -> dict[str, Any]:
    """Generate 2-3 Route Candidates(§30) + per-candidate cost quote.

    agents: 可用 agent 标识(provider_id/model_id 或 CLI agent 名),按偏好排序。
    规则生成(确定性,无 LLM):
      A Fast:     首选 agent DIRECT(便宜/快)
      B Balanced: 首选 agent PLAN_FIRST
      C Assurance: DECOMPOSE 三步(分析→实现→评审),高风险/高难度才给
    """
    store = Path(store)
    if not agents:
        raise ValueError("at least one executable agent is required")
    lead = agents[0]
    candidates: list[dict[str, Any]] = []
    quote_steps: list[dict[str, Any]] = []

    # A: Fast / Direct
    step_a = {
        "title": "Execute task",
        "description": description or title,
        "provider": lead.split("/")[0] if "/" in lead else "",
        "model": lead.split("/")[1] if "/" in lead else lead,
        "estimated_input": 3000,
        "estimated_output": 1500,
    }
    candidates.append({
        "id": "A", "label": "Fast", "strategy": "DIRECT",
        "agents": [lead], "steps": [step_a],
        "cost_quote": plan_cost_quote(store, steps=[step_a]),
        "description": "Single direct execution with the first available agent",
    })
    quote_steps.append(step_a)

    # B: Balanced / Plan First
    step_b = {
        "title": "Plan and execute",
        "description": f"Plan the work, then complete: {description or title}",
        "provider": lead.split("/")[0] if "/" in lead else "",
        "model": lead.split("/")[1] if "/" in lead else lead,
        "estimated_input": 5000,
        "estimated_output": 3000,
    }
    candidates.append({
        "id": "B", "label": "Balanced", "strategy": "PLAN_FIRST",
        "agents": [lead], "steps": [step_b],
        "cost_quote": plan_cost_quote(store, steps=[step_b]),
        "description": "Plan first, then execute with the same agent",
    })
    quote_steps.append(step_b)

    # C: Assurance / Decompose (难度 D4+ 或风险 R2+ 才推荐)
    if difficulty in ("D4", "D5") or risk in ("R2", "R3"):
        second = agents[1] if len(agents) > 1 else lead
        third = agents[2] if len(agents) > 2 else second
        quote_c = [
            {"provider": lead.split("/")[0] if "/" in lead else "", "model": lead.split("/")[1] if "/" in lead else lead,
             "estimated_input": 3000, "estimated_output": 1200},
            {"provider": second.split("/")[0] if "/" in second else "", "model": second.split("/")[1] if "/" in second else second,
             "estimated_input": 5000, "estimated_output": 4000},
            {"provider": third.split("/")[0] if "/" in third else "", "model": third.split("/")[1] if "/" in third else third,
             "estimated_input": 2000, "estimated_output": 1000},
        ]
        candidates.append({
            "id": "C", "label": "Assurance", "strategy": "DECOMPOSE",
            "agents": [lead, second, third],
            "cost_quote": plan_cost_quote(store, steps=quote_c),
            "steps": [
                {"step_id": "S1", "title": "Analyze", "description": f"Analyze requirements and constraints for: {description or title}"},
                {"step_id": "S2", "title": "Implement", "description": f"Implement the requested result: {description or title}"},
                {"step_id": "S3", "title": "Review", "description": "Review the result against the task requirements and verification criteria"},
            ],
            "description": "Analyze → Implement → Review across agents for higher assurance",
        })
        quote_steps.extend(quote_c)

    # 每候选的成本报价
    quote = plan_cost_quote(store, steps=quote_steps)
    return {
        "schema_version": "icle-plan-candidates/v0.1",
        "task": {"title": title, "difficulty": difficulty, "risk": risk},
        "candidates": candidates,
        "cost_quote": quote,
        "suggested_strategies": suggest_strategy(difficulty),
        "created_at": _now(),
    }


# ---------------------------------------------------------------- Progress Ledger (§33)


def progress_ledger_from_run(run: dict[str, Any]) -> dict[str, Any]:
    """Build a ProgressLedger from a TaskRun(§33)。只记录事实,不自我反思。

    replan_reason 仅在 step failed / verification failed / budget exceeded /
    agent unavailable 时出现(§33 触发条件)。
    """
    steps = run.get("steps", [])
    completed = [s["step_id"] for s in steps if s.get("status") == "completed"]
    failed = [s["step_id"] for s in steps if s.get("status") == "failed"]
    current = None
    for s in steps:
        if s.get("status") == "running":
            current = s["step_id"]
    remaining = [s["step_id"] for s in steps if s.get("status") not in ("completed", "failed")]
    replan_reasons = []
    for s in failed:
        replan_reasons.append(f"step {s} failed")
    return {
        "schema_version": "icle-progress-ledger/v0.1",
        "completed_steps": completed,
        "current_step": current,
        "failed_steps": failed,
        "blocked_by": failed or None,
        "remaining_plan": remaining,
        "replan_reason": "; ".join(replan_reasons) if replan_reasons else None,
        "updated_at": _now(),
    }


# ---------------------------------------------------------------- persistence


def save_ledger(store: str | Path, ledger: dict[str, Any]) -> dict[str, Any]:
    store = Path(store)
    _atomic_write(
        store / "ledgers" / f"{ledger['ledger_id']}.json",
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
    )
    return ledger


def save_candidates(store: str | Path, candidates: dict[str, Any]) -> dict[str, Any]:
    store = Path(store)
    _atomic_write(
        store / "plan_candidates" / f"pc-{candidates['task']['title'][:20]}.json",
        json.dumps(candidates, ensure_ascii=False, indent=2) + "\n",
    )
    return candidates
