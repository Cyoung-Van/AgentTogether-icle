"""Synthetic-only validation path for the deterministic A/B/J/C contracts.

This module adapts persisted known-parameter simulation responses into the
ordinary TaskSpec/ExecutionResult contract. It deliberately keeps the path
separate from the real A/B registry: simulated results validate computation,
not real evidence, calibration, or agent ranking.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

from .canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_AXES,
    CANONICAL_SCALE_ID,
    align_additive_effect_cell,
    align_probability_cell,
    validate_axis_subset,
)
from .evaluation_matrix import generate_evaluation_matrix
from .matrix_pipeline import _align_j_cell, generate_c_matrix
from .public_dataset import DatasetError, sha256_file, stable_record_id


SYNTHETIC_AXIS_PROBE_CONTRACT_ID = "synthetic-axis-probe/v0.1"
SYNTHETIC_PUBLICATION_SCOPE = "synthetic_only"
SYNTHETIC_EVIDENCE_ORIGIN = "known_parameter_simulation"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetError(f"cannot read JSONL {path}: {exc}") from exc
    rows = []
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSONL {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise DatasetError(f"JSONL row must be an object: {path}:{lineno}")
        rows.append(value)
    return rows


def load_known_parameter_responses(
    path: Path, *, simulation_replicate: Optional[int] = 0
) -> list[dict[str, Any]]:
    """Load persisted known-parameter responses without treating truth as data."""
    rows = read_jsonl(path)
    selected = [
        row for row in rows
        if simulation_replicate is None
        or row.get("simulation_replicate") == simulation_replicate
    ]
    if not selected:
        scope = "all replicates" if simulation_replicate is None else f"replicate {simulation_replicate}"
        raise DatasetError(f"no known-parameter responses found for {scope}: {path}")
    required = (
        "simulation_response_id",
        "group_id",
        "task_id",
        "outcome",
        "evidence_origin",
        "simulation_method",
        "simulation_seed",
        "simulation_replicate",
    )
    seen = set()
    for row in selected:
        missing = [key for key in required if key not in row]
        if missing:
            raise DatasetError(f"known-parameter response missing fields {missing}")
        response_id = row["simulation_response_id"]
        if response_id in seen:
            raise DatasetError(f"duplicate known-parameter response: {response_id}")
        seen.add(response_id)
        if row["evidence_origin"] != SYNTHETIC_EVIDENCE_ORIGIN:
            raise DatasetError("synthetic validation requires known_parameter_simulation evidence")
        if row["simulation_method"] != "known_parameter_logit_additive":
            raise DatasetError("synthetic validation requires known_parameter_logit_additive rows")
        if row["outcome"] not in (0, 1):
            raise DatasetError("known-parameter outcome must be binary")
    return sorted(selected, key=lambda row: (str(row["group_id"]), str(row["task_id"]), int(row["simulation_replicate"])))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def validate_known_parameter_contract(
    rows: Sequence[dict[str, Any]], known_parameters: dict[str, Any]
) -> dict[str, Any]:
    """Audit the persisted synthetic truth before using outcomes as inputs."""
    if known_parameters.get("generator") != "known_parameter_logit_additive/v0.1":
        raise DatasetError("unsupported known-parameter generator")
    if known_parameters.get("formula") != "logit(p_gt)=intercept+group_effect_g-task_difficulty_t":
        raise DatasetError("known-parameter formula does not match the synthetic contract")
    group_effects = known_parameters.get("group_effects")
    task_difficulties = known_parameters.get("task_difficulties")
    intercept = known_parameters.get("intercept")
    if not isinstance(group_effects, dict) or not isinstance(task_difficulties, dict):
        raise DatasetError("known parameters require group_effects and task_difficulties")
    if not isinstance(intercept, (int, float)) or not math.isfinite(float(intercept)):
        raise DatasetError("known parameters require finite intercept")
    if not group_effects or not task_difficulties:
        raise DatasetError("known parameters require non-empty group and task maps")
    group_values = [float(value) for value in group_effects.values()]
    task_values = [float(value) for value in task_difficulties.values()]
    if any(not math.isfinite(value) for value in group_values + task_values):
        raise DatasetError("known effects and difficulties must be finite")
    if abs(sum(group_values) / len(group_values)) > 1e-12:
        raise DatasetError("known group effects violate mean-zero identifiability constraint")
    if abs(sum(task_values) / len(task_values)) > 1e-12:
        raise DatasetError("known task difficulties violate mean-zero identifiability constraint")
    observed_groups = {str(row.get("group_id")) for row in rows}
    observed_tasks = {str(row.get("task_id")) for row in rows}
    if observed_groups != set(group_effects):
        raise DatasetError("known responses do not cover exactly the known group effects")
    if observed_tasks != set(task_difficulties):
        raise DatasetError("known responses do not cover exactly the known task difficulties")
    replicate_sets: dict[tuple[str, str], set[int]] = defaultdict(set)
    seen_pairs: set[tuple[str, str, int]] = set()
    max_linear_predictor_error = 0.0
    max_probability_error = 0.0
    for row in rows:
        group_id = str(row["group_id"])
        task_id = str(row["task_id"])
        replicate = int(row["simulation_replicate"])
        key = (group_id, task_id, replicate)
        if key in seen_pairs:
            raise DatasetError(f"duplicate known response cell: {key}")
        seen_pairs.add(key)
        replicate_sets[(group_id, task_id)].add(replicate)
        expected_linear_predictor = (
            float(intercept) + float(group_effects[group_id]) - float(task_difficulties[task_id])
        )
        observed_linear_predictor = row.get("true_linear_predictor")
        observed_probability = row.get("success_probability")
        if not isinstance(observed_linear_predictor, (int, float)):
            raise DatasetError("known response missing true_linear_predictor")
        if not isinstance(observed_probability, (int, float)):
            raise DatasetError("known response missing success_probability")
        max_linear_predictor_error = max(
            max_linear_predictor_error,
            abs(float(observed_linear_predictor) - expected_linear_predictor),
        )
        max_probability_error = max(
            max_probability_error,
            abs(float(observed_probability) - _sigmoid(expected_linear_predictor)),
        )
    replicate_sets_values = list(replicate_sets.values())
    if not replicate_sets_values or any(value != replicate_sets_values[0] for value in replicate_sets_values):
        raise DatasetError("known responses do not form a complete rectangular replicate design")
    return {
        "status": "passed",
        "formula": known_parameters["formula"],
        "group_count": len(group_effects),
        "task_count": len(task_difficulties),
        "replicate_count": len(replicate_sets_values[0]),
        "response_count": len(rows),
        "max_linear_predictor_error": max_linear_predictor_error,
        "max_probability_error": max_probability_error,
        "mean_group_effect": sum(group_values) / len(group_values),
        "mean_task_difficulty": sum(task_values) / len(task_values),
    }


def build_axis_probe_task_specs(
    task_ids: Sequence[str],
    *,
    dimensions: Sequence[str] = CANONICAL_AXES,
    task_revision: str = SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Create a balanced, one-hot synthetic Q design.

    The assignment is a mechanical validation fixture, not a semantic claim
    about the source benchmark tasks. It is persisted with the output bundle.
    """
    axes = validate_axis_subset(dimensions)
    unique_task_ids = sorted(set(task_ids))
    if not unique_task_ids:
        raise DatasetError("synthetic axis probe requires at least one task")
    mapping = {
        task_id: axes[index % len(axes)]
        for index, task_id in enumerate(unique_task_ids)
    }
    counts = {axis: sum(value == axis for value in mapping.values()) for axis in axes}
    if max(counts.values()) - min(counts.values()) > 1:
        raise DatasetError("synthetic axis probe must be balanced across selected axes")
    task_specs = []
    for task_id in unique_task_ids:
        axis = mapping[task_id]
        task_specs.append({
            "task_id": task_id,
            "task_revision": task_revision,
            "task_family": "synthetic_axis_probe",
            "weight": 1.0,
            "capability_loadings": {axis: 1.0},
            "metrics": [{
                "metric_id": "success",
                "min": 0,
                "max": 1,
                "direction": "maximize",
                "weight": 1.0,
                "required": True,
            }],
            "axis_probe_contract_id": SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
            "mapping_basis": "balanced_round_robin_fixture",
            "semantic_capability_claim": False,
        })
    return task_specs, mapping


