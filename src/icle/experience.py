"""Experience Model v0.1 (COST-05/06) — learn usage/outcome distributions, not dollars.

The plan's core: official pricing decides what resources cost; experience
decides how many resources an agent needs; outcomes decide whether it was
worth paying. Therefore ExperienceCostObservation records PRICE + USAGE +
OUTCOME in one document (so value can be computed later), and the model
predicts USAGE DISTRIBUTIONS (P25/P50/P75), duration and outcome — never a
single dollar figure. CostQuote (COST-07) multiplies the predicted usage by
the CURRENT tariff.

Shrinkage (plan §15): effective_n/(effective_n + k) with k=7, layering
route → agent → task_type → system prior, so n=2 never shows 100%.

Similarity weights (plan §16), all versioned:
    same exact route        1.00
    same agent + model      0.90
    same agent other model  0.40
    same project            1.00 / other 0.65
    recency                 1.00 fresh → 0.50 at ~1 year
"""

from __future__ import annotations

import json
import math
import statistics
import uuid
from pathlib import Path
from typing import Any

SCHEMA_OBSERVATION = "icle-experience-observation/v0.1"
EXPERIENCE_MODEL_VERSION = "cost-exp-v0.1"
SIMILARITY_VERSION = "v0.1"
SHRINKAGE_K = 7.0
# accept_without_rework mapping (plan §19) — calibrated, not scientific
MARK_OUTCOME = {"accept": 1.0, "edit": 0.8, "reject": 0.0}
# edit 大类再细分(major/minor 由备注/修正次数估计,v0.1 用 mark 映射)
MARK_OUTCOME_MAJOR_EDIT = 0.4
REDO_OUTCOME = 0.1


class ExperienceError(ValueError):
    pass


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- observation record (COST-05)

def _route_from_step(step: dict[str, Any]) -> dict[str, str]:
    """Best-effort route key from a run step's recommended_agent."""
    agent = str(step.get("agent") or step.get("recommended_agent") or "unknown")
    provider, _, model = agent.partition("/")
    return {
        "agent": provider or agent,
        "model": model or "",
        "provider": provider or "",
        "execution_provider": "direct_cli",  # v0.1: CLI/直连;proxy 留后续
        "context_policy": str(step.get("context_policy") or "PROJECT_STATE"),
    }


