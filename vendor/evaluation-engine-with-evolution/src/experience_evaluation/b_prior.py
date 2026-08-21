"""Weak literature prior and evidence-driven specialization for Matrix B."""

from __future__ import annotations

import math
from typing import Any, Sequence

from .canonical_measurement import AXIS_CONTRACT_ID, CANONICAL_SCALE_ID, validate_axis_subset
from .public_dataset import DatasetError, stable_record_id


B_PRIOR_ID = "literature-shrunk-harness-prior/v0.1"
DEFAULT_B_MEAN_LOGIT = 0.15
DEFAULT_B_SD_LOGIT = 0.75
FAMILY_USABLE_B_EVIDENCE_TIERS = frozenset({
    "family_observation",
    "exact_full_gate",
})
REJECTED_B_SPECIALIZATION_TIERS = frozenset({
    "provisional_same_label_blocks",
    "default_literature_prior",
    "official_description_prior",
})


def is_family_usable_b_observation(row: dict[str, Any]) -> bool:
    """Family-level B evidence may specialize a prior; provisional and priors may not."""
    tier = row.get("evidence_tier")
    if tier in REJECTED_B_SPECIALIZATION_TIERS:
        return False
    return tier in FAMILY_USABLE_B_EVIDENCE_TIERS


def build_default_b(
    *,
    harness_id: str,
    harness_revision_id: str | None,
    dimensions: Sequence[str],
) -> list[dict[str, Any]]:
    """Return a deliberately weak positive B prior on C's additive scale."""
    axes = validate_axis_subset(dimensions)
    revision_id = harness_revision_id or f"{harness_id}@default-prior"
    return [{
        "baseline_id": stable_record_id("b-default", B_PRIOR_ID, revision_id, dimension),
        "prior_id": B_PRIOR_ID,
        "harness_id": harness_id,
        "harness_revision_id": revision_id,
        "dimension_id": dimension,
        "value": DEFAULT_B_MEAN_LOGIT,
        "stderr": DEFAULT_B_SD_LOGIT,
        "canonical_value": DEFAULT_B_MEAN_LOGIT,
        "canonical_stderr": DEFAULT_B_SD_LOGIT,
        "scale_id": CANONICAL_SCALE_ID,
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "scale_role": "additive_effect",
        "axis_contract_id": AXIS_CONTRACT_ID,
        "calibration_status": "default_prior_for_c",
        "evidence_tier": "default_literature_prior",
        "uncertainty_kind": "prior_sd",
    } for dimension in axes]


def specialize_b(
    prior: dict[str, Any], observed: dict[str, Any]
) -> dict[str, Any]:
    """Shrink a family-usable B observation toward the weak or document prior."""
    if prior.get("dimension_id") != observed.get("dimension_id"):
        raise DatasetError("B specialization dimension mismatch")
    if observed.get("scale_id") != CANONICAL_SCALE_ID:
        raise DatasetError("observed B baseline is not on canonical logit scale")
    if not is_family_usable_b_observation(observed):
        raise DatasetError("only family-usable B observations can specialize the prior")
    observed_se = observed.get("stderr")
    if not isinstance(observed_se, (int, float)) or observed_se <= 0 or not math.isfinite(float(observed_se)):
        raise DatasetError("observed B baseline requires positive finite stderr")
    prior_mean = float(prior["canonical_value"])
    prior_sd = float(prior["canonical_stderr"])
    observed_mean = float(observed["value"])
    observed_variance = float(observed_se) ** 2
    prior_variance = prior_sd ** 2
    posterior_variance = 1.0 / (1.0 / prior_variance + 1.0 / observed_variance)
    posterior_mean = posterior_variance * (
        prior_mean / prior_variance + observed_mean / observed_variance
    )
    posterior_sd = math.sqrt(posterior_variance)
    return {
        **prior,
        "baseline_id": stable_record_id(
            "b-specialized", prior["baseline_id"], observed["baseline_id"]
        ),
        "value": posterior_mean,
        "stderr": posterior_sd,
        "canonical_value": posterior_mean,
        "canonical_stderr": posterior_sd,
        "calibration_status": "specialized_for_c",
        "evidence_tier": "family_observation_shrunk_to_prior",
        "uncertainty_kind": "posterior_sd_normal_normal",
        "prior_baseline_id": prior["baseline_id"],
        "observed_baseline_id": observed["baseline_id"],
        "observed_value": observed_mean,
        "observed_stderr": float(observed_se),
        "observed_evidence_tier": observed.get("evidence_tier"),
    }
