"""Experience Router: domain-aware, evidence-based Agent recommendation.

The router is deterministic and read-only.  Quality comes from the
versioned decision loss over A+B+C measurement.  Unknown stays ``None``.
Public A and Agent-shell marks never fabricate a capability score.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .decision import score_route
from .external_evaluation import infer_domain, resolve_model_identity
from .intelligence import similar_episodes
from .replay import list_replays

POLICY_VERSION = "experience-router-v0.4"
QUALITY_WEIGHT = 0.85
SPEED_WEIGHT = 0.15
LOCAL_PRIOR_K = 7.0
MAX_COST_NORMALIZER_MS = 600_000.0
POSITIVE_MARKS = {"accept", "adopted", "adopted_with_minor_edits"}
NEGATIVE_MARKS = {"reject", "redo", "abandoned_result", "requested_rework"}


class RouterError(ValueError):
    pass


def executable_candidates(
    store: str | Path,
    *,
    capture_store: str | Path | None = None,
) -> list[str]:
    """Return exact targets that are currently authorized and executable.

    Discovery/linkage is separate from execution authorization.  Capture-only
    CLIs are excluded; connected Provider Models are keyed by provider/model.
    """
    from .discovery import scan_agents
    from .provider import list_providers

    store = Path(store)
    candidates: list[str] = []
    for item in scan_agents(
        store,
        capture_store=Path(capture_store) if capture_store else None,
        probe=False,
    ):
        if item.get("status") == "linked" and item.get("execution_supported"):
            candidates.append(str(item["agent_type"]))
    for provider in list_providers(store):
        if provider.get("status") != "connected":
            continue
        if provider.get("type") not in {"openai-compatible", "local"}:
            continue
        if provider.get("type") != "local" and not provider.get("configured"):
            continue
        for model in provider.get("models", []):
            model_id = str(model.get("id") or "").strip()
            if model_id:
                candidates.append(f"{provider['provider_id']}/{model_id}")
    return list(dict.fromkeys(candidates))


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return float(ordered[len(ordered) // 2])


def _load_episodes(store: Path) -> list[dict[str, Any]]:
    episodes_dir = store / "episodes"
    if not episodes_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(episodes_dir.glob("ep-*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _load_json_files(directory: Path, pattern: str) -> list[dict[str, Any]]:
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


def _episode_context(episodes: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    context: dict[str, dict[str, str]] = {}
    for episode in episodes:
        episode_id = str(episode.get("episode_id") or "")
        request = str((episode.get("task_start") or {}).get("original_user_request") or "")
        if episode_id:
            context[episode_id] = {
                "domain": infer_domain(request),
                "project_id": str(episode.get("project_id") or ""),
            }
    return context


def _domain_speed_score(
    store: Path,
    *,
    agent: str,
    domain: str,
    context: dict[str, dict[str, str]],
) -> tuple[float | None, float | None]:
    durations = [
        int(entry.get("duration_ms") or 0)
        for entry in list_replays(store)
        if entry.get("agent") == agent
        and entry.get("status") == "completed"
        and (context.get(str(entry.get("episode_id") or "")) or {}).get("domain") == domain
        and int(entry.get("duration_ms") or 0) > 0
    ]
    median_ms = _median(durations)
    if median_ms is None:
        return None, None
    score = 1 - min(median_ms, MAX_COST_NORMALIZER_MS) / MAX_COST_NORMALIZER_MS
    return round(score, 4), median_ms


def _local_quality(
    *,
    agent: str,
    domain: str,
    marks: list[dict[str, Any]],
    ratings: list[dict[str, Any]],
    context: dict[str, dict[str, str]],
    relevant_ids: set[str],
) -> dict[str, Any]:
    domain_ids = {
        episode_id for episode_id, item in context.items() if item.get("domain") == domain
    }
    scoped_ids = relevant_ids or domain_ids
    by_episode: dict[str, list[float]] = {}
    positive = negative = mark_count = rating_count = 0
    for mark in marks:
        episode_id = str(mark.get("episode_id") or "")
        if mark.get("agent_id") != agent or episode_id not in scoped_ids:
            continue
        if mark.get("mark") in POSITIVE_MARKS:
            positive += 1
            mark_count += 1
            by_episode.setdefault(episode_id, []).append(1.0)
        elif mark.get("mark") in NEGATIVE_MARKS:
            negative += 1
            mark_count += 1
            by_episode.setdefault(episode_id, []).append(0.0)
    for rating in ratings:
        episode_id = str(rating.get("episode_id") or "")
        if rating.get("agent_id") != agent or episode_id not in scoped_ids:
            continue
        dimensions = rating.get("dimensions") or {}
        dimension_values = [
            float(value) / 5.0
            for value in dimensions.values()
            if isinstance(value, (int, float)) and 1 <= float(value) <= 5
        ]
        if dimension_values:
            rating_value = sum(dimension_values) / len(dimension_values)
        elif isinstance(rating.get("overall_preference"), (int, float)) and 1 <= float(rating["overall_preference"]) <= 5:
            rating_value = float(rating["overall_preference"]) / 5.0
        else:
            continue
        rating_count += 1
        by_episode.setdefault(episode_id, []).append(rating_value)
    # Correlated mark/rating signals from one Episode become one task sample.
    values = [sum(signals) / len(signals) for signals in by_episode.values() if signals]
    if not values:
        return {
            "score": None,
            "raw_score": None,
            "sample_count": 0,
            "mark_count": 0,
            "rating_count": 0,
            "positive": 0,
            "negative": 0,
            "scope": "none",
        }
    raw = sum(values) / len(values)
    shrink = len(values) / (len(values) + LOCAL_PRIOR_K)
    score = 0.5 + shrink * (raw - 0.5)
    return {
        "score": round(score, 4),
        "raw_score": round(raw, 4),
        "sample_count": len(values),
        "mark_count": mark_count,
        "rating_count": rating_count,
        "positive": positive,
        "negative": negative,
        "scope": "similar_task" if relevant_ids else "task_domain",
    }


def _profile_domain(task_profile: dict[str, Any] | None, task: str) -> str:
    profile = task_profile or {}
    subtype = str(profile.get("subtype") or "").lower()
    primary = str(profile.get("primary_type") or "").upper()
    if subtype in {"refactor", "refactoring"}:
        return "refactoring"
    mapped = {
        "CODING": "coding",
        "SYSTEM_OPERATION": "terminal",
        "SYSTEM_OPERATIONS": "terminal",
        "RESEARCH": "research",
        "PLANNING": "planning",
        "WRITING": "writing",
    }.get(primary)
    return mapped or infer_domain(task)


def recommend_agent(
    store: str | Path,
    *,
    task: str,
    project_id: str | None = None,
    candidates: list[str] | None = None,
    task_profile: dict[str, Any] | None = None,
    limit_alternatives: int = 2,
) -> dict[str, Any]:
    """Recommend an eligible Agent using per-axis measurement.

    Quality is the decision-layer loss over observed C (then J). Agent-shell
    marks stay in uncertainty and cannot create a quality score. Speed only
    breaks quality-aware policy order.
    """
    store = Path(store)
    episodes = _load_episodes(store)
    context = _episode_context(episodes)
    marks = _load_json_files(store / "marks", "m-*.json")
    ratings = _load_json_files(store / "ratings", "r-*.json")
    all_agents = list(dict.fromkeys(candidates)) if candidates is not None else sorted(
        {str(item.get("agent_id")) for item in marks + ratings if item.get("agent_id")}
        | {str(item.get("agent")) for item in list_replays(store) if item.get("agent")}
    )
    if not all_agents:
        raise RouterError("no eligible candidate agents are available")

    domain = _profile_domain(task_profile, task)
    if not task_profile:
        task_profile = {
            "coding": {"primary_type": "CODING"},
            "refactoring": {"primary_type": "CODING", "subtype": "refactor"},
            "terminal": {"primary_type": "SYSTEM_OPERATION"},
            "research": {"primary_type": "RESEARCH"},
            "planning": {"primary_type": "PLANNING"},
            "writing": {"primary_type": "WRITING"},
        }.get(domain)
    similar = similar_episodes(episodes, task, project_id=project_id, limit=20)
    relevant_ids = {
        item["episode_id"]
        for item in similar
        if item["score"] > 0.15
        and (context.get(item["episode_id"]) or {}).get("domain") == domain
    }
    evaluated_episode_ids = {
        str(item.get("episode_id") or "") for item in marks + ratings if item.get("episode_id")
    }
    # Similar text without any outcome is retrieval context, not quality
    # evidence. Fall back to domain-matched evaluated Episodes in that case.
    quality_relevant_ids = relevant_ids & evaluated_episode_ids

    ranking: list[dict[str, Any]] = []
    for agent in all_agents:
        metadata = None
        if "/" in agent:
            provider_id, _, model_id = agent.partition("/")
            metadata = {
                "agent_type": "provider-model",
                "provider_id": provider_id,
                "model_id": model_id,
            }
        shell = _local_quality(
            agent=agent,
            domain=domain,
            marks=marks,
            ratings=ratings,
            context=context,
            relevant_ids=quality_relevant_ids,
        )
        route = {
            "agent": agent,
            "provider_id": (metadata or {}).get("provider_id") or "",
            "model": (metadata or {}).get("model_id") or "",
        }
        signal = score_route(store, route=route, task_profile=task_profile)
        quality_score = signal.get("quality_unit")
        quality_source = signal.get("quality_source") or "none"

        speed_score, median_duration_ms = _domain_speed_score(
            store, agent=agent, domain=domain, context=context
        )
        if quality_score is None:
            score = None
        elif speed_score is None:
            score = round(float(quality_score), 4)
        else:
            score = round(
                QUALITY_WEIGHT * float(quality_score) + SPEED_WEIGHT * speed_score,
                4,
            )

        evidence_parts: list[str] = []
        uncertainty: list[str] = []
        if signal.get("axes_used"):
            evidence_parts.append(
                f"{quality_source}={quality_score:.3f} axes={','.join(item['axis'] for item in signal['axes_used'])}"
            )
        else:
            evidence_parts.append("no observed quality axis")
            if signal.get("reason"):
                uncertainty.append(str(signal["reason"]))
        if signal.get("axes_skipped"):
            uncertainty.append("skipped_axes=" + ",".join(signal["axes_skipped"]))
        if shell["sample_count"]:
            uncertainty.append(
                f"agent_shell={shell['score']:.3f} "
                f"(raw={shell['raw_score']:.3f}, episodes={shell['sample_count']}); "
                "not a capability score"
            )
        if speed_score is not None:
            evidence_parts.append(f"completed_domain_speed={speed_score:.3f}")
        else:
            uncertainty.append("no completed domain-matched duration observation")
        score_source = (
            f"{quality_source}+domain_speed"
            if speed_score is not None and quality_score is not None
            else quality_source
        )
        confidence = (
            "medium" if quality_source == "c_residual" else
            "low" if quality_source in {"j_observed", "mixed_c_j"} else
            "insufficient-data"
        )

        ranking.append({
            "agent": agent,
            "score": score,
            "quality_score": quality_score,
            "model_quality_score": None,
            "local_score": None,
            "local_evidence_count": 0,
            "agent_shell_score": shell["score"],
            "agent_shell_evidence_count": shell["sample_count"],
            "decision": signal,
            "model_identity": resolve_model_identity(store, agent, metadata=metadata),
            "score_source": score_source,
            "confidence": confidence,
            "evidence": "; ".join(evidence_parts),
            "uncertainty": uncertainty,
            "similar_outcomes": shell["mark_count"],
            "relevant_episodes": len(relevant_ids),
            "median_duration_ms": median_duration_ms,
            "exploration": score is None,
        })

    ranking.sort(
        key=lambda item: (
            item["score"] is None,
            -(float(item["score"]) if item["score"] is not None else 0.0),
            item["agent"],
        )
    )
    best = ranking[0]
    recommended = best["agent"] if best["score"] is not None else None
    reason = (
        f"{best['agent']}: domain={domain}, score={best['score']}; "
        f"{best['evidence']}; confidence={best['confidence']}"
        if recommended
        else f"No eligible Agent has observed C/J on the task Q axes for {domain}; exploration candidates are shown without fabricated scores."
    )
    return {
        "schema_version": "icle-router-recommendation/v0.4",
        "policy": POLICY_VERSION,
        "loss_id": "icle-decision-loss/v0.1",
        "weights": {"quality": QUALITY_WEIGHT, "domain_speed": SPEED_WEIGHT, "local_prior_k": LOCAL_PRIOR_K},
        "domain": domain,
        "task": task,
        "recommended": recommended,
        "reason": reason,
        "alternatives": ranking[1 : 1 + limit_alternatives],
        "policy_order": ranking,
        "ranking": ranking,
        "candidate_count": len(ranking),
        "note": "decision-layer policy order over observed C/J; missing stays null; Agent-shell marks are not capability",
    }
