"""Deterministic Agent + model decision profiles.

This module is a read-only projection over four evidence layers:

* external benchmark snapshots are the 6-domain public prior;
* exact Agent + model observations supply cost and duration, not quality;
* A+B+C measurement from the vendored evaluation engine is the capability posterior;
* the decision profile combines them without changing execution routing.

The projection deliberately keeps unknown values as ``None``.  A catalog
capability declaration is descriptive metadata and never becomes a numeric
quality score.  Historical files, Episodes, Runs, Ledger entries and
CostActual records are never rewritten here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cost import billing_mode, list_actuals
from .external_evaluation import external_baseline, resolve_model_identity
from .tariff import compute_from_snapshot, resolve_snapshot

SCHEMA = "icle-agent-decision-profile/v0.2"
PROFILE_VERSION = "agent-profile-v0.2"

# These domains match the currently imported external benchmark catalog.  The
# task taxonomy remains the source of truth; this is only a projection layer.
CAPABILITY_DOMAINS = ("coding", "refactoring", "terminal", "research", "planning", "writing")

# Cold-start task workloads are explicit assumptions, not observed usage.  A
# D2/D3/D4 envelope gives a useful quote before ICLE has local observations;
# the actual local P25/P50/P75 distribution replaces it once available.
BASE_WORKLOAD_SCHEMA = "icle-standard-workload-v0.1"
BASE_WORKLOAD_VERSION = "coding-agent-d2-d4-v0.1"
BASE_WORKLOADS: dict[str, dict[str, dict[str, int]]] = {
    "coding": {
        "D1": {"input_tokens": 8_000, "output_tokens": 1_500},
        "D2": {"input_tokens": 20_000, "output_tokens": 4_000},
        "D3": {"input_tokens": 60_000, "output_tokens": 12_000},
        "D4": {"input_tokens": 150_000, "output_tokens": 30_000},
        "D5": {"input_tokens": 300_000, "output_tokens": 60_000},
    },
    "refactoring": {
        "D1": {"input_tokens": 12_000, "output_tokens": 2_500},
        "D2": {"input_tokens": 30_000, "output_tokens": 6_000},
        "D3": {"input_tokens": 80_000, "output_tokens": 16_000},
        "D4": {"input_tokens": 180_000, "output_tokens": 36_000},
        "D5": {"input_tokens": 360_000, "output_tokens": 72_000},
    },
    "terminal": {
        "D1": {"input_tokens": 10_000, "output_tokens": 2_000},
        "D2": {"input_tokens": 25_000, "output_tokens": 5_000},
        "D3": {"input_tokens": 70_000, "output_tokens": 14_000},
        "D4": {"input_tokens": 160_000, "output_tokens": 32_000},
        "D5": {"input_tokens": 320_000, "output_tokens": 64_000},
    },
    # No benchmark-specific usage source is claimed for these domains.  The
    # same workload envelope is still a transparent planning assumption.
    "research": {
        "D1": {"input_tokens": 8_000, "output_tokens": 2_000},
        "D2": {"input_tokens": 20_000, "output_tokens": 5_000},
        "D3": {"input_tokens": 60_000, "output_tokens": 15_000},
        "D4": {"input_tokens": 140_000, "output_tokens": 35_000},
        "D5": {"input_tokens": 280_000, "output_tokens": 70_000},
    },
    "planning": {
        "D1": {"input_tokens": 6_000, "output_tokens": 1_500},
        "D2": {"input_tokens": 15_000, "output_tokens": 4_000},
        "D3": {"input_tokens": 45_000, "output_tokens": 10_000},
        "D4": {"input_tokens": 100_000, "output_tokens": 24_000},
        "D5": {"input_tokens": 220_000, "output_tokens": 50_000},
    },
    "writing": {
        "D1": {"input_tokens": 8_000, "output_tokens": 2_000},
        "D2": {"input_tokens": 20_000, "output_tokens": 5_000},
        "D3": {"input_tokens": 60_000, "output_tokens": 15_000},
        "D4": {"input_tokens": 140_000, "output_tokens": 35_000},
        "D5": {"input_tokens": 280_000, "output_tokens": 70_000},
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_files(directory: Path, pattern: str = "*.json") -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(directory.glob(pattern)):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            out.append(value)
    return out


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _recency_weight(value: Any) -> float:
    created = _parse_time(value)
    if created is None:
        return 0.75
    age_days = max(0, (datetime.now(timezone.utc) - created).days)
    return round(max(0.5, 1.0 - age_days / 730.0), 6)


def _effective_n(weights: list[float]) -> float:
    total = sum(weights)
    if total <= 0:
        return 0.0
    return round(total * total / sum(weight * weight for weight in weights), 2)


def _weighted_mean(values: list[float], weights: list[float]) -> float | None:
    total = sum(weights)
    if not values or total <= 0:
        return None
    return sum(value * weight for value, weight in zip(values, weights)) / total


def _weighted_percentile(values: list[float], weights: list[float], percentile: float) -> float | None:
    if not values or not weights or sum(weights) <= 0:
        return None
    pairs = sorted(zip(values, weights), key=lambda pair: pair[0])
    threshold = sum(weights) * percentile
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def _domain_for_task(task_profile: dict[str, Any] | None) -> str:
    profile = task_profile or {}
    subtype = str(profile.get("subtype") or "").lower()
    primary = str(profile.get("primary_type") or "").upper()
    if subtype in {"refactor", "refactoring"}:
        return "refactoring"
    if primary == "CODING":
        return "coding"
    if primary in {"SYSTEM_OPERATION", "SYSTEM_OPERATIONS"}:
        return "terminal"
    if primary == "RESEARCH":
        return "research"
    if primary == "PLANNING":
        return "planning"
    if primary in {"WRITING", "DOCUMENTATION"}:
        return "writing"
    return "coding" if not primary else "general"


def _provider_slug(value: str | None) -> str:
    raw = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    aliases = {
        "deep seek": "deepseek",
        "deepseek": "deepseek",
        "open ai": "openai",
        "anthropic": "anthropic",
        "claude": "anthropic",
        "moonshot": "kimi",
        "kimi": "kimi",
        "google": "google",
        "gemini": "google",
    }
    return aliases.get(raw, raw.replace(" ", "-"))


def _provider_for_id(store: Path, provider_id: str | None) -> str | None:
    if not provider_id:
        return None
    try:
        from .provider import list_providers

        for provider in list_providers(store):
            if provider.get("provider_id") == provider_id:
                return _provider_slug(provider.get("display_name") or provider.get("type"))
    except Exception:  # provider discovery must not break a read-only profile
        pass
    return _provider_slug(provider_id)


def _target_route(
    store: Path,
    agent_id: str,
    metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = metadata or {}
    identity = resolve_model_identity(store, agent_id, metadata=metadata)
    if "/" in agent_id:
        provider_id, _, model = agent_id.partition("/")
        provider = _provider_for_id(store, metadata.get("provider_id") or provider_id)
        return {
            "agent": provider_id,
            "agent_id": agent_id,
            "model": model,
            "provider": provider,
            "provider_id": metadata.get("provider_id") or provider_id,
            "execution_mode": "provider_api",
            "execution_billing_mode": metadata.get("execution_billing_mode") or "API_METERED",
            "identity": {**identity, "provider": provider, "provider_id": provider_id},
        }
    model = identity.get("model")
    provider = _provider_slug(identity.get("provider")) if identity.get("provider") else None
    return {
        "agent": agent_id,
        "agent_id": agent_id,
        "model": model,
        "provider": provider,
        "provider_id": None,
        "execution_mode": "direct_cli",
        "execution_billing_mode": metadata.get("execution_billing_mode") or "UNKNOWN",
        "identity": identity,
    }


def _observation_route_matches(observation: dict[str, Any], route: dict[str, Any]) -> bool:
    observed = observation.get("route") or {}
    model = str(route.get("model") or "")
    return bool(
        model
        and str(observed.get("agent") or "") == str(route.get("agent") or "")
        and str(observed.get("model") or "") == model
    )


def _local_stats(
    store: Path,
    route: dict[str, Any],
    domain: str,
) -> dict[str, Any]:
    observations = _json_files(store / "observations")
    selected: list[tuple[dict[str, Any], float]] = []
    for observation in observations:
        if not _observation_route_matches(observation, route):
            continue
        observed_domain = _domain_for_task(observation.get("task_profile"))
        if domain != "general" and observed_domain != domain:
            continue
        selected.append((observation, _recency_weight(observation.get("created_at"))))

    weights = [weight for _, weight in selected]
    effective_n = _effective_n(weights)
    # Accept/reject is not a capability posterior. Quality lives in the
    # A+B+C measurement engine; observations here only keep cost and time.

    durations = [float(item.get("duration_ms") or 0) / 1000.0 for item, _ in selected]
    usage_keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
    )
    usage: dict[str, dict[str, float | None]] = {}
    for key in usage_keys:
        values = [float((item.get("usage") or {}).get(key, 0) or 0) for item, _ in selected]
        usage[key] = {
            "p25": _round_or_none(_weighted_percentile(values, weights, 0.25)),
            "p50": _round_or_none(_weighted_percentile(values, weights, 0.50)),
            "p75": _round_or_none(_weighted_percentile(values, weights, 0.75)),
        }

    known_costs: list[tuple[float, float]] = []
    for item, weight in selected:
        cost = (item.get("cost") or {}).get("cash_cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            known_costs.append((float(cost), weight))
    cost_values = [value for value, _ in known_costs]
    cost_weights = [weight for _, weight in known_costs]
    intervention_values = [float((item.get("outcome") or {}).get("user_corrections", 0) or 0) for item, _ in selected]
    intervention = _weighted_mean(intervention_values, weights)

    return {
        "sample_count": len(selected),
        "effective_sample_size": effective_n,
        "quality": None,
        "raw_quality": None,
        "confidence": _confidence(len(selected), effective_n),
        "quality_source": "deferred_to_measurement",
        "duration_s": {
            "p25": _round_or_none(_weighted_percentile(durations, weights, 0.25), 1),
            "p50": _round_or_none(_weighted_percentile(durations, weights, 0.50), 1),
            "p75": _round_or_none(_weighted_percentile(durations, weights, 0.75), 1),
        },
        "usage": usage,
        "known_cost": {
            "sample_count": len(known_costs),
            "p25": _round_or_none(_weighted_percentile(cost_values, cost_weights, 0.25), 6),
            "p50": _round_or_none(_weighted_percentile(cost_values, cost_weights, 0.50), 6),
            "p75": _round_or_none(_weighted_percentile(cost_values, cost_weights, 0.75), 6),
        },
        "intervention": _round_or_none(intervention, 2),
        "source": "local_observations" if selected else "none",
    }


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    return round(float(value), digits) if value is not None else None


def _confidence(sample_count: int, effective_n: float) -> str:
    if sample_count >= 10 and effective_n >= 7:
        return "high"
    if sample_count >= 5 and effective_n >= 3:
        return "medium"
    if sample_count >= 1:
        return "low"
    return "very_low"


def _base_prior(external: dict[str, Any]) -> dict[str, Any]:
    match = external.get("match_strength")
    strength_by_match = {
        "agent+model": 12.0,
        "model-only": 6.0,
        "agent+model-family": 4.0,
        "model-family": 3.0,
        "agent-only": 2.0,
    }
    records = external.get("records") or []
    # Rows in the same benchmark share a trial set.  Do not turn three model
    # leaderboard rows with n_trials=445 into a fictitious 1,335 trials.
    benchmark_sizes: dict[str, int] = {}
    for record in records:
        source_id = str(record.get("source_id") or record.get("benchmark") or "unknown")
        benchmark_sizes[source_id] = max(
            benchmark_sizes.get(source_id, 0),
            max(1, int((record.get("metadata") or {}).get("n_trials") or 1)),
        )
    benchmark_n = sum(benchmark_sizes.values())
    return {
        "score": external.get("score"),
        "status": external.get("status", "unavailable"),
        "confidence": external.get("confidence", "none"),
        "match_strength": match,
        "prior_strength": strength_by_match.get(match, 0.0),
        "benchmark_sample_size": benchmark_n if records else 0,
        "source": "external_benchmark" if external.get("score") is not None else "none",
        "records": records,
        "sources_checked": external.get("sources_checked", []),
        "reason": external.get("reason"),
    }


def _fused_quality(base: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    base_score = base.get("score")
    local_score = local.get("quality")
    local_n = float(local.get("effective_sample_size") or 0)
    base_n = float(base.get("prior_strength") or 0)
    if base_score is not None and local_score is not None:
        total = base_n + local_n
        score = (float(base_score) * base_n + float(local_score) * local_n) / total if total else None
        source = "base+local"
    elif local_score is not None:
        score = local_score
        source = "local"
    elif base_score is not None:
        score = base_score
        source = "base"
    else:
        score = None
        source = "none"
    return {
        "score": _round_or_none(score, 4),
        "source": source,
        "base_score": base_score,
        "local_score": local_score,
        "base_weight": base_n,
        "local_effective_sample_size": local_n,
        "confidence": (
            "medium" if source == "base+local" and local_n >= 3 else
            base.get("confidence", "none") if source == "base" else
            local.get("confidence", "very_low") if source == "local" else "none"
        ),
    }


def _actual_call_cost_stats(store: Path, route: dict[str, Any]) -> dict[str, Any]:
    """Exact Provider + model CostActual records, never promoted to task cost."""
    provider = str(route.get("provider") or "")
    model = str(route.get("model") or "")
    values: list[float] = []
    for actual in list_actuals(store, limit=100_000):
        if str(actual.get("provider") or "").lower() != provider.lower():
            continue
        if str(actual.get("model") or "") != model:
            continue
        cost = actual.get("cash_cost")
        if cost is None:
            cost = actual.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            values.append(float(cost))
    weights = [1.0] * len(values)
    return {
        "sample_count": len(values),
        "p25": _round_or_none(_weighted_percentile(values, weights, 0.25), 6),
        "p50": _round_or_none(_weighted_percentile(values, weights, 0.50), 6),
        "p75": _round_or_none(_weighted_percentile(values, weights, 0.75), 6),
        "scope": "per_llm_call",
    }


def _base_workload_cost(
    snapshot: dict[str, Any],
    *,
    domain: str,
    billing_mode_value: str,
) -> dict[str, Any]:
    """Price a transparent D2/D3/D4 workload envelope with the catalog tariff."""
    workloads = BASE_WORKLOADS.get(domain) or BASE_WORKLOADS["coding"]
    levels: dict[str, dict[str, Any]] = {}
    for difficulty, workload in workloads.items():
        usage = {
            "input_tokens": workload["input_tokens"],
            "output_tokens": workload["output_tokens"],
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "tool_calls": {},
        }
        calculation = compute_from_snapshot(snapshot, usage)
        levels[difficulty] = {
            "difficulty": difficulty,
            "input_tokens": workload["input_tokens"],
            "output_tokens": workload["output_tokens"],
            "cash_cost": round(calculation["total"], 6)
            if billing_mode_value == "API_METERED" else None,
            "catalog_cash_cost": round(calculation["total"], 6),
            "billing_mode": billing_mode_value,
        }
    expected = levels["D3"]
    return {
        "schema_version": BASE_WORKLOAD_SCHEMA,
        "workload_version": BASE_WORKLOAD_VERSION,
        "domain": domain,
        "difficulty_envelope": list(workloads),
        "assumptions": {
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
            "tool_calls": {},
            "interpretation": "standard workload envelope, not local usage",
        },
        "low": levels["D2"]["cash_cost"],
        "expected": expected["cash_cost"],
        "high": levels["D4"]["cash_cost"],
        "levels": levels,
        "source": "catalog_tariff_x_standard_workload",
        "cash_cost_basis": "catalog_api_rate",
        "unknown_reason": (
            "non_cash_billing_mode" if billing_mode_value != "API_METERED" else None
        ),
    }


def _pricing_profile(
    store: Path,
    route: dict[str, Any],
    local: dict[str, Any],
    *,
    domain: str,
) -> dict[str, Any]:
    provider = route.get("provider")
    model = route.get("model")
    catalog_mode = billing_mode(str(provider or ""), str(model or "")) if model else "UNKNOWN"
    execution_mode = str(route.get("execution_mode") or "provider_api")
    execution_billing_mode = str(
        route.get("execution_billing_mode")
        or (catalog_mode if execution_mode == "provider_api" else "UNKNOWN")
    )
    local_task_cost = {
        "low": local["known_cost"].get("p25"),
        "expected": local["known_cost"].get("p50"),
        "high": local["known_cost"].get("p75"),
        "source": "local_task_observations" if local["known_cost"].get("sample_count") else None,
    }
    has_local_cash_cost = local_task_cost["expected"] is not None
    result: dict[str, Any] = {
        # `billing_mode` remains the catalog/API rate basis for compatibility.
        # Execution billing is separate because a direct CLI may use a
        # subscription or account quota instead of API metering.
        "billing_mode": catalog_mode if model else "UNKNOWN",
        "catalog_billing_mode": catalog_mode if model else "UNKNOWN",
        "execution_mode": execution_mode,
        "execution_billing_mode": execution_billing_mode,
        "status": "unknown" if not model else "unresolved",
        "provider": provider,
        "model": model,
        "unit_rates": None,
        "pricing_snapshot": None,
        "source": None,
        # Task-level cash cost prefers exact local observations. A catalog
        # workload estimate only becomes execution cash cost for API-metered
        # execution; direct CLI billing remains unknown until locally observed.
        "typical_task_cost": local_task_cost,
        "api_equivalent_task_cost": None,
        "execution_task_cost": {
            "low": local_task_cost["low"],
            "expected": local_task_cost["expected"],
            "high": local_task_cost["high"],
            "source": "local_task_observations" if has_local_cash_cost else None,
            "billing_mode": execution_billing_mode,
            "cash_cost_status": "measured" if has_local_cash_cost else "unknown",
            "unknown_reason": None if has_local_cash_cost else "execution_billing_mode_unresolved",
        },
        "base_cost_prior": None,
        "local_call_cost": _actual_call_cost_stats(store, route),
        "unknown_reason": None,
    }
    if not model:
        result["unknown_reason"] = "model_identity_unresolved"
        return result
    try:
        snapshot, source = resolve_snapshot(store, str(provider or ""), str(model))
    except Exception:
        result["unknown_reason"] = "no_pricing_snapshot"
    else:
        rates: dict[str, float] = {}
        for component in snapshot.get("pricing", []):
            if component.get("kind") == "token":
                category = str(component.get("category"))
                rates[category] = float(component.get("price", 0))
        base_cost = _base_workload_cost(
            snapshot, domain=domain, billing_mode_value=catalog_mode
        )
        base_cost["execution_billing_mode"] = execution_billing_mode
        base_cost["execution_cash_cost"] = (
            base_cost["expected"]
            if execution_billing_mode == "API_METERED"
            else None
        )
        base_cost["api_equivalent_cost"] = base_cost["expected"]
        base_cost["execution_cost_unknown_reason"] = (
            None
            if execution_billing_mode == "API_METERED"
            else "execution_billing_mode_unresolved"
        )
        result.update({
            "status": "known",
            "unit_rates": rates,
            "pricing_snapshot": f"{snapshot.get('provider')}/{snapshot.get('model')}@{snapshot.get('effective_at')}",
            "source": source,
            "pricing_source": snapshot.get("source"),
            "base_cost_prior": base_cost,
        })
        result["api_equivalent_task_cost"] = {
            "low": base_cost["low"],
            "expected": base_cost["expected"],
            "high": base_cost["high"],
            "source": "base_workload_prior",
            "basis": "catalog_api_rate",
        }
        if not has_local_cash_cost and execution_billing_mode == "API_METERED":
            result["typical_task_cost"] = {
                "low": base_cost["low"],
                "expected": base_cost["expected"],
                "high": base_cost["high"],
                "source": "base_workload_prior",
            }
        execution_cost = result["typical_task_cost"]
        if has_local_cash_cost:
            result["execution_task_cost"] = {
                "low": execution_cost["low"],
                "expected": execution_cost["expected"],
                "high": execution_cost["high"],
                "source": "local_task_observations",
                "billing_mode": execution_billing_mode,
                "cash_cost_status": "measured",
                "unknown_reason": None,
            }
        elif execution_billing_mode == "API_METERED":
            result["execution_task_cost"] = {
                "low": execution_cost["low"],
                "expected": execution_cost["expected"],
                "high": execution_cost["high"],
                "source": execution_cost["source"],
                "billing_mode": execution_billing_mode,
                "cash_cost_status": "estimated",
                "unknown_reason": None,
            }
        else:
            result["execution_task_cost"] = {
                "low": None,
                "expected": None,
                "high": None,
                "source": None,
                "billing_mode": execution_billing_mode,
                "cash_cost_status": "unknown",
                "unknown_reason": "execution_billing_mode_unresolved",
            }
    if result["typical_task_cost"]["expected"] is None and execution_billing_mode != "API_METERED":
        result["unknown_reason"] = "execution_billing_mode_unresolved"
    elif result["typical_task_cost"]["expected"] is None and result["unknown_reason"] is None:
        result["unknown_reason"] = "no_pricing_snapshot"
    return result


def _cost_to_accept(cost: float | None, quality: float | None) -> float | None:
    if cost is None or quality is None or quality <= 0:
        return None
    return round(cost / quality, 6)


def _capability(
    store: Path,
    route: dict[str, Any],
    domain: str,
    declared: list[str],
) -> dict[str, Any]:
    # A missing benchmark domain is deliberately unavailable.  ``general`` in
    # external_baseline means "all domains" for legacy ranking and would make
    # a coding result look like a research or planning result here.
    external_domain = domain
    external = external_baseline(
        store,
        model_identity=route["identity"],
        agent_id=str(route["agent_id"]),
        domain=external_domain,
        allow_model_family=True,
    )
    base = _base_prior(external)
    local = _local_stats(store, route, domain)
    fused = _fused_quality(base, local)
    cost = _pricing_profile(store, route, local, domain=domain)
    duration = local["duration_s"]
    cost["cost_to_accept"] = _cost_to_accept(cost["typical_task_cost"].get("expected"), fused.get("score"))
    cost["api_equivalent_cost_to_accept"] = _cost_to_accept(
        (cost.get("api_equivalent_task_cost") or {}).get("expected"), fused.get("score")
    )
    cost["execution_cost_to_accept"] = _cost_to_accept(
        (cost.get("execution_task_cost") or {}).get("expected"), fused.get("score")
    )
    cost["time_to_accept_s"] = _round_or_none(
        duration.get("p50") / fused["score"] if duration.get("p50") is not None and fused.get("score") else None,
        1,
    )
    return {
        "domain": domain,
        "declared_capabilities": [cap for cap in declared if cap],
        "base_prior": base,
        "local_experience": local,
        "quality": fused,
        "duration_s": duration,
        "intervention": local.get("intervention"),
        "cost": cost,
        "evidence_coverage": _evidence_coverage(base, local, cost),
        "evidence_status": (
            "base_prior+local" if base.get("score") is not None and local.get("sample_count") else
            "local_measured" if local.get("sample_count") else
            "base_prior" if base.get("score") is not None else
            "declared_only" if declared else "unavailable"
        ),
    }


def _agent_shell_evidence(store: Path, agent_id: str) -> dict[str, int]:
    """Legacy evidence is informative but not exact Agent + model evidence."""
    marks = [item for item in _json_files(store / "marks") if item.get("agent_id") == agent_id]
    ratings = [item for item in _json_files(store / "ratings") if item.get("agent_id") == agent_id]
    replays = [
        item for item in _json_files(store / "replays", "*/replay.json")
        if ((item.get("target_agent_revision") or {}).get("agent_id") == agent_id)
    ]
    return {
        "marks": len(marks),
        "ratings": len(ratings),
        "replays": len(replays),
        "total": len(marks) + len(ratings) + len(replays),
        "scope": "agent_shell",
    }


def _evidence_coverage(
    base: dict[str, Any],
    local: dict[str, Any],
    cost: dict[str, Any],
) -> dict[str, Any]:
    """Make missing evidence explicit without promoting weaker evidence."""
    available: list[str] = []
    missing: list[str] = []
    if base.get("score") is not None:
        available.append("public_prior")
    else:
        missing.append("public_prior")
    if local.get("sample_count", 0) > 0:
        available.append("exact_model_observation")
    else:
        missing.append("exact_model_observation")
    if (cost.get("api_equivalent_task_cost") or {}).get("expected") is not None:
        available.append("api_equivalent_cost")
    else:
        missing.append("api_equivalent_cost")
    if (cost.get("execution_task_cost") or {}).get("expected") is not None:
        available.append("execution_cost")
    else:
        missing.append("execution_cost")
    if local.get("duration_s", {}).get("p50") is not None:
        available.append("duration")
    else:
        missing.append("duration")
    return {
        "available": available,
        "missing": missing,
        "public_prior_records": len(base.get("records") or []),
        "exact_model_observations": local.get("sample_count", 0),
        "status": "complete" if not missing else "partial" if available else "missing",
    }


def _decision_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [item for item in capabilities if item["quality"].get("score") is not None]
    measured.sort(key=lambda item: float(item["quality"].get("score") or 0), reverse=True)
    cost_known = [
        item for item in capabilities
        if (item["cost"].get("api_equivalent_task_cost") or {}).get("expected") is not None
    ]
    return {
        "measured_domains": [item["domain"] for item in measured],
        "strongest_domain": measured[0]["domain"] if measured else None,
        "cost_profile_domains": [item["domain"] for item in cost_known],
        "unresolved_domains": [item["domain"] for item in capabilities if item["quality"].get("score") is None],
        "base_prior_domains": [item["domain"] for item in capabilities if item["base_prior"].get("score") is not None],
        "exact_model_domains": [item["domain"] for item in capabilities if item["local_experience"].get("sample_count", 0) > 0],
        "api_cost_domains": [item["domain"] for item in capabilities if (item["cost"].get("api_equivalent_task_cost") or {}).get("expected") is not None],
        "execution_cost_domains": [item["domain"] for item in capabilities if (item["cost"].get("execution_task_cost") or {}).get("expected") is not None],
        "evidence_status_counts": {
            status: sum(1 for item in capabilities if item.get("evidence_status") == status)
            for status in {item.get("evidence_status") for item in capabilities}
        },
        "explanation": (
            "Evidence-backed capability domains are shown separately; unknown domains have no reliable score."
            if measured else
            "No numeric capability prior or exact local model evidence is available yet."
        ),
    }


def build_agent_profile(
    store: str | Path,
    *,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, explainable Agent + model decision profile."""
    store = Path(store)
    metadata = metadata or {}
    route = _target_route(store, agent_id, metadata)
    declared = list(metadata.get("capabilities") or [])
    capabilities = [_capability(store, route, domain, declared) for domain in CAPABILITY_DOMAINS]
    shell_evidence = _agent_shell_evidence(store, agent_id)
    decision_summary = _decision_summary(capabilities)
    from .evolution import load_measurement

    measurement = load_measurement(store, route)
    return {
        "schema_version": SCHEMA,
        "profile_version": PROFILE_VERSION,
        "agent_id": agent_id,
        "model_identity": route["identity"],
        "route": {
            "agent": route["agent"],
            "provider": route.get("provider"),
            "provider_id": route.get("provider_id"),
            "model": route.get("model"),
        },
        "declared_capabilities": declared,
        "capabilities": capabilities,
        "measurement": measurement,
        "agent_shell_evidence": shell_evidence,
        "coverage": {
            "domain_count": len(capabilities),
            "domains_with_public_prior": decision_summary["base_prior_domains"],
            "domains_with_exact_model_experience": decision_summary["exact_model_domains"],
            "domains_with_api_cost": decision_summary["api_cost_domains"],
            "domains_with_execution_cost": decision_summary["execution_cost_domains"],
            "agent_shell_evidence_count": shell_evidence["total"],
            "exact_model_observation_count": sum(
                item["local_experience"].get("sample_count", 0) for item in capabilities
            ),
        },
        "decision_summary": decision_summary,
        "generated_at": _now(),
    }


