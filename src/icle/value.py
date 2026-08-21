"""Value Engine (COST-09/10) — Quality/Cost/Time/Intervention/Risk → Pareto + policy ranking.

The plan's §21-§22: never divide quality by dollars (explodes near $0, and
subscription/local models don't compare). Instead output a Pareto frontier of
route candidates and rank them under a user policy:

    quality_first / balanced / cost_first / speed_first

AdjustedUtility = utility − uncertainty_penalty, so a n=2 "great" candidate
never jumps to the top (plan §22). RouteCandidate carries five dimensions +
confidence + evidence + reason (plan §28) — the Router no longer outputs a
bare agent score.
"""

from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

POLICIES = ("quality_first", "balanced", "cost_first", "speed_first")
DEFAULT_POLICY = "balanced"

# uncertainty penalty by confidence tier (plan §22)
UNCERTAINTY_PENALTY = {"high": 0.0, "medium": 0.05, "low": 0.15, "very_low": 0.30}

# policy → dimension weights (quality+, cost/time/effort/risk−)
POLICY_WEIGHTS: dict[str, dict[str, float]] = {
    "quality_first": {"quality": 1.0, "cost": 0.05, "time": 0.05, "effort": 0.05, "risk": 0.35},
    "balanced": {"quality": 0.5, "cost": 0.35, "time": 0.25, "effort": 0.25, "risk": 0.3},
    "cost_first": {"quality": 0.3, "cost": 1.0, "time": 0.3, "effort": 0.3, "risk": 0.3},
    "speed_first": {"quality": 0.4, "cost": 0.3, "time": 1.0, "effort": 0.3, "risk": 0.4},
}


class ValueError_(ValueError):
    pass


def _cost_to_accept(cost: float | None, quality: float | None) -> float | None:
    """计划书 §20 Money/Time-to-Accept:几何级数 cost + (1-q)cost + ... = cost/q.

    便宜但返工高的 route,其"到接受"的总成本/总时间会显著上升——
    这正是 balanced 应选高质量 route 的依据(§34 行为)。
    """
    if cost is None or quality is None or quality <= 0:
        return None
    return cost / quality


def build_route_candidate(
    store: str | Path,
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    policy: str = DEFAULT_POLICY,
) -> dict[str, Any]:
    """One RouteCandidate:五维预测 + billing + utility + reason (plan §28)."""
    from .cost import route_prediction
    from .cost import billing_mode  # subscription/local hints live there (COST §23)

    if policy not in POLICIES:
        raise ValueError_(f"policy must be one of {POLICIES}, got {policy!r}")
    prediction = route_prediction(
        store, profile=profile, route=route,
        provider=str(route.get("provider") or ""),
        model=str(route.get("model") or ""),
    )
    mode = billing_mode(str(route.get("provider") or ""), str(route.get("model") or ""))
    # 订阅制:成本不明不报 $0(计划书 §26)— cash_marginal_cost=null + quota 说明
    cost = prediction.get("cost") or {}
    if mode == "SUBSCRIPTION":
        cost = {"low": None, "expected": None, "high": None,
                "billing_mode": "SUBSCRIPTION",
                "note": "subscription plan — marginal cost unknown, quota applies"}
    elif mode == "LOCAL_COMPUTE":
        cost = {"low": None, "expected": None, "high": None,
                "billing_mode": "LOCAL_COMPUTE",
                "note": "local compute — no API invoice; infrastructure cash cost is unmeasured"}

    from .decision import score_route

    expected = cost.get("expected")
    expected_time = (prediction.get("time_s") or {}).get("p50")
    decision = score_route(store, route=route, task_profile=profile)
    quality = decision.get("quality_unit")
    intervention = prediction.get("intervention")
    confidence = (
        "medium" if decision.get("quality_source") == "c_residual" else
        "low" if decision.get("quality_source") in {"j_observed", "mixed_c_j"} else
        "insufficient-data"
    )

    # 计划书 §20:utility 用 Cost-to-Accept / Time-to-Accept(返工几何成本),
    # 而不是单次调用成本——便宜但返工的 route 会显式被惩罚。
    cost_to_accept = _cost_to_accept(expected, quality)
    time_to_accept = _cost_to_accept(expected_time, quality)

    # 归一化维度(候选内由 rank 处理;这里保留原始值 + 可计算 utility 的哨兵)
    candidate = {
        "route_id": f"rt-{uuid.uuid4().hex[:6]}",
        "agent": route.get("agent"),
        "model": route.get("model"),
        "provider": route.get("provider"),
        "execution_provider": route.get("execution_provider", "direct_cli"),
        "context_policy": route.get("context_policy", "PROJECT_STATE"),
        "strategy": route.get("strategy", "DIRECT"),
        "expected_quality": quality,
        "expected_cost": cost,
        "expected_time_s": prediction.get("time_s"),
        "expected_intervention": intervention,
        "billing_mode": mode,
        "confidence": confidence,
        "decision": decision,
        "evidence": prediction.get("evidence"),
        "model_version": prediction.get("model_version"),
        "utility": None,
        "recommended": False,
        "reason": [],
    }
    # 可用的候选才能算 utility(缺质量/成本/时间 → utility=None 排最后)
    if expected is not None and quality is not None and expected_time is not None:
        candidate["utility"] = _utility(
            quality=quality, cost=cost_to_accept or 0.0, time_s=time_to_accept or 0.0,
            effort=intervention or 0.0, risk=1.0 - (quality or 0.0),
            weights=POLICY_WEIGHTS[policy],
        )
    candidate["cost_to_accept"] = cost_to_accept
    candidate["time_to_accept"] = time_to_accept
    candidate["reason"] = _explain(candidate, policy)
    return candidate


