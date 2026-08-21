"""Decision layer: versioned scalarization over seven-axis measurement.

Measurement emits A/B/J/C. This module is the only place that may collapse
observed axes into a quality signal. The loss never writes back to the
engine, never treats missing as 0, and never uses public A as quality.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .evolution import capability_loadings_for, load_measurement, subject_identity

LOSS_ID = "icle-decision-loss/v0.1"
FORBIDDEN_TOTAL_KEYS = {
    "total",
    "total_score",
    "overall",
    "overall_score",
    "rank",
    "leaderboard_score",
}


class DecisionError(ValueError):
    pass


def _logit_to_unit(value: float) -> float:
    """Map canonical logit onto (0, 1). Inverse of the measurement scale."""
    clipped = max(-20.0, min(20.0, float(value)))
    return 1.0 / (1.0 + math.exp(-clipped))


def _axis_row(measurement: dict[str, Any], axis: str) -> dict[str, Any]:
    return ((measurement.get("axes") or {}).get(axis) or {})


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def _axis_signal(row: dict[str, Any]) -> tuple[float | None, str | None]:
    """C residual first; J only when the C gate failed. A is never quality."""
    residual = _numeric(row.get("c") if row.get("c") is not None else row.get("mean"))
    if row.get("status") == "observed" and residual is not None:
        return residual, "c_residual"
    observed = _numeric(row.get("j"))
    if observed is not None:
        return observed, "j_observed"
    return None, None


def score_measurement(
    measurement: dict[str, Any] | None,
    task_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Q-weighted mean of observed axes. Unobserved axes are skipped."""
    weights = capability_loadings_for(task_profile)
    axes = (measurement or {}).get("axes") or {}
    used: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not weights:
        payload = {
            "schema_version": "icle-decision-signal/v0.1",
            "loss_id": LOSS_ID,
            "quality": None,
            "quality_unit": None,
            "quality_source": "none",
            "axes_used": [],
            "axes_skipped": [],
            "reason": "q_unmapped",
        }
        _assert_no_totals(payload)
        return payload
    for axis, weight in weights.items():
        value, source = _axis_signal(axes.get(axis) or {})
        if value is None or source is None:
            skipped.append(axis)
            continue
        used.append({
            "axis": axis,
            "weight": float(weight),
            "value": value,
            "source": source,
        })
    if not used:
        payload = {
            "schema_version": "icle-decision-signal/v0.1",
            "loss_id": LOSS_ID,
            "quality": None,
            "quality_unit": None,
            "quality_source": "none",
            "axes_used": [],
            "axes_skipped": skipped,
            "reason": "no_observed_quality_axis",
        }
        _assert_no_totals(payload)
        return payload
    total_weight = sum(item["weight"] for item in used)
    quality = sum(item["value"] * item["weight"] for item in used) / total_weight
    sources = {item["source"] for item in used}
    if sources == {"c_residual"}:
        quality_source = "c_residual"
    elif sources == {"j_observed"}:
        quality_source = "j_observed"
    else:
        quality_source = "mixed_c_j"
    payload = {
        "schema_version": "icle-decision-signal/v0.1",
        "loss_id": LOSS_ID,
        "quality": round(quality, 6),
        "quality_unit": round(_logit_to_unit(quality), 6),
        "quality_source": quality_source,
        "axes_used": used,
        "axes_skipped": skipped,
        "reason": None,
    }
    _assert_no_totals(payload)
    return payload


def route_identity(route: dict[str, Any]) -> dict[str, Any]:
    agent = str(route.get("agent") or route.get("agent_id") or "")
    if "/" in agent and not route.get("model"):
        provider_id, _, model = agent.partition("/")
        return {
            "agent": provider_id,
            "agent_id": agent,
            "provider_id": route.get("provider_id") or provider_id,
            "model": model,
            "provider": route.get("provider") or provider_id,
            "execution_mode": "provider_api",
        }
    return {
        "agent": agent,
        "agent_id": route.get("agent_id") or agent,
        "provider_id": route.get("provider_id") or "",
        "model": route.get("model") or "",
        "provider": route.get("provider") or "",
        "execution_mode": route.get("execution_mode") or "direct_cli",
    }


def score_route(
    store: str | Path,
    *,
    route: dict[str, Any],
    task_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    identity = route_identity(route)
    measurement = load_measurement(store, identity)
    signal = score_measurement(measurement, task_profile)
    signal["subject_id"] = identity and subject_identity(identity).get("subject_id")
    signal["identity"] = identity
    return signal


def _assert_no_totals(payload: dict[str, Any]) -> None:
    leaked = FORBIDDEN_TOTAL_KEYS & set(payload)
    if leaked:
        raise DecisionError(f"decision signal leaked forbidden keys: {sorted(leaked)}")