def adapt_known_parameter_responses(
    rows: Sequence[dict[str, Any]],
    *,
    task_revision: str,
) -> list[dict[str, Any]]:
    """Adapt synthetic binary responses to ExecutionResult without leakage.

    Truth fields are retained in provenance for audit only. The matrix
    generator reads only ``metrics.success`` when computing J.
    """
    results = []
    for row in rows:
        source_id = str(row["simulation_response_id"])
        result = {
            "result_id": stable_record_id(
                "synthetic-execution-result", source_id, task_revision
            ),
            "subject_id": f"synthetic:{row['group_id']}",
            "task_id": row["task_id"],
            "task_revision": task_revision,
            "status": "completed",
            "metrics": {"success": int(row["outcome"])},
            "provenance": {
                "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
                "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
                "simulation_method": row["simulation_method"],
                "simulation_seed": row["simulation_seed"],
                "simulation_replicate": row["simulation_replicate"],
                "simulation_response_id": source_id,
                "parent_sample_dataset_id": row.get("parent_sample_dataset_id"),
                "success_probability": row.get("success_probability"),
                "true_linear_predictor": row.get("true_linear_predictor"),
                "truth_fields_not_used_for_scoring": [
                    "success_probability", "true_linear_predictor"
                ],
            },
        }
        results.append(result)
    return sorted(results, key=lambda row: row["result_id"])