def _utility(*, quality: float, cost: float, time_s: float, effort: float, risk: float,
             weights: dict[str, float]) -> float:
    """Raw utility; normalized inside rank() across candidates. Higher = better."""
    return (
        weights["quality"] * quality
        - weights["cost"] * cost
        - weights["time"] * time_s
        - weights["effort"] * effort
        - weights["risk"] * risk
    )


def order_candidates(candidates: list[dict[str, Any]], policy: str = DEFAULT_POLICY) -> list[dict[str, Any]]:
    """Policy order: normalize dimensions, apply AdjustedUtility − penalty.

    This is a decision-layer sort, not a capability ranking. Candidates
    without a utility (usually missing quality) go last.
    """
    usable = [c for c in candidates if c.get("utility") is not None]
    if usable:
        max_cost = max((c.get("cost_to_accept") or 0.0) for c in usable) or 1.0
        max_time = max((c.get("time_to_accept") or 0.0) for c in usable) or 1.0
        max_effort = max((c.get("expected_intervention") or 0.0) for c in usable) or 1.0
        for candidate in usable:
            cost = candidate.get("cost_to_accept") or 0.0
            time_s = candidate.get("time_to_accept") or 0.0
            effort = candidate.get("expected_intervention") or 0.0
            risk = 1.0 - (candidate.get("expected_quality") or 0.0)
            weights = POLICY_WEIGHTS[policy]
            raw = _utility(quality=candidate.get("expected_quality") or 0.0,
                           cost=cost / max_cost, time_s=time_s / max_time,
                           effort=effort / max_effort, risk=risk,
                           weights=weights)
            penalty = UNCERTAINTY_PENALTY.get(candidate["confidence"], 0.30)
            candidate["utility"] = round(raw - penalty, 3)
    ordered = sorted(candidates, key=lambda c: c.get("utility") if c.get("utility") is not None else -1e9,
                     reverse=True)
    if ordered:
        ordered[0]["recommended"] = True
    return ordered


def rank_candidates(candidates: list[dict[str, Any]], policy: str = DEFAULT_POLICY) -> list[dict[str, Any]]:
    """Compatibility alias for order_candidates."""
    return order_candidates(candidates, policy=policy)


def pareto_keep(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove dominated candidates (plan §21): keep A if no B is better on all
    of quality/cost/time/effort and strictly better on at least one."""
    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        dominated = False
        for other in candidates:
            if other is candidate:
                continue
            if _dominates(other, candidate):
                dominated = True
                break
        if not dominated:
            kept.append(candidate)
    return kept


def _dominates(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """a dominates b iff a is ≥ on quality and ≤ on cost/time/effort, strictly on one."""
    def value(candidate, key):
        if key == "expected_quality":
            return candidate.get(key)
        if key == "expected_cost":
            cost = candidate.get(key) or {}
            return cost.get("expected")
        if key == "expected_time_s":
            time = candidate.get(key) or {}
            return time.get("p50")
        if key == "expected_intervention":
            return candidate.get(key)
        return None

    dimensions = ("expected_quality", "expected_cost", "expected_time_s", "expected_intervention")
    values_a = [value(a, d) for d in dimensions]
    values_b = [value(b, d) for d in dimensions]
    if any(v is None for v in values_a + values_b):
        return False
    # quality 越大越好;cost/time/effort 越小越好
    quality_ok = values_a[0] >= values_b[0]
    cost_ok = values_a[1] <= values_b[1]
    time_ok = values_a[2] <= values_b[2]
    effort_ok = values_a[3] <= values_b[3]
    strictly_better = (values_a[0] > values_b[0] or values_a[1] < values_b[1]
                       or values_a[2] < values_b[2] or values_a[3] < values_b[3])
    return quality_ok and cost_ok and time_ok and effort_ok and strictly_better


def _explain(candidate: dict[str, Any], policy: str) -> list[str]:
    """Short rule-based reasons for the UI (plan §29 'Why')."""
    reasons: list[str] = []
    quality = candidate.get("expected_quality")
    cost = (candidate.get("expected_cost") or {}).get("expected")
    time_s = (candidate.get("expected_time_s") or {}).get("p50")
    if quality is not None:
        if quality >= 0.85:
            reasons.append("High expected acceptance without rework")
        elif quality < 0.6:
            reasons.append("Lower expected acceptance — may need rework")
    if cost is not None and policy in ("cost_first", "balanced"):
        reasons.append(f"Expected cost ≈ ${cost:.2f}")
    if time_s is not None and policy in ("speed_first", "balanced"):
        reasons.append(f"Expected time ≈ {time_s:.0f}s")
    if candidate.get("billing_mode") == "SUBSCRIPTION":
        reasons.append("Subscription plan — marginal cost unknown")
    if not reasons:
        reasons.append("Cold start — no local experience yet")
    return reasons
