"""Calibrate Matrix A from a same-TaskSpec baseline-model probe.

This is the only approved way to cover axes that public LiveBench does not map.
It does not invent A, does not map unapproved LiveBench categories, and is
common-item only with the TaskSpecs used in the same pipeline run.
"""

from __future__ import annotations

from typing import Any, Sequence

from .canonical_measurement import AXIS_CONTRACT_ID, CANONICAL_AXES, CANONICAL_SCALE_ID, PROBABILITY_SCALE_ID
from .evaluation_matrix import generate_evaluation_matrix
from .public_dataset import DatasetError, stable_record_id


LOCAL_PROBE_GENERATOR = "experience-evaluation-local-probe-a/v0.1"
BASELINE_ROLE = "baseline_model"


def baseline_subject_ids(identities: Sequence[dict[str, Any]]) -> set[str]:
    ids = set()
    for identity in identities:
        if identity.get("role") != BASELINE_ROLE:
            continue
        subject_id = identity.get("subject_id")
        if not isinstance(subject_id, str) or not subject_id:
            raise DatasetError("baseline_model identity requires subject_id")
        ids.add(subject_id)
    return ids


def split_results_by_role(
    execution_results: Sequence[dict[str, Any]],
    identities: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    baseline_ids = baseline_subject_ids(identities)
    agent_results = []
    baseline_results = []
    for row in execution_results:
        subject_id = row.get("subject_id")
        if subject_id in baseline_ids:
            baseline_results.append(row)
        else:
            agent_results.append(row)
    return agent_results, baseline_results


def model_match_key(identity: dict[str, Any], resolved_model: dict[str, Any]) -> tuple[str, str] | None:
    config_id = resolved_model.get("model_config_id")
    if isinstance(config_id, str) and config_id:
        return ("config", config_id)
    raw = (
        identity.get("model_config_id")
        or identity.get("model_revision")
        or identity.get("model_id")
        or identity.get("model")
    )
    provider = identity.get("provider") or identity.get("model_provider")
    if not isinstance(raw, str) or not raw:
        return None
    from .public_dataset import canonical_model_id

    return ("raw", canonical_model_id(raw, provider if isinstance(provider, str) else None))


def overlay_local_a(
    public_lookup: dict[str, Any],
    local_cells: Sequence[dict[str, Any]],
    dimensions: Sequence[str],
) -> dict[str, Any]:
    """Prefer same-TaskSpec local A; keep public calibrated A only for leftover axes."""
    by_dimension: dict[str, dict[str, Any]] = {}
    sources: dict[str, str] = {}
    for cell in public_lookup.get("cells", []):
        if cell.get("calibration_status") != "calibrated_for_c":
            continue
        if cell.get("dimension_id") not in dimensions:
            continue
        by_dimension[cell["dimension_id"]] = cell
        sources[cell["dimension_id"]] = str(
            cell.get("calibration_contract_id") or "public_calibrated_a"
        )
    for cell in local_cells:
        dimension = cell.get("dimension_id")
        if dimension not in dimensions:
            continue
        if cell.get("calibration_status") != "calibrated_for_c":
            continue
        by_dimension[dimension] = cell
        sources[dimension] = str(cell.get("calibration_contract_id") or "local_probe_same_taskspec")
    cells = [by_dimension[dimension] for dimension in dimensions if dimension in by_dimension]
    missing = [dimension for dimension in dimensions if dimension not in by_dimension]
    if cells and not missing:
        status = "available"
    elif cells:
        status = "partial"
    else:
        status = "unavailable"
    reasons = list(public_lookup.get("reasons") or [])
    reasons = [reason for reason in reasons if not str(reason).startswith("missing_dimension:")]
    reasons.extend(f"missing_dimension:{item}" for item in missing)
    if local_cells:
        reasons.append("local_probe_same_taskspec_overlaid")
    return {
        "status": status,
        "cells": cells,
        "reasons": sorted(set(reasons)),
        "a_sources": sources,
        "model_config_id": public_lookup.get("model_config_id"),
    }


def calibrate_local_probe_a(
    task_specs: Sequence[dict[str, Any]],
    baseline_results: Sequence[dict[str, Any]],
    generator_config: dict[str, Any],
    contract: dict[str, Any],
    *,
    baseline_subject_id: str,
    model_config_id: str,
) -> dict[str, Any]:
    """Build calibrated A cells from a baseline_model J on the same TaskSpecs."""
    contract_id = contract.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id:
        raise DatasetError("local probe A contract requires contract_id")
    min_observed_tasks = int(contract.get("min_observed_tasks", 2))
    if min_observed_tasks < 2:
        raise DatasetError("local probe A min_observed_tasks must be at least 2")
    if not baseline_results:
        raise DatasetError("local probe A requires baseline_model execution results")
    unexpected = {
        row.get("subject_id") for row in baseline_results if row.get("subject_id") != baseline_subject_id
    }
    if unexpected:
        raise DatasetError(f"local probe A results include non-baseline subjects: {sorted(unexpected)}")
    j_bundle = generate_evaluation_matrix(task_specs, baseline_results, generator_config)
    cells = []
    rejected = []
    for cell in j_bundle["evaluation_matrix"]:
        if cell.get("subject_id") != baseline_subject_id:
            continue
        dimension = cell.get("dimension_id")
        if dimension not in CANONICAL_AXES:
            rejected.append({"dimension_id": dimension, "reason": "dimension_not_canonical"})
            continue
        if cell.get("publish_status") != "publishable":
            rejected.append({
                "dimension_id": dimension,
                "reason": "j_not_publishable",
                "publish_status": cell.get("publish_status"),
            })
            continue
        if cell.get("value") is None or cell.get("canonical_value") is None:
            rejected.append({"dimension_id": dimension, "reason": "j_value_unavailable"})
            continue
        observed_tasks = int(cell.get("observed_task_count") or 0)
        if observed_tasks < min_observed_tasks:
            rejected.append({
                "dimension_id": dimension,
                "reason": "insufficient_items",
                "observed_task_count": observed_tasks,
            })
            continue
        if cell.get("canonical_stderr") is None or cell.get("stderr") is None:
            rejected.append({
                "dimension_id": dimension,
                "reason": "j_stderr_unavailable",
                "uncertainty_status": cell.get("uncertainty_status"),
            })
            continue
        cells.append({
            "schema_version": "experience-evaluation-capability-baseline/v0.1",
            "baseline_id": stable_record_id(
                "a-local-probe", LOCAL_PROBE_GENERATOR, contract_id, model_config_id, dimension
            ),
            "model_config_id": model_config_id,
            "dimension_id": dimension,
            "value": cell["value"],
            "stderr": cell["stderr"],
            "scale_id": PROBABILITY_SCALE_ID,
            "scale_role": "probability_level",
            "axis_contract_id": AXIS_CONTRACT_ID,
            "calibration_status": "calibrated_for_c",
            "evidence_tier": contract.get("evidence_tier", "local_probe_same_taskspec"),
            "calibration_contract_id": contract_id,
            "generator_version": LOCAL_PROBE_GENERATOR,
            "item_count": observed_tasks,
            "source_record_ids": list(cell.get("source_objective_row_ids") or []),
            "canonical_scale_id": CANONICAL_SCALE_ID,
            "canonical_value": cell["canonical_value"],
            "canonical_stderr": cell["canonical_stderr"],
            "boundary_clipped": cell.get("boundary_clipped", False),
            "task_spec_content_sha256": j_bundle["summary"]["task_spec_content_sha256"],
            "publication_scope": cell.get("publication_scope"),
            "evidence_origin": cell.get("evidence_origin"),
        })
    present = {row["dimension_id"] for row in cells}
    return {
        "schema_version": "experience-evaluation-local-probe-a-report/v0.1",
        "generator_version": LOCAL_PROBE_GENERATOR,
        "contract_id": contract_id,
        "baseline_subject_id": baseline_subject_id,
        "model_config_id": model_config_id,
        "cells": cells,
        "rejected_dimensions": rejected,
        "approved_dimensions": sorted(present),
        "cell_count": len(cells),
        "task_spec_content_sha256": j_bundle["summary"]["task_spec_content_sha256"],
        "j_summary": j_bundle["summary"],
    }