def estimate_agent_task_cost(
    store: str | Path,
    *,
    agent_id: str,
    task_profile: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only task quote for one exact executable Agent + Model target.

    The quote is a deterministic projection.  It does not create a Plan,
    authorize execution, write CostActual, or mutate historical evidence.
    """
    profile = task_profile or {}
    requested_domain = _domain_for_task(profile)
    domain = requested_domain if requested_domain in BASE_WORKLOADS else "coding"
    difficulty = str(profile.get("difficulty") or "D2")
    if difficulty not in {"D1", "D2", "D3", "D4", "D5"}:
        difficulty = "D2"
    decision = build_agent_profile(
        store,
        agent_id=agent_id,
        metadata=metadata,
    )
    capability = next(
        item for item in decision["capabilities"] if item["domain"] == domain
    )
    cost = capability["cost"]
    base = cost.get("base_cost_prior") or {}
    level = (base.get("levels") or {}).get(difficulty) or {}
    api_equivalent = level.get("catalog_cash_cost")
    execution_mode = cost.get("execution_billing_mode") or "UNKNOWN"
    local_measured = (capability.get("local_experience") or {}).get("known_cost") or {}
    local_p50 = local_measured.get("p50")
    if local_p50 is not None:
        execution_cost = local_p50
        execution_status = "measured"
        execution_source = "local_task_observations"
        unknown_reason = None
    elif execution_mode == "API_METERED" and api_equivalent is not None:
        execution_cost = api_equivalent
        execution_status = "estimated"
        execution_source = "catalog_tariff_x_standard_workload"
        unknown_reason = None
    else:
        execution_cost = None
        execution_status = "unknown"
        execution_source = None
        unknown_reason = (
            cost.get("unknown_reason")
            or (cost.get("execution_task_cost") or {}).get("unknown_reason")
            or "execution_billing_mode_unresolved"
        )
    quality = (capability.get("quality") or {}).get("score")
    return {
        "schema_version": "icle-agent-task-cost-estimate/v0.1",
        "agent_id": agent_id,
        "provider": cost.get("provider"),
        "model": cost.get("model"),
        "domain": domain,
        "requested_domain": requested_domain,
        "domain_assumption": (
            None if requested_domain == domain else "coding_default_for_unmapped_task_type"
        ),
        "difficulty": difficulty,
        "workload": {
            "input_tokens": level.get("input_tokens"),
            "output_tokens": level.get("output_tokens"),
            "schema_version": base.get("schema_version"),
            "version": base.get("workload_version"),
            "source": "standard_workload_assumption" if level else None,
        },
        "unit_rates": cost.get("unit_rates"),
        "api_equivalent_cost": api_equivalent,
        "execution_cost": execution_cost,
        "execution_cost_status": execution_status,
        "execution_cost_source": execution_source,
        "execution_billing_mode": execution_mode,
        "local_measured_task_cost_p50": local_p50,
        "quality": quality,
        "api_equivalent_cost_to_accept": _cost_to_accept(api_equivalent, quality),
        "execution_cost_to_accept": _cost_to_accept(execution_cost, quality),
        "pricing_snapshot": cost.get("pricing_snapshot"),
        "pricing_source": cost.get("pricing_source"),
        "unknown_reason": unknown_reason,
        "generated_at": _now(),
    }


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    """Small directory payload; full evidence stays on the profile page."""
    summary = profile.get("decision_summary") or {}
    measurement = profile.get("measurement") or {}
    return {
        "measured_domains": summary.get("measured_domains", []),
        "strongest_domain": summary.get("strongest_domain"),
        "cost_profile_domains": summary.get("cost_profile_domains", []),
        "unresolved_domains": summary.get("unresolved_domains", []),
        "profile_version": profile.get("profile_version"),
        "measurement_status": measurement.get("publication_status"),
        "observed_axis_count": measurement.get("observed_axis_count"),
    }
