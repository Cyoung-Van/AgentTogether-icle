"""Calibrate current Matrix A observations onto the canonical logit contract."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Sequence

from .canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_SCALE_ID,
    PROBABILITY_SCALE_ID,
    align_probability_cell,
    validate_category_axis_map,
)
from .public_dataset import DatasetError, stable_record_id


A_CALIBRATION_GENERATOR = "experience-evaluation-a-calibrator/v0.1"


def calibrate_a_baselines(
    observations: Sequence[dict[str, Any]],
    contract: dict[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Build calibrated A cells from current-view LiveBench observations only."""
    contract_id = contract.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id:
        raise DatasetError("A calibration contract requires contract_id")
    if contract.get("approved_category_map") is None:
        raise DatasetError("A calibration contract requires approved_category_map")
    category_map = validate_category_axis_map(contract.get("approved_category_map"))
    min_items = int(contract.get("min_items", 2))
    if min_items < 2:
        raise DatasetError("A calibration min_items must be at least 2")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    skipped = []
    for row in observations:
        if row.get("score_status") != "observed":
            skipped.append({"record_id": row.get("record_id"), "reason": "score_not_observed"})
            continue
        if row.get("freshness_status") != "active_current":
            skipped.append({
                "record_id": row.get("record_id"),
                "reason": (
                    "freshness_status_missing"
                    if row.get("freshness_status") is None
                    else "not_active_current"
                ),
            })
            continue
        dimension = category_map.get(str(row.get("category")))
        if dimension is None:
            skipped.append({"record_id": row.get("record_id"), "reason": "category_not_approved"})
            continue
        score = row.get("score")
        if not isinstance(score, (int, float)) or not math.isfinite(float(score)):
            skipped.append({"record_id": row.get("record_id"), "reason": "score_not_finite"})
            continue
        if not 0 <= float(score) <= 100:
            skipped.append({"record_id": row.get("record_id"), "reason": "score_outside_0_100"})
            continue
        model_id = row.get("model_id")
        if not isinstance(model_id, str) or not model_id:
            skipped.append({"record_id": row.get("record_id"), "reason": "model_id_missing"})
            continue
        grouped[(model_id, dimension)].append(row)

    cells = []
    rejected_groups = []
    for (model_id, dimension), rows in sorted(grouped.items()):
        values = [float(row["score"]) / 100.0 for row in rows]
        if len(values) < min_items:
            rejected_groups.append({
                "model_config_id": model_id,
                "dimension_id": dimension,
                "reason": "insufficient_items",
                "item_count": len(values),
            })
            continue
        mean = sum(values) / len(values)
        stderr = statistics.stdev(values) / math.sqrt(len(values))
        aligned = align_probability_cell(mean, stderr)
        cells.append({
            "schema_version": "experience-evaluation-capability-baseline/v0.1",
            "baseline_id": stable_record_id(
                "a-calibrated", A_CALIBRATION_GENERATOR, contract_id, as_of, model_id, dimension
            ),
            "model_config_id": model_id,
            "dimension_id": dimension,
            "value": mean,
            "stderr": stderr,
            "scale_id": PROBABILITY_SCALE_ID,
            "scale_role": "probability_level",
            "axis_contract_id": AXIS_CONTRACT_ID,
            "calibration_status": "calibrated_for_c",
            "evidence_tier": contract.get("evidence_tier", "livebench_current_axis_mean"),
            "calibration_contract_id": contract_id,
            "generator_version": A_CALIBRATION_GENERATOR,
            "item_count": len(values),
            "source_record_ids": [str(row["record_id"]) for row in rows],
            "canonical_scale_id": CANONICAL_SCALE_ID,
            **aligned,
        })
    return {
        "schema_version": "experience-evaluation-a-calibration-report/v0.1",
        "generator_version": A_CALIBRATION_GENERATOR,
        "contract_id": contract_id,
        "as_of": as_of,
        "cells": cells,
        "rejected_groups": rejected_groups,
        "skipped_observations": skipped,
        "model_config_count": len({row["model_config_id"] for row in cells}),
        "cell_count": len(cells),
        "approved_dimensions": sorted(set(category_map.values())),
    }
