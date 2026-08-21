"""RecommendationEngine v0.3 (v0.4 P13 / Batch 5): route-level recommendation.

计划书 §36-41:
- 推荐对象是 Route(agent 组合 + 策略 + 成本),不是单一 agent(§36)
- Pipeline 四步(§37): Eligibility(确定性淘汰) → Evidence Retrieval(相似任务
  证据) → Utility(历史表现/评分/成本) → Confidence(样本/多样性) ——
  Utility ≠ Confidence
- 输出含 why[] / uncertainty[](§38),冷启动 agent 进 Exploration Candidate
  (Evidence=NONE,§39);不直接上强化学习,Exploration 用 Active Replay(§40)
- RecommendationDecision(§41): 每次推荐前保存,结果后对比(accepted/redo/
  rating/cost/duration),积累后评估 Router 是否优于 Always-Kimi 等基线

确定性统计,无 LLM。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cost import actual_cash_cost, list_actuals

POSITIVE_MARKS = {"accept", "adopted", "adopted_with_minor_edits"}
NEGATIVE_MARKS = {"reject", "redo", "abandoned_result", "requested_rework"}


class RecommendationError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


def _load_json_files(directory: Path, pattern: str) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob(pattern)):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


# ---------------------------------------------------------------- eligibility (§37-1)


def eligible_agents(
    *,
    available: list[str],
    linked: list[str] | None = None,
    required_capabilities: list[str] | None = None,
) -> list[str]:
    """Step 1 — Eligibility: 确定性淘汰离线/未链接/缺能力的 agent。"""
    allowed = set(linked or available)
    eligible = [agent for agent in available if agent in allowed]
    if required_capabilities and "filesystem" in required_capabilities:
        pass  # 全部本地 CLI 默认具备;MVP 不做深能力过滤
    return eligible


# ---------------------------------------------------------------- evidence (§37-2)


def _cost_belongs_to_agent(actual: dict[str, Any], agent_id: str) -> bool:
    """Use explicit execution attribution; never spread global spend to routes."""
    attributed = str(actual.get("agent") or "")
    if attributed:
        return attributed == agent_id
    if "/" not in agent_id or actual.get("scope") != "task_execution":
        return False
    provider_id, _, model = agent_id.partition("/")
    return (
        str(actual.get("provider_id") or "") == provider_id
        and str(actual.get("model") or "") == model
    )


def _agent_evidence(store: Path, agent_id: str) -> dict[str, Any]:
    """Step 2/3 — Evidence Retrieval + Utility: 从 marks/ratings/replays 聚合。

    返回 {outcomes_positive, outcomes_negative, ratings, replays, cost_sum}.
    """
    positive = negative = 0
    for mark in _load_json_files(store / "marks", "m-*.json"):
        if mark.get("agent_id") != agent_id:
            continue
        if mark.get("mark") in POSITIVE_MARKS:
            positive += 1
        elif mark.get("mark") in NEGATIVE_MARKS:
            negative += 1
    ratings = [r for r in _load_json_files(store / "ratings", "r-*.json") if r.get("agent_id") == agent_id]
    replay_count = 0
    for run in _load_json_files(store / "replays", "rp-*/replay.json"):
        if run.get("target_agent_revision", {}).get("agent_id") == agent_id:
            replay_count += 1
    cost_sum = 0.0
    cost_count = 0
    for actual in list_actuals(store, limit=100_000):
        if not _cost_belongs_to_agent(actual, agent_id):
            continue
        cost = actual_cash_cost(actual)  # H1: 兼容 v0.1 cost / v0.2 cash_cost
        if cost is not None:
            cost_sum += cost
            cost_count += 1
    return {
        "outcomes_positive": positive,
        "outcomes_negative": negative,
        "ratings": ratings,
        "replay_count": replay_count,
        "cost_sum": cost_sum,
        "cost_count": cost_count,
    }


def _utility(evidence: dict[str, Any]) -> tuple[float, list[str]]:
    """Utility(§37-3): 历史结果 + 用户评分 + 成本惩罚。缺失项不惩罚。"""
    positive = evidence["outcomes_positive"]
    negative = evidence["outcomes_negative"]
    outcomes = positive + negative
    adopt_rate = positive / outcomes if outcomes else None
    why: list[str] = []
    rating_avg = None
    if evidence["ratings"]:
        rating_avg = sum(r.get("overall_preference", 0) for r in evidence["ratings"]) / len(
            evidence["ratings"]
        )
        why.append(f"{len(evidence['ratings'])} user ratings")
    score = 0.0
    if adopt_rate is not None:
        score += 0.6 * adopt_rate
        why.append(f"{positive}/{outcomes} accepted")
    if rating_avg is not None:
        score += 0.4 * (rating_avg / 5)
    if score == 0 and evidence["replay_count"]:
        score = 0.3  # 有运行但无评价:中性偏探索
    if evidence["cost_count"]:
        average_cost = evidence["cost_sum"] / evidence["cost_count"]
        # Bounded, monotonic cash-cost penalty. It affects ordering without
        # allowing one expensive call to erase all measured quality.
        cost_penalty = min(0.25, 0.1 * math.log1p(average_cost))
        score -= cost_penalty
        why.append(
            f"avg cash cost ${average_cost:.4f} (-{cost_penalty:.3f} utility)"
        )
    return round(max(-0.25, min(1.0, score)), 3), why


def _confidence(evidence: dict[str, Any]) -> tuple[float, list[str]]:
    """Confidence(§37-4): 样本数/多样性/近期。Utility ≠ Confidence。"""
    n = evidence["outcomes_positive"] + evidence["outcomes_negative"] + len(evidence["ratings"])
    uncertainty: list[str] = []
    confidence = min(1.0, n / 10)
    if n < 3:
        uncertainty.append(f"only {n} samples")
    if n == 0:
        confidence = 0.0
    return round(confidence, 3), uncertainty


# ---------------------------------------------------------------- routes (§36)


def recommend_routes(
    store: str | Path,
    *,
    title: str,
    description: str,
    difficulty: str = "D3",
    risk: str = "R1",
    available: list[str] | None = None,
    linked: list[str] | None = None,
    include_cold_start: bool = True,
) -> dict[str, Any]:
    """推荐 Route 候选(§36/§38): 每个 route 含 utility/confidence/evidence/
    why/uncertainty。候选 = 单 agent DIRECT + 组合 DECOMPOSE(高难度)。

    Cold-start agent(无证据)不判 0 分,标记 Evidence=NONE 且仅在有探索意愿时
    纳入(§39)。
    """
    store = Path(store)
    available = available or ["kimi"]
    eligible = eligible_agents(available=available, linked=linked)
    if not eligible:
        eligible = available  # 无 linked 时退回可用列表(MVP)

    routes = []
    for agent in eligible:
        evidence = _agent_evidence(store, agent)
        utility, why = _utility(evidence)
        confidence, uncertainty = _confidence(evidence)
        evidence_level = "NONE"
        if evidence["outcomes_positive"] + evidence["outcomes_negative"] + len(evidence["ratings"]) > 0:
            evidence_level = "LOW" if confidence < 0.3 else ("MEDIUM" if confidence < 0.7 else "HIGH")
        from .decision import score_route

        decision = score_route(
            store,
            route={"agent": agent},
            task_profile={"primary_type": "CODING"} if difficulty in {"D2", "D3", "D4", "D5"} else {},
        )
        if decision.get("quality_unit") is not None:
            utility = decision["quality_unit"]
            why = [f"decision {decision['quality_source']} on {','.join(item['axis'] for item in decision['axes_used'])}"]
            evidence_level = "MEASURED"
        elif decision.get("reason"):
            uncertainty = list(uncertainty) + [str(decision["reason"])]
        routes.append({
            "route_id": f"rt-{uuid.uuid4().hex[:6]}",
            "agents": [agent],
            "strategy": "DIRECT",
            "label": agent,
            "expected_utility": utility,
            "confidence": confidence,
            "evidence": evidence_level,
            "why": why,
            "uncertainty": uncertainty,
            "decision": decision,
            "cost_sum": evidence["cost_sum"],
            "exploration": evidence_level == "NONE" and include_cold_start,
        })
    routes.sort(key=lambda r: (r["exploration"], -r["expected_utility"]))

    # 高难度 + 多 agent: 追加 DECOMPOSE 组合路由
    if difficulty in ("D4", "D5") and len(eligible) >= 2:
        combo_evidence = {
            "outcomes_positive": 0, "outcomes_negative": 0, "ratings": [], "replay_count": 0,
            "cost_sum": 0.0, "cost_count": 0,
        }
        for agent in eligible[:3]:
            part = _agent_evidence(store, agent)
            combo_evidence["outcomes_positive"] += part["outcomes_positive"]
            combo_evidence["outcomes_negative"] += part["outcomes_negative"]
            combo_evidence["ratings"].extend(part["ratings"])
            combo_evidence["replay_count"] += part["replay_count"]
            combo_evidence["cost_sum"] += part["cost_sum"]
            combo_evidence["cost_count"] += part["cost_count"]
        utility, why = _utility(combo_evidence)
        confidence, uncertainty = _confidence(combo_evidence)
        routes.append({
            "route_id": f"rt-{uuid.uuid4().hex[:6]}",
            "agents": eligible[:3],
            "strategy": "DECOMPOSE",
            "label": " → ".join(eligible[:3]),
            "expected_utility": round(utility + 0.05, 3),  # 组合加分
            "confidence": confidence,
            "evidence": "NONE" if confidence == 0 else "LOW",
            "why": why + ["multi-agent assurance"],
            "uncertainty": uncertainty,
            "cost_sum": combo_evidence["cost_sum"],
            "exploration": False,
        })
    routes.sort(key=lambda r: (r["exploration"], -r["expected_utility"]))

    return {
        "schema_version": "icle-recommendation-routes/v0.1",
        "task": {"title": title, "description": description, "difficulty": difficulty, "risk": risk},
        "routes": routes,
        "created_at": _now(),
    }


# ---------------------------------------------------------------- decision (§41)


def save_decision(
    store: str | Path,
    *,
    task_id: str,
    chosen_route: dict[str, Any],
    alternatives: list[dict[str, Any]],
    reason: str,
    router_version: str = "v0.3",
) -> dict[str, Any]:
    """RecommendationDecision(§41): 推荐结果产生前保存,供结果后对比。"""
    decision = {
        "schema_version": "icle-recommendation-decision/v0.1",
        "decision_id": "rd-" + uuid.uuid4().hex[:10],
        "task_id": task_id,
        "chosen_route": chosen_route,
        "alternatives": alternatives,
        "reason": reason,
        "router_version": router_version,
        "created_at": _now(),
    }
    _atomic_write(
        Path(store) / "decisions" / f"{decision['decision_id']}.json",
        json.dumps(decision, ensure_ascii=False, indent=2) + "\n",
    )
    return decision


def list_decisions(store: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    return _load_json_files(Path(store) / "decisions", "rd-*.json")[-limit:]
