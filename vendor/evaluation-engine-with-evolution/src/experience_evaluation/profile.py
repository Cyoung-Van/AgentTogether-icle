"""Read-only subject portrait from C state. No total score and no ranking."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .canonical_measurement import AXIS_CONTRACT_ID, CANONICAL_AXES, CANONICAL_SCALE_ID
from .c_state import CStateStore
from .public_dataset import DatasetError, stable_record_id


PROFILE_SCHEMA = "experience-evaluation-subject-portrait/v0.1"
FORBIDDEN_TOTAL_KEYS = {
    "total",
    "total_score",
    "overall",
    "overall_score",
    "rank",
    "leaderboard_score",
}


def validate_publication_policy(policy: dict[str, Any]) -> dict[str, Any]:
    if policy.get("emit_total_score") is not False:
        raise DatasetError("profile policy must set emit_total_score=false")
    if policy.get("emit_ranking") is not False:
        raise DatasetError("profile policy must set emit_ranking=false")
    if policy.get("missing_equals_zero") is not False:
        raise DatasetError("profile policy must set missing_equals_zero=false")
    allowed = policy.get("allowed_publication_scopes")
    if not isinstance(allowed, list) or not allowed:
        raise DatasetError("profile policy requires allowed_publication_scopes")
    return policy


def _axis_projection(state: Optional[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    if state is None:
        return {
            "status": "unavailable",
            "mean": None,
            "sd": None,
            "ci_low": None,
            "ci_high": None,
            "evidence_count": 0,
            "reason": "no_c_state",
        }
    evidence_count = int(state.get("evidence_count") or 0)
    min_evidence = int(policy.get("min_evidence_count") or 1)
    if evidence_count < min_evidence:
        return {
            "status": "insufficient_evidence",
            "mean": None,
            "sd": None,
            "ci_low": None,
            "ci_high": None,
            "evidence_count": evidence_count,
            "reason": "insufficient_evidence",
        }
    return {
        "status": "observed",
        "mean": state["mean"],
        "sd": state["sd"],
        "ci_low": state["ci_low"],
        "ci_high": state["ci_high"],
        "evidence_count": evidence_count,
        "last_observed_at": state.get("last_observed_at"),
        "publication_scope": state.get("latest_publication_scope"),
        "evidence_origin": state.get("latest_evidence_origin"),
        "warnings": list(state.get("latest_warnings") or []),
    }


def _publication_status(axes: dict[str, dict[str, Any]], policy: dict[str, Any]) -> str:
    allowed = set(policy["allowed_publication_scopes"])
    forbidden = set(policy.get("forbidden_publication_scopes") or [])
    observed = [row for row in axes.values() if row["status"] == "observed"]
    if not observed:
        return "insufficient_evidence"
    scopes = {row.get("publication_scope") for row in observed}
    if None in scopes or "" in scopes:
        return "shadow_only"
    if scopes & forbidden:
        return "shadow_only"
    if not scopes <= allowed:
        return "shadow_only"
    return "publishable_local_portrait"


def render_subject_profile(
    store: CStateStore,
    subject_id: str,
    policy: dict[str, Any],
) -> dict[str, Any]:
    policy = validate_publication_policy(policy)
    states = {
        row["dimension_id"]: row
        for row in store.list_states()
        if row.get("subject_id") == subject_id
    }
    axes = {
        dimension: _axis_projection(states.get(dimension), policy)
        for dimension in CANONICAL_AXES
    }
    publication_status = _publication_status(axes, policy)
    portrait = {
        "schema_version": PROFILE_SCHEMA,
        "portrait_id": stable_record_id("portrait", policy.get("policy_id"), subject_id),
        "portrait_kind": policy.get("portrait_kind", "subject_residual_c"),
        "policy_id": policy.get("policy_id"),
        "subject_id": subject_id,
        "axis_contract_id": AXIS_CONTRACT_ID,
        "scale_id": CANONICAL_SCALE_ID,
        "publication_status": publication_status,
        "ranking": False,
        "axes": axes,
        "observed_axis_count": sum(row["status"] == "observed" for row in axes.values()),
        "unavailable_axis_count": sum(row["status"] != "observed" for row in axes.values()),
        "not_claimed": list(policy.get("not_claimed") or []),
    }
    leaked = FORBIDDEN_TOTAL_KEYS & set(portrait)
    if leaked:
        raise DatasetError(f"portrait leaked forbidden aggregate keys: {sorted(leaked)}")
    if portrait.get("ranking") is not False:
        raise DatasetError("portrait ranking must be false")
    return portrait


def render_profiles(
    store: CStateStore,
    policy: dict[str, Any],
    *,
    subject_ids: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    policy = validate_publication_policy(policy)
    available_subjects = sorted({row["subject_id"] for row in store.list_states()})
    selected = list(subject_ids) if subject_ids is not None else available_subjects
    portraits = [render_subject_profile(store, subject_id, policy) for subject_id in selected]
    return {
        "schema_version": "experience-evaluation-portrait-bundle/v0.1",
        "policy_id": policy.get("policy_id"),
        "ranking": False,
        "subject_count": len(portraits),
        "portraits": portraits,
    }