def build_observation(
    *,
    task: dict[str, Any],
    run: dict[str, Any],
    outcome: dict[str, Any],
    model_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One observation from a finished run + its outcome (COST-05).

    outcome: {mark: 'accept'|'edit'|'reject'|'redo', user_corrections: int,
    rating: float|None, notes: str|None}. Aggregates per-step usage/cost
    into route-level totals (first step's route represents the run).
    """
    steps = run.get("steps") or []
    if not steps:
        raise ExperienceError("run has no steps; cannot build observation")
    first = steps[0]
    route = _route_from_step(first)
    # Direct CLI steps only carry the Agent ID. Resolve the configured model
    # at the evidence boundary so future exact Agent + model observations can
    # be matched without rewriting historical Episodes or Runs.
    identity = model_identity or {}
    if identity.get("model"):
        route["model"] = str(identity["model"])
        if identity.get("provider"):
            route["provider"] = str(identity["provider"])
        route["model_source"] = str(identity.get("source") or "resolved")
    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0,
                             "cache_read_tokens": 0, "cache_write_tokens": 0,
                             "reasoning_tokens": 0, "tool_calls": {}, "usage_source": "unknown"}
    costs: list[float] = []
    duration_ms = 0
    for step in steps:
        step_usage = step.get("usage") or {}
        for key in ("input_tokens", "output_tokens", "cache_read_tokens",
                    "cache_write_tokens", "reasoning_tokens"):
            usage[key] += int(step_usage.get(key, 0))
        if step.get("usage_source"):
            usage["usage_source"] = step["usage_source"]
        cost = (step.get("cost") or {}).get("cash_cost")
        if cost is not None:
            costs.append(float(cost))
        duration_ms += int(step.get("duration_ms", 0))

    profile = task.get("profile") or {}
    mark = str(outcome.get("mark") or "accept")
    if mark == "edit" and (outcome.get("user_corrections") or 0) > 2:
        outcome_value = MARK_OUTCOME_MAJOR_EDIT
    elif mark == "redo":
        outcome_value = REDO_OUTCOME
    else:
        outcome_value = MARK_OUTCOME.get(mark, 0.0)

    return {
        "schema_version": SCHEMA_OBSERVATION,
        "observation_id": "obs-" + uuid.uuid4().hex[:8],
        "task_id": task.get("task_id"),
        "run_id": run.get("run_id"),
        "task_profile": {
            "primary_type": profile.get("primary_type", "OTHER"),
            "subtype": profile.get("subtype", ""),
            "difficulty": profile.get("difficulty", ""),
            "risk": profile.get("risk", ""),
            "project_id": task.get("project_id", ""),
            "context_tokens": int(profile.get("context_requirement_tokens") or 0),
            "steps": len(steps),
        },
        "route": route,
        "usage": usage,
        "cost": {
            "cash_cost": round(sum(costs), 6) if costs else None,
            "per_step": costs,
        },
        "duration_ms": duration_ms,
        "outcome": {
            "mark": mark,
            "accept_without_rework": outcome_value,
            "user_corrections": int(outcome.get("user_corrections") or 0),
            "rating": outcome.get("rating"),
        },
        "model_version": {
            "experience_model_version": EXPERIENCE_MODEL_VERSION,
            "similarity_version": SIMILARITY_VERSION,
        },
        "created_at": _now(),
    }


def _observations_path(store: str | Path) -> Path:
    return Path(store) / "observations"


def record_observation(store: str | Path, observation: dict[str, Any]) -> dict[str, Any]:
    if observation.get("schema_version") != SCHEMA_OBSERVATION:
        raise ExperienceError("observation: schema_version mismatch")
    path = _observations_path(store) / f"{observation['observation_id']}.json"
    _atomic_write(path, json.dumps(observation, ensure_ascii=False, indent=2) + "\n")
    return observation


def list_observations(store: str | Path, *, limit: int = 1000) -> list[dict[str, Any]]:
    directory = _observations_path(store)
    observations: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                observations.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return observations[-limit:]


# ---------------------------------------------------------------- Experience Model v0.1 (COST-06)

def _similarity(observation: dict[str, Any], *, profile: dict[str, Any], route: dict[str, Any]) -> float:
    """Plan §16 similarity: task × route × project × recency (all versioned)."""
    obs_task = observation["task_profile"]
    obs_route = observation["route"]

    task_sim = 1.0
    if obs_task.get("primary_type") != profile.get("primary_type"):
        task_sim *= 0.5
    if obs_task.get("subtype") and profile.get("subtype") and obs_task.get("subtype") != profile.get("subtype"):
        task_sim *= 0.7
    if obs_task.get("difficulty") and profile.get("difficulty"):
        diff_order = ("D1", "D2", "D3", "D4", "D5")
        try:
            gap = abs(diff_order.index(obs_task["difficulty"]) - diff_order.index(profile["difficulty"]))
        except ValueError:
            gap = 2
        task_sim *= {0: 1.0, 1: 0.8, 2: 0.5}.get(gap, 0.3)

    route_sim = 0.15  # 不同 agent(plan §16 最低档)
    if obs_route.get("agent") == route.get("agent"):
        if obs_route.get("model") == route.get("model") and obs_route.get("model"):
            route_sim = 1.0  # same exact route
        else:
            route_sim = 0.4  # same agent, other model (plan §16)

    project_sim = 1.0 if obs_task.get("project_id") == profile.get("project_id") else 0.65
    recency = 1.0
    from datetime import datetime, timezone

    try:
        created = datetime.fromisoformat(observation["created_at"].replace("Z", "+00:00"))
        days = (datetime.now(timezone.utc) - created).days
        recency = min(1.0, max(0.5, 1.0 - days / 730.0))  # 1 year → 0.5 (plan §16)
    except (ValueError, KeyError):
        recency = 0.9
    return task_sim * route_sim * project_sim * recency


def _weighted_percentile(values: list[float], weights: list[float], percentile: float) -> float:
    """Weighted percentile: sort by value, accumulate normalized weights."""
    pairs = sorted(zip(values, weights))
    total = sum(weights)
    if total <= 0:
        return statistics.median(values) if values else 0.0
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight / total
        if cumulative >= percentile:
            return value
    return pairs[-1][0] if pairs else 0.0


def _effective_n(weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return 0.0
    return round(total * total / sum(w * w for w in weights), 2)


def usage_prediction(
    store: str | Path,
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    prior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Predict usage distribution for (profile, route) — P25/P50/P75, NO dollars.

    prior (optional) provides the shrinkage target (system/global prior);
    it can carry the same shape (e.g. from usage_prediction over all routes).
    """
    observations = list_observations(store)
    weighted: list[tuple[dict[str, Any], float]] = []
    for observation in observations:
        weight = _similarity(observation, profile=profile, route=route)
        if weight > 0:
            weighted.append((observation, weight))
    weighted.sort(key=lambda item: item[1], reverse=True)

    def _dist(key: str) -> dict[str, float]:
        values = [o["usage"].get(key, 0) for o, _ in weighted]
        weights = [w for _, w in weighted]
        local = {
            "p25": round(_weighted_percentile(values, weights, 0.25)),
            "p50": round(_weighted_percentile(values, weights, 0.50)),
            "p75": round(_weighted_percentile(values, weights, 0.75)),
        }
        if not weighted or not values:
            return local
        effective = _effective_n(weights)
        shrink = effective / (effective + SHRINKAGE_K)
        if prior and prior.get(key):
            for level in ("p25", "p50", "p75"):
                local[level] = round(shrink * local[level] + (1 - shrink) * prior[key].get(level, local[level]))
        return local

    durations = [o["duration_ms"] / 1000.0 for o, _ in weighted]
    duration_weights = [w for _, w in weighted]
    effective = _effective_n(duration_weights) if weighted else 0.0
    sample_count = len(weighted)
    if sample_count >= 10:
        confidence = "high"
    elif sample_count >= 5:
        confidence = "medium"
    elif sample_count >= 2:
        confidence = "low"
    else:
        confidence = "very_low"

    return {
        "agent": route.get("agent"),
        "expected_usage": {
            "input_tokens": _dist("input_tokens"),
            "output_tokens": _dist("output_tokens"),
            "cache_read_tokens": _dist("cache_read_tokens"),
            "cache_write_tokens": _dist("cache_write_tokens"),
            "reasoning_tokens": _dist("reasoning_tokens"),
        },
        "expected_duration_s": {
            "p25": round(_weighted_percentile(durations, duration_weights, 0.25), 1),
            "p50": round(_weighted_percentile(durations, duration_weights, 0.50), 1),
            "p75": round(_weighted_percentile(durations, duration_weights, 0.75), 1),
        },
        "sample_count": sample_count,
        "effective_sample_size": effective,
        "confidence": confidence,
        "model_version": {"experience_model_version": EXPERIENCE_MODEL_VERSION,
                          "similarity_version": SIMILARITY_VERSION},
    }


def outcome_prediction(
    store: str | Path,
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    """Predict accept_without_rework (plan §19) with shrinkage toward prior."""
    observations = list_observations(store)
    weighted: list[tuple[dict[str, Any], float]] = []
    for observation in observations:
        weight = _similarity(observation, profile=profile, route=route)
        if weight > 0:
            weighted.append((observation, weight))
    if not weighted:
        return {"accept_without_rework": None, "expected_intervention": None,
                "sample_count": 0, "effective_sample_size": 0.0, "confidence": "very_low"}
    effective = _effective_n([w for _, w in weighted])
    shrink = effective / (effective + SHRINKAGE_K)
    local = sum(o["outcome"]["accept_without_rework"] * w for o, w in weighted) / sum(w for _, w in weighted)
    # prior = 全局平均(outcome across all observations)
    all_outcomes = [o["outcome"]["accept_without_rework"] for o in observations]
    prior_mean = statistics.mean(all_outcomes) if all_outcomes else 0.5
    predicted = shrink * local + (1 - shrink) * prior_mean
    # expected_intervention: 加权平均 user_corrections(受 shrink 约束,避免 n=1 骗人)
    all_corrections = [o["outcome"].get("user_corrections", 0) for o in observations]
    prior_corrections = statistics.mean(all_corrections) if all_corrections else 0.0
    local_corrections = sum(
        o["outcome"].get("user_corrections", 0) * w for o, w in weighted
    ) / sum(w for _, w in weighted) if weighted else 0.0
    intervention = shrink * local_corrections + (1 - shrink) * prior_corrections
    sample_count = len(weighted)
    confidence = "high" if sample_count >= 10 else "medium" if sample_count >= 5 else "low" if sample_count >= 2 else "very_low"
    return {
        "accept_without_rework": round(predicted, 3),
        "expected_intervention": round(intervention, 2),
        "sample_count": sample_count,
        "effective_sample_size": effective,
        "confidence": confidence,
        "model_version": {"experience_model_version": EXPERIENCE_MODEL_VERSION,
                          "similarity_version": SIMILARITY_VERSION},
    }
