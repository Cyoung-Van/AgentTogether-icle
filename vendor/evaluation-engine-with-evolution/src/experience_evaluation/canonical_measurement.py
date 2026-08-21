"""Canonical seven-axis and additive scale contracts for A/B/J/C."""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from .public_dataset import DatasetError


CANONICAL_AXES = (
    "reasoning",
    "coding",
    "agentic_coding",
    "mathematics",
    "data_analysis",
    "language",
    "instruction_following",
)
AXIS_CONTRACT_ID = "livebench-capability-seven/v0.1"
CANONICAL_SCALE_ID = "canonical_logit/v0.1"
PROBABILITY_SCALE_ID = "normalized_0_1"
LOGIT_EPSILON = 1e-6

# Single source of truth for the LiveBench category -> canonical axis mapping.
# A calibration contracts may restrict this map but must not invent categories.
LIVEBENCH_CATEGORY_AXIS = {
    "Reasoning": "reasoning",
    "Coding": "coding",
    "Agentic Coding": "agentic_coding",
    "Mathematics": "mathematics",
    "Data Analysis": "data_analysis",
    "Language": "language",
    "IF": "instruction_following",
}


def validate_category_axis_map(category_map: Any) -> dict[str, str]:
    """Check a contract category map against the frozen LiveBench mapping."""
    if not isinstance(category_map, dict) or not category_map:
        raise DatasetError("category map must be a non-empty object")
    unknown = sorted(set(category_map) - set(LIVEBENCH_CATEGORY_AXIS))
    if unknown:
        raise DatasetError(f"categories are outside {AXIS_CONTRACT_ID}: {unknown}")
    conflicting = sorted(
        category
        for category, axis in category_map.items()
        if axis != LIVEBENCH_CATEGORY_AXIS[category]
    )
    if conflicting:
        raise DatasetError(
            f"category map disagrees with {AXIS_CONTRACT_ID}: {conflicting}"
        )
    validate_axis_subset(sorted(set(category_map.values())))
    return dict(category_map)


def validate_axis_subset(dimensions: Sequence[str]) -> tuple[str, ...]:
    values = tuple(dimensions)
    if not values or len(set(values)) != len(values):
        raise DatasetError("canonical dimensions must be a non-empty unique list")
    unknown = sorted(set(values) - set(CANONICAL_AXES))
    if unknown:
        raise DatasetError(f"dimensions are outside {AXIS_CONTRACT_ID}: {unknown}")
    return values


def align_probability_cell(
    value: float,
    stderr: Optional[float],
    *,
    epsilon: float = LOGIT_EPSILON,
) -> dict[str, Any]:
    """Map a bounded 0–1 level to additive log-odds with delta-method SE."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise DatasetError("probability value must be finite numeric")
    value = float(value)
    if not 0 <= value <= 1:
        raise DatasetError("probability value must be in [0,1]")
    if not 0 < epsilon < 0.5:
        raise DatasetError("logit epsilon must be in (0,0.5)")
    aligned_probability = min(max(value, epsilon), 1 - epsilon)
    boundary_clipped = aligned_probability != value
    canonical_value = math.log(aligned_probability / (1 - aligned_probability))
    if stderr is None:
        canonical_stderr = None
    else:
        if not isinstance(stderr, (int, float)) or stderr < 0 or not math.isfinite(float(stderr)):
            raise DatasetError("probability stderr must be non-negative finite numeric or null")
        canonical_stderr = float(stderr) / (
            aligned_probability * (1 - aligned_probability)
        )
    return {
        "source_scale_id": PROBABILITY_SCALE_ID,
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "scale_transform": "logit",
        "scale_transform_revision": "probability-to-logit-delta/v0.1",
        "source_value": value,
        "source_stderr": stderr,
        "canonical_value": canonical_value,
        "canonical_stderr": canonical_stderr,
        "boundary_clipped": boundary_clipped,
        "epsilon": epsilon,
    }


def align_additive_effect_cell(
    value: float, stderr: Optional[float], scale_id: str
) -> dict[str, Any]:
    """Validate a B effect already expressed on the canonical additive scale."""
    if scale_id != CANONICAL_SCALE_ID:
        raise DatasetError(f"B effect scale must be {CANONICAL_SCALE_ID}")
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise DatasetError("additive effect must be finite numeric")
    if stderr is not None and (
        not isinstance(stderr, (int, float)) or stderr < 0 or not math.isfinite(float(stderr))
    ):
        raise DatasetError("additive effect stderr must be non-negative finite numeric or null")
    return {
        "source_scale_id": scale_id,
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "scale_transform": "identity",
        "scale_transform_revision": "canonical-effect-identity/v0.1",
        "source_value": float(value),
        "source_stderr": stderr,
        "canonical_value": float(value),
        "canonical_stderr": None if stderr is None else float(stderr),
        "boundary_clipped": False,
    }