def _aligned_truth_cell(
    *,
    baseline_id: str,
    dimension: str,
    value: float,
    truth_role: str,
    subject_id: str,
    truth_source: str,
) -> dict[str, Any]:
    aligned = align_additive_effect_cell(value, 0.0, CANONICAL_SCALE_ID)
    return {
        "baseline_id": baseline_id,
        "subject_id": subject_id,
        "dimension_id": dimension,
        "value": float(value),
        "stderr": 0.0,
        "scale_id": CANONICAL_SCALE_ID,
        "scale_role": "additive_effect",
        "calibration_status": "calibrated_for_c",
        "evidence_tier": "synthetic_ground_truth",
        "axis_contract_id": AXIS_CONTRACT_ID,
        "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
        "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
        "truth_role": truth_role,
        "truth_source": truth_source,
        **aligned,
    }


def _expected_projection_from_truth(
    known_parameters: dict[str, Any],
    axis_mapping: dict[str, str],
    dimensions: Sequence[str],
    *,
    replicate_count: int,
) -> dict[str, dict[str, float]]:
    """Compute J's population projection and Bernoulli sampling uncertainty."""
    if replicate_count <= 0:
        raise DatasetError("replicate_count must be positive")
    intercept = float(known_parameters["intercept"])
    group_effects = known_parameters["group_effects"]
    task_difficulties = known_parameters["task_difficulties"]
    tasks_by_axis: dict[str, list[str]] = defaultdict(list)
    for task_id, axis in axis_mapping.items():
        tasks_by_axis[axis].append(task_id)
    expected = {}
    for group_id, group_effect in sorted(group_effects.items()):
        subject_id = f"synthetic:{group_id}"
        expected[subject_id] = {}
        for dimension in dimensions:
            task_ids = tasks_by_axis.get(dimension, [])
            probabilities = [
                _sigmoid(intercept + float(group_effect) - float(task_difficulties[task_id]))
                for task_id in task_ids
            ]
            if not probabilities:
                continue
            source_value = sum(probabilities) / len(probabilities)
            source_variance = sum(
                probability * (1.0 - probability) / replicate_count
                for probability in probabilities
            ) / (len(probabilities) ** 2)
            source_stderr = math.sqrt(source_variance)
            aligned = align_probability_cell(source_value, source_stderr)
            expected[subject_id][dimension] = {
                "source_value": source_value,
                "source_sampling_stderr": source_stderr,
                "canonical_value": float(aligned["canonical_value"]),
                "canonical_sampling_stderr": float(aligned["canonical_stderr"]),
                "expected_c_residual": float(aligned["canonical_value"])
                - intercept
                - float(group_effect),
            }
    return expected


def build_synthetic_lookups(
    known_parameters: dict[str, Any],
    subjects: Sequence[str],
    dimensions: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build synthetic A and B lookups from persisted known truth parameters."""
    validate_axis_subset(dimensions)
    group_effects = known_parameters.get("group_effects")
    if not isinstance(group_effects, dict):
        raise DatasetError("known_parameters requires group_effects")
    intercept = known_parameters.get("intercept")
    if not isinstance(intercept, (int, float)):
        raise DatasetError("known_parameters requires numeric intercept")
    a_lookups: dict[str, dict[str, Any]] = {}
    b_lookups: dict[str, dict[str, Any]] = {}
    for subject_id in sorted(subjects):
        if not subject_id.startswith("synthetic:"):
            raise DatasetError(f"synthetic subject ID has unexpected form: {subject_id}")
        group_id = subject_id.removeprefix("synthetic:")
        if group_id not in group_effects:
            raise DatasetError(f"known_parameters lacks group effect for {group_id}")
        a_cells = [
            _aligned_truth_cell(
                baseline_id=stable_record_id("synthetic-a", subject_id, dimension),
                dimension=dimension,
                value=float(intercept),
                truth_role="model_intercept",
                subject_id=subject_id,
                truth_source="known_parameters.json",
            )
            for dimension in dimensions
        ]
        b_cells = [
            _aligned_truth_cell(
                baseline_id=stable_record_id("synthetic-b", subject_id, dimension),
                dimension=dimension,
                value=float(group_effects[group_id]),
                truth_role="group_effect",
                subject_id=subject_id,
                truth_source="known_parameters.json",
            )
            for dimension in dimensions
        ]
        a_lookups[subject_id] = {
            "status": "synthetic_calibrated",
            "cells": a_cells,
            "reasons": [],
            "axis_contract_id": AXIS_CONTRACT_ID,
            "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
            "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
        }
        b_lookups[subject_id] = {
            "status": "synthetic_calibrated",
            "cells": b_cells,
            "reasons": [],
            "axis_contract_id": AXIS_CONTRACT_ID,
            "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
            "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
        }
    return a_lookups, b_lookups


def run_synthetic_abjc_validation(
    *,
    response_rows: Sequence[dict[str, Any]],
    known_parameters: dict[str, Any],
    dimensions: Sequence[str] = CANONICAL_AXES,
    task_revision: str = SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
    bootstrap_replicates: int = 500,
    bootstrap_seed: int = 20260820,
) -> dict[str, Any]:
    """Run J and C against one synthetic response population."""
    axes = validate_axis_subset(dimensions)
    truth_audit = validate_known_parameter_contract(response_rows, known_parameters)
    task_specs, axis_mapping = build_axis_probe_task_specs(
        [str(row["task_id"]) for row in response_rows],
        dimensions=axes,
        task_revision=task_revision,
    )
    execution_results = adapt_known_parameter_responses(
        response_rows, task_revision=task_revision
    )
    generator_config = {
        "dimensions": list(axes),
        "axis_contract_id": AXIS_CONTRACT_ID,
        "source_scale_id": "normalized_0_1",
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "attempt_reducer": "mean",
        "subject_failure_score": 0.0,
        "bootstrap_replicates": bootstrap_replicates,
        "bootstrap_seed": bootstrap_seed,
        "min_coverage": 1.0,
        "comparability_verified": True,
        "comparison_contract_id": SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
        "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
        "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
    }
    j_bundle = generate_evaluation_matrix(
        task_specs, execution_results, generator_config
    )
    subjects = sorted({row["subject_id"] for row in execution_results})
    a_lookups, b_lookups = build_synthetic_lookups(
        known_parameters, subjects, axes
    )
    calibration_contract = {
        "contract_id": SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
        "verified": True,
        "scale_id": CANONICAL_SCALE_ID,
        "independence_assumption": True,
        "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
        "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
    }
    canonical_j_matrix = [_align_j_cell(cell) for cell in j_bundle["evaluation_matrix"]]
    c_matrix = generate_c_matrix(
        canonical_j_matrix=canonical_j_matrix,
        a_lookups=a_lookups,
        b_lookups=b_lookups,
        calibration_contract=calibration_contract,
    )
    expected_projection = _expected_projection_from_truth(
        known_parameters,
        axis_mapping,
        axes,
        replicate_count=truth_audit["replicate_count"],
    )
    j_projection_errors = []
    c_formula_errors = []
    for j_cell, c_cell in zip(canonical_j_matrix, c_matrix):
        expected = expected_projection[j_cell["subject_id"]][j_cell["dimension_id"]]
        j_projection_errors.append(
            abs(float(j_cell["canonical_value"]) - expected["canonical_value"])
        )
        expected_c = (
            float(j_cell["canonical_value"])
            - float(a_lookups[j_cell["subject_id"]]["cells"][axes.index(j_cell["dimension_id"])]["canonical_value"])
            - float(b_lookups[j_cell["subject_id"]]["cells"][axes.index(j_cell["dimension_id"])]["canonical_value"])
        )
        if c_cell["status"] == "available":
            c_formula_errors.append(abs(float(c_cell["value"]) - expected_c))
    standardized_projection_errors = []
    for j_cell in canonical_j_matrix:
        expected = expected_projection[j_cell["subject_id"]][j_cell["dimension_id"]]
        expected_stderr = expected["canonical_sampling_stderr"]
        if expected_stderr > 0:
            standardized_projection_errors.append(
                abs(float(j_cell["canonical_value"]) - expected["canonical_value"])
                / expected_stderr
            )
    max_standardized_projection_error = (
        max(standardized_projection_errors) if standardized_projection_errors else None
    )
    sampling_check_status = (
        "passed"
        if truth_audit["replicate_count"] >= 2
        and max_standardized_projection_error is not None
        and max_standardized_projection_error <= 5.0
        else "insufficient_replicates"
        if truth_audit["replicate_count"] < 2
        else "failed"
    )
    truth_projection_report = {
        "expected_projection": expected_projection,
        "max_abs_empirical_j_canonical_minus_expected": max(j_projection_errors) if j_projection_errors else None,
        "max_standardized_empirical_j_projection_error": max_standardized_projection_error,
        "sampling_check_threshold_sd": 5.0,
        "sampling_check_status": sampling_check_status,
        "max_abs_c_formula_error": max(c_formula_errors) if c_formula_errors else None,
        "expected_c_residuals_are_zero": all(
            abs(value["expected_c_residual"]) <= 1e-12
            for subject in expected_projection.values()
            for value in subject.values()
        ),
        "interpretation": (
            "The expected residual is generally non-zero because J computes "
            "logit(mean(probability)), while the known generator is additive on "
            "the per-trial logit scale. This is a projection effect, not an error."
        ),
    }
    return {
        "schema_version": "experience-evaluation-synthetic-abjc-validation/v0.2",
        "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
        "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
        "axis_probe_contract_id": SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
        "task_specs": task_specs,
        "execution_results": execution_results,
        "axis_mapping": axis_mapping,
        "generator_config": generator_config,
        "calibration_contract": calibration_contract,
        "known_parameters": known_parameters,
        "a_lookups": a_lookups,
        "b_lookups": b_lookups,
        "j_bundle": j_bundle,
        "canonical_j_matrix": canonical_j_matrix,
        "c_matrix": c_matrix,
        "truth_projection_report": truth_projection_report,
        "validation_report": {
            "schema_version": "experience-evaluation-synthetic-validation-report/v0.2",
            "validation_status": "passed" if (
                all(cell["status"] == "available" for cell in c_matrix)
                and truth_audit["status"] == "passed"
                and truth_projection_report["max_abs_c_formula_error"] is not None
                and truth_projection_report["max_abs_c_formula_error"] <= 1e-12
            ) else "failed",
            "sampling_check_status": truth_projection_report["sampling_check_status"],
            "evidence_origin": SYNTHETIC_EVIDENCE_ORIGIN,
            "publication_scope": SYNTHETIC_PUBLICATION_SCOPE,
            "synthetic_only": True,
            "j_cell_count": len(canonical_j_matrix),
            "c_available_cells": sum(cell["status"] == "available" for cell in c_matrix),
            "c_unavailable_cells": sum(cell["status"] != "available" for cell in c_matrix),
            "j_publish_statuses": sorted({cell["publish_status"] for cell in canonical_j_matrix}),
            "source_result_count": len(execution_results),
            "truth_contract_audit": truth_audit,
            "truth_projection_report": truth_projection_report,
            "attempt_reducer": "mean",
            "truth_fields_used_for_scoring": [],
            "truth_fields_retained_for_audit": [
                "success_probability", "true_linear_predictor", "group_effects", "task_difficulties"
            ],
            "non_commuting_transform_warning": (
                "J applies logit after probability aggregation; this is not the same as "
                "averaging per-trial logits. Therefore C is checked against the J aggregation "
                "contract, not expected to equal zero from the latent generating parameters."
            ),
            "interpretation": (
                "This validates deterministic matrix arithmetic and contract wiring only. "
                "It is not real Agent evidence, calibration, ranking, or latent-state recovery."
            ),
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_synthetic_validation_bundle(
    output_dir: Path,
    validation: dict[str, Any],
    *,
    input_files: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Write an immutable synthetic-only validation bundle."""
    if output_dir.exists():
        raise DatasetError(f"synthetic validation bundle already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "task_specs.jsonl", validation["task_specs"])
    _write_jsonl(output_dir / "execution_results.jsonl", validation["execution_results"])
    _write_json(output_dir / "synthetic_axis_probe_mapping.json", {
        "schema_version": "experience-evaluation-synthetic-axis-probe-mapping/v0.1",
        "axis_probe_contract_id": validation["axis_probe_contract_id"],
        "axis_contract_id": AXIS_CONTRACT_ID,
        "dimensions": validation["generator_config"]["dimensions"],
        "mapping": validation["axis_mapping"],
        "mapping_basis": "balanced_round_robin_fixture",
        "semantic_capability_claim": False,
    })
    a_rows = []
    for lookup in validation["a_lookups"].values():
        a_rows.extend({"lookup_status": lookup["status"], **cell} for cell in lookup["cells"])
    b_rows = []
    for lookup in validation["b_lookups"].values():
        b_rows.extend({"lookup_status": lookup["status"], **cell} for cell in lookup["cells"])
    _write_jsonl(output_dir / "a_baselines.jsonl", a_rows)
    _write_jsonl(output_dir / "b_baselines.jsonl", b_rows)
    _write_jsonl(output_dir / "objective_matrix.jsonl", validation["j_bundle"]["objective_matrix"])
    _write_jsonl(output_dir / "j_matrix.jsonl", validation["j_bundle"]["evaluation_matrix"])
    _write_jsonl(output_dir / "canonical_j_matrix.jsonl", validation["canonical_j_matrix"])
    _write_jsonl(output_dir / "c_matrix.jsonl", validation["c_matrix"])
    _write_json(output_dir / "generator_config.json", validation["generator_config"])
    _write_json(output_dir / "calibration_contract.json", validation["calibration_contract"])
    _write_json(output_dir / "known_parameters.json", validation["known_parameters"])
    _write_json(output_dir / "truth_projection_report.json", validation["truth_projection_report"])
    _write_json(output_dir / "summary.json", {
        "schema_version": "experience-evaluation-synthetic-abjc-summary/v0.2",
        "evidence_origin": validation["evidence_origin"],
        "publication_scope": validation["publication_scope"],
        "axis_probe_contract_id": validation["axis_probe_contract_id"],
        "j_summary": validation["j_bundle"]["summary"],
        "c_available_cells": validation["validation_report"]["c_available_cells"],
        "c_unavailable_cells": validation["validation_report"]["c_unavailable_cells"],
        "synthetic_only": True,
    })
    _write_json(output_dir / "validation_report.json", validation["validation_report"])
    files = {
        item.name: sha256_file(item)
        for item in sorted(output_dir.iterdir())
        if item.is_file()
    }
    manifest = {
        "schema_version": "experience-evaluation-synthetic-abjc-manifest/v0.2",
        "evidence_origin": validation["evidence_origin"],
        "publication_scope": validation["publication_scope"],
        "files": files,
        "inputs": input_files or {},
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest
