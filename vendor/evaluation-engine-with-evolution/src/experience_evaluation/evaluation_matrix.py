"""Deterministic Task-result to evaluation-matrix generator.

This module contains no LLM calls. It validates versioned Task specifications,
normalizes objective metrics, reduces repeated attempts, preserves missingness,
and projects Task scores through a capability-loading matrix with coverage and
Task-block bootstrap uncertainty.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

from .canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_AXES,
    CANONICAL_SCALE_ID,
    PROBABILITY_SCALE_ID,
    align_probability_cell,
    validate_axis_subset,
)
from .j_a_mapping import load_j_a_mapping_bundle, project_atoms_to_axes
from .public_dataset import DatasetError, sha256_file, stable_record_id


GENERATOR_VERSION = "experience-evaluation-matrix-generator/v0.4"
_RESULT_STATUSES = {"completed", "subject_failure", "infrastructure_failure", "invalid"}


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise DatasetError("quantile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    dimensions = config.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions or any(
        not isinstance(item, str) or not item for item in dimensions
    ):
        raise DatasetError("generator config requires non-empty dimensions")
    try:
        dimensions = list(validate_axis_subset(dimensions))
    except DatasetError as exc:
        raise DatasetError(f"generator dimensions must use {AXIS_CONTRACT_ID}") from exc
    attempt_reducer = config.get("attempt_reducer", "mean")
    if attempt_reducer not in {"mean", "max"}:
        raise DatasetError("attempt_reducer must be mean or max")
    bootstrap_replicates = config.get("bootstrap_replicates", 1000)
    if not isinstance(bootstrap_replicates, int) or bootstrap_replicates < 0:
        raise DatasetError("bootstrap_replicates must be a non-negative integer")
    min_coverage = float(config.get("min_coverage", 0.7))
    if not 0 <= min_coverage <= 1:
        raise DatasetError("min_coverage must be in [0, 1]")
    subject_failure_score = float(config.get("subject_failure_score", 0.0))
    if not 0 <= subject_failure_score <= 1:
        raise DatasetError("subject_failure_score must be in [0, 1]")
    comparability_verified = config.get("comparability_verified", False)
    if not isinstance(comparability_verified, bool):
        raise DatasetError("comparability_verified must be boolean")
    comparison_contract_id = config.get("comparison_contract_id")
    if comparability_verified and (
        not isinstance(comparison_contract_id, str) or not comparison_contract_id
    ):
        raise DatasetError("verified comparability requires comparison_contract_id")
    expected_contract = {
        "axis_contract_id": AXIS_CONTRACT_ID,
        "source_scale_id": PROBABILITY_SCALE_ID,
        "canonical_scale_id": CANONICAL_SCALE_ID,
    }
    for key, expected in expected_contract.items():
        if config.get(key) != expected:
            raise DatasetError(f"generator config requires {key}={expected}")
    evidence_origin = config.get("evidence_origin")
    publication_scope = config.get("publication_scope")
    if evidence_origin is not None and (
        not isinstance(evidence_origin, str) or not evidence_origin
    ):
        raise DatasetError("evidence_origin must be a non-empty string or null")
    if publication_scope is not None and publication_scope not in {
        "real_evidence", "local_controlled", "synthetic_only", "shadow"
    }:
        raise DatasetError(
            "publication_scope must be real_evidence, local_controlled, synthetic_only, shadow, or null"
        )
    atom_projection = config.get("atom_projection") or {}
    if atom_projection and not isinstance(atom_projection, dict):
        raise DatasetError("atom_projection must be an object")
    atom_enabled = bool(atom_projection.get("enabled", False))
    atom_config_dir = None
    # Atom-projected stderr is the sample SE across contributing tasks. There is no
    # constant fallback: a single observed task yields a point estimate with no SE.
    if "default_stderr" in atom_projection:
        raise DatasetError(
            "atom_projection.default_stderr is not supported; "
            "atom stderr comes from across-task variation only"
        )
    if atom_enabled:
        raw_dir = atom_projection.get("config_dir")
        if not isinstance(raw_dir, str) or not raw_dir:
            raise DatasetError("atom_projection.enabled requires config_dir")
        atom_config_dir = Path(raw_dir)
        if not atom_config_dir.is_dir():
            raise DatasetError(f"atom_projection config_dir is not a directory: {atom_config_dir}")
    return {
        "dimensions": list(dimensions),
        "attempt_reducer": attempt_reducer,
        "bootstrap_replicates": bootstrap_replicates,
        "bootstrap_seed": int(config.get("bootstrap_seed", 0)),
        "min_coverage": min_coverage,
        "subject_failure_score": subject_failure_score,
        "comparability_verified": comparability_verified,
        "comparison_contract_id": comparison_contract_id,
        "evidence_origin": evidence_origin,
        "publication_scope": publication_scope,
        "atom_projection": {
            "enabled": atom_enabled,
            "config_dir": None if atom_config_dir is None else str(atom_config_dir),
        },
        **expected_contract,
    }


def _validate_task_specs(
    task_specs: Sequence[dict[str, Any]], dimensions: Sequence[str]
) -> dict[str, dict[str, Any]]:
    tasks: dict[str, dict[str, Any]] = {}
    dimension_set = set(dimensions)
    for task in task_specs:
        task_id = task.get("task_id")
        revision = task.get("task_revision")
        if not isinstance(task_id, str) or not task_id or not isinstance(revision, str) or not revision:
            raise DatasetError("every TaskSpec requires task_id and task_revision")
        if task_id in tasks:
            raise DatasetError(f"duplicate TaskSpec task_id: {task_id}")
        weight = task.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or not math.isfinite(float(weight)) or weight <= 0:
            raise DatasetError(f"TaskSpec {task_id} weight must be positive")
        loadings = task.get("capability_loadings")
        if not isinstance(loadings, dict) or not loadings:
            raise DatasetError(f"TaskSpec {task_id} requires capability_loadings")
        unknown_dimensions = set(loadings) - dimension_set
        if unknown_dimensions:
            raise DatasetError(f"TaskSpec {task_id} has unknown dimensions: {sorted(unknown_dimensions)}")
        normalized_loadings = {}
        for dimension, loading in loadings.items():
            if not isinstance(loading, (int, float)) or not math.isfinite(float(loading)):
                raise DatasetError(f"TaskSpec {task_id}/{dimension} loading must be finite")
            if not 0 <= float(loading) <= 1:
                raise DatasetError(f"TaskSpec {task_id}/{dimension} loading must be in [0,1]")
            if loading > 0:
                normalized_loadings[dimension] = float(loading)
        if not normalized_loadings:
            raise DatasetError(f"TaskSpec {task_id} has no positive capability loading")
        metrics = task.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise DatasetError(f"TaskSpec {task_id} requires metrics")
        normalized_metrics = []
        metric_ids = set()
        for metric in metrics:
            metric_id = metric.get("metric_id")
            if not isinstance(metric_id, str) or not metric_id or metric_id in metric_ids:
                raise DatasetError(f"TaskSpec {task_id} metric IDs must be unique non-empty strings")
            metric_ids.add(metric_id)
            minimum = metric.get("min")
            maximum = metric.get("max")
            if not isinstance(minimum, (int, float)) or not isinstance(maximum, (int, float)):
                raise DatasetError(f"TaskSpec {task_id}/{metric_id} requires numeric min/max")
            if not math.isfinite(float(minimum)) or not math.isfinite(float(maximum)) or maximum <= minimum:
                raise DatasetError(f"TaskSpec {task_id}/{metric_id} requires max > min")
            direction = metric.get("direction")
            if direction not in {"maximize", "minimize"}:
                raise DatasetError(f"TaskSpec {task_id}/{metric_id} direction must be maximize/minimize")
            metric_weight = metric.get("weight", 1.0)
            if not isinstance(metric_weight, (int, float)) or metric_weight <= 0:
                raise DatasetError(f"TaskSpec {task_id}/{metric_id} weight must be positive")
            normalized_metrics.append({
                "metric_id": metric_id,
                "min": float(minimum),
                "max": float(maximum),
                "direction": direction,
                "weight": float(metric_weight),
                "required": bool(metric.get("required", True)),
            })
        tasks[task_id] = {
            **task,
            "weight": float(weight),
            "capability_loadings": normalized_loadings,
            "metrics": normalized_metrics,
        }
    if not tasks:
        raise DatasetError("at least one TaskSpec is required")
    return tasks


def _score_completed_result(
    result: dict[str, Any], task: dict[str, Any]
) -> tuple[Optional[float], dict[str, float], Optional[str], dict[str, Any]]:
    raw_metrics = result.get("metrics")
    if not isinstance(raw_metrics, dict):
        return None, {}, "metrics_not_object", {}
    declared_ids = {metric["metric_id"] for metric in task["metrics"]}
    unknown = sorted(set(raw_metrics) - declared_ids)
    if unknown:
        return None, {}, "unknown_metric", {"unknown_metrics": unknown}
    normalized = {}
    weighted_sum = 0.0
    total_weight = 0.0
    for metric in task["metrics"]:
        metric_id = metric["metric_id"]
        if metric_id not in raw_metrics or raw_metrics[metric_id] is None:
            if metric["required"]:
                return None, normalized, "required_metric_missing", {"metric_id": metric_id}
            continue
        value = raw_metrics[metric_id]
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return None, normalized, "metric_not_finite_number", {"metric_id": metric_id}
        value = float(value)
        if value < metric["min"] or value > metric["max"]:
            return None, normalized, "metric_out_of_range", {
                "metric_id": metric_id,
                "value": value,
                "min": metric["min"],
                "max": metric["max"],
            }
        scaled = (value - metric["min"]) / (metric["max"] - metric["min"])
        if metric["direction"] == "minimize":
            scaled = 1.0 - scaled
        normalized[metric_id] = scaled
        weighted_sum += scaled * metric["weight"]
        total_weight += metric["weight"]
    if total_weight == 0:
        return None, normalized, "no_scored_metrics", {}
    return weighted_sum / total_weight, normalized, None, {}


def _reduce_attempts(values: Sequence[float], reducer: str) -> float:
    if reducer == "max":
        return max(values)
    return sum(values) / len(values)


def generate_evaluation_matrix(
    task_specs: Sequence[dict[str, Any]],
    execution_results: Sequence[dict[str, Any]],
    generator_config: dict[str, Any],
) -> dict[str, Any]:
    """Generate objective and capability matrices from deterministic results."""
    config = _validate_config(generator_config)
    tasks = _validate_task_specs(task_specs, config["dimensions"])
    subjects = sorted({
        str(row.get("subject_id"))
        for row in execution_results
        if isinstance(row.get("subject_id"), str) and row.get("subject_id")
    })
    if not subjects:
        raise DatasetError("at least one execution result with subject_id is required")

    attempts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    excluded: dict[tuple[str, str], list[str]] = defaultdict(list)
    rejected = []
    seen_result_ids = set()

    def reject(result: dict[str, Any], reason: str, details: Optional[dict[str, Any]] = None) -> None:
        rejected.append({
            "schema_version": "experience-evaluation-result-rejection/v0.1",
            "result_id": result.get("result_id"),
            "subject_id": result.get("subject_id"),
            "task_id": result.get("task_id"),
            "stage": "result_validation",
            "reason": reason,
            "details": details or {},
        })

    for result in execution_results:
        result_id = result.get("result_id")
        subject_id = result.get("subject_id")
        task_id = result.get("task_id")
        if not isinstance(result_id, str) or not result_id:
            reject(result, "result_id_missing")
            continue
        if result_id in seen_result_ids:
            reject(result, "duplicate_result_id")
            continue
        seen_result_ids.add(result_id)
        if not isinstance(subject_id, str) or not subject_id:
            reject(result, "subject_id_missing")
            continue
        if task_id not in tasks:
            reject(result, "unknown_task")
            continue
        task = tasks[task_id]
        if result.get("task_revision") != task["task_revision"]:
            reject(result, "task_revision_mismatch", {
                "expected": task["task_revision"], "observed": result.get("task_revision")
            })
            continue
        status = result.get("status")
        if status not in _RESULT_STATUSES:
            reject(result, "invalid_result_status")
            continue
        key = (subject_id, task_id)
        if status == "infrastructure_failure":
            excluded[key].append(result_id)
            continue
        if status == "invalid":
            reject(result, "result_marked_invalid")
            continue
        if status == "subject_failure":
            attempts[key].append({
                "result_id": result_id,
                "score": config["subject_failure_score"],
                "status": "subject_failure",
                "normalized_metrics": {},
                "provenance": result.get("provenance", {}),
            })
            continue
        score, normalized_metrics, error, details = _score_completed_result(result, task)
        if error is not None or score is None:
            reject(result, error or "score_unavailable", details)
            continue
        attempts[key].append({
            "result_id": result_id,
            "score": score,
            "status": "completed",
            "normalized_metrics": normalized_metrics,
            "provenance": result.get("provenance", {}),
        })

    objective_rows = []
    objective_by_subject_task = {}
    for subject_id in subjects:
        for task_id, task in sorted(tasks.items()):
            key = (subject_id, task_id)
            valid_attempts = attempts.get(key, [])
            if valid_attempts:
                scores = [row["score"] for row in valid_attempts]
                task_score = _reduce_attempts(scores, config["attempt_reducer"])
                statuses = {row["status"] for row in valid_attempts}
                score_status = (
                    "observed_subject_failure" if statuses == {"subject_failure"}
                    else "observed_completed" if statuses == {"completed"}
                    else "observed_mixed"
                )
            else:
                task_score = None
                score_status = "not_observed"
            row = {
                "schema_version": "experience-evaluation-objective-matrix-row/v0.1",
                "objective_row_id": stable_record_id(
                    "objective-row", GENERATOR_VERSION, subject_id, task_id, task["task_revision"]
                ),
                "subject_id": subject_id,
                "task_id": task_id,
                "task_revision": task["task_revision"],
                "task_family": task.get("task_family"),
                "task_score": task_score,
                "score_status": score_status,
                "attempt_reducer": config["attempt_reducer"],
                "attempt_count": len(valid_attempts),
                "attempt_scores": [row["score"] for row in valid_attempts],
                "source_result_ids": sorted(row["result_id"] for row in valid_attempts),
                "excluded_infrastructure_result_ids": sorted(excluded.get(key, [])),
                "normalized_metric_attempts": [row["normalized_metrics"] for row in valid_attempts],
                "source_result_provenance": [row["provenance"] for row in valid_attempts],
            }
            objective_rows.append(row)
            objective_by_subject_task[key] = row

    task_ids = sorted(tasks)
    bootstrap_draws = []
    if config["bootstrap_replicates"]:
        rng = random.Random(config["bootstrap_seed"])
        bootstrap_draws = [
            [rng.choice(task_ids) for _ in task_ids]
            for _ in range(config["bootstrap_replicates"])
        ]

    evaluation_cells = []
    for subject_id in subjects:
        for dimension in config["dimensions"]:
            eligible = [
                task for task in tasks.values()
                if task["capability_loadings"].get(dimension, 0) > 0
            ]
            eligible_weight = sum(
                task["weight"] * task["capability_loadings"][dimension] for task in eligible
            )
            observed = [
                task for task in eligible
                if objective_by_subject_task[(subject_id, task["task_id"])]["task_score"] is not None
            ]
            observed_weight = sum(
                task["weight"] * task["capability_loadings"][dimension] for task in observed
            )
            if observed_weight:
                value = sum(
                    objective_by_subject_task[(subject_id, task["task_id"])]["task_score"]
                    * task["weight"] * task["capability_loadings"][dimension]
                    for task in observed
                ) / observed_weight
            else:
                value = None
            coverage = observed_weight / eligible_weight if eligible_weight else 0.0

            replicate_values = []
            if value is None:
                uncertainty_status = "unavailable_no_observations"
            elif len(observed) < 2:
                uncertainty_status = "insufficient_task_diversity"
            elif not bootstrap_draws:
                uncertainty_status = "bootstrap_disabled"
            else:
                uncertainty_status = "available"
                for draw in bootstrap_draws:
                    numerator = 0.0
                    denominator = 0.0
                    for task_id in draw:
                        task = tasks[task_id]
                        loading = task["capability_loadings"].get(dimension, 0.0)
                        task_score = objective_by_subject_task[(subject_id, task_id)]["task_score"]
                        if loading <= 0 or task_score is None:
                            continue
                        contribution_weight = task["weight"] * loading
                        numerator += task_score * contribution_weight
                        denominator += contribution_weight
                    if denominator:
                        replicate_values.append(numerator / denominator)
            if uncertainty_status == "available" and len(replicate_values) >= 2:
                stderr = statistics.stdev(replicate_values)
                ci_low = _quantile(replicate_values, 0.025)
                ci_high = _quantile(replicate_values, 0.975)
            else:
                stderr = ci_low = ci_high = None
                if uncertainty_status == "available":
                    uncertainty_status = "insufficient_bootstrap_replicates"
            if value is None or coverage < config["min_coverage"]:
                publish_status = "insufficient_coverage"
            elif config["publication_scope"] == "synthetic_only":
                publish_status = "synthetic_only"
            elif not config["comparability_verified"]:
                publish_status = "shadow_only_unverified_comparability"
            else:
                publish_status = "publishable"
            canonical = None if value is None else align_probability_cell(value, stderr)
            evaluation_cells.append({
                "schema_version": "experience-evaluation-capability-matrix-cell/v0.4",
                "cell_id": stable_record_id(
                    "evaluation-cell", GENERATOR_VERSION, subject_id, dimension
                ),
                "subject_id": subject_id,
                "dimension_id": dimension,
                "axis_contract_id": AXIS_CONTRACT_ID,
                "value": value,
                "source_value": value,
                "source_stderr": stderr,
                "source_scale_id": PROBABILITY_SCALE_ID,
                "canonical_value": None if canonical is None else canonical["canonical_value"],
                "canonical_stderr": None if canonical is None else canonical["canonical_stderr"],
                "canonical_scale_id": CANONICAL_SCALE_ID,
                "scale_transform": None if canonical is None else canonical["scale_transform"],
                "scale_transform_revision": None if canonical is None else canonical["scale_transform_revision"],
                "boundary_clipped": False if canonical is None else canonical["boundary_clipped"],
                "epsilon": None if canonical is None else canonical["epsilon"],
                "scale": PROBABILITY_SCALE_ID,
                "eligible_task_count": len(eligible),
                "observed_task_count": len(observed),
                "eligible_weight": eligible_weight,
                "observed_weight": observed_weight,
                "coverage": coverage,
                "stderr": stderr,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "uncertainty_status": uncertainty_status,
                "bootstrap_replicates_requested": config["bootstrap_replicates"],
                "bootstrap_replicates_used": len(replicate_values),
                "comparability_verified": config["comparability_verified"],
                "comparison_contract_id": config["comparison_contract_id"],
                "evidence_origin": config["evidence_origin"],
                "publication_scope": config["publication_scope"],
                "publish_status": publish_status,
                "source_objective_row_ids": [
                    objective_by_subject_task[(subject_id, task["task_id"])]["objective_row_id"]
                    for task in observed
                ],
                "projection": "capability_loadings",
            })

    if config["atom_projection"]["enabled"]:
        evaluation_cells = _overlay_atom_projection(
            evaluation_cells,
            subjects=subjects,
            tasks=tasks,
            attempts=attempts,
            config=config,
        )

    task_spec_hash = hashlib.sha256(
        json.dumps(task_specs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result_hash = hashlib.sha256(
        json.dumps(execution_results, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    summary = {
        "schema_version": "experience-evaluation-matrix-summary/v0.1",
        "generator_version": GENERATOR_VERSION,
        "task_spec_content_sha256": task_spec_hash,
        "execution_result_content_sha256": result_hash,
        "task_count": len(tasks),
        "subject_count": len(subjects),
        "dimensions": config["dimensions"],
        "objective_row_count": len(objective_rows),
        "evaluation_cell_count": len(evaluation_cells),
        "observed_objective_row_count": sum(row["task_score"] is not None for row in objective_rows),
        "rejected_result_count": len(rejected),
        "config": config,
        "axis_contract_id": AXIS_CONTRACT_ID,
        "source_scale_id": PROBABILITY_SCALE_ID,
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "no_total_score": True,
    }
    return {
        "objective_matrix": objective_rows,
        "evaluation_matrix": evaluation_cells,
        "rejected_results": rejected,
        "summary": summary,
    }


def _collect_atom_scores(
    subject_id: str,
    *,
    attempts: dict[tuple[str, str], list[dict[str, Any]]],
    mapped_atom_ids: Sequence[str],
    reducer: str,
    task_id: Optional[str] = None,
) -> dict[str, float]:
    mapped = set(mapped_atom_ids)
    collected: dict[str, list[float]] = defaultdict(list)
    for (row_subject, row_task_id), rows in attempts.items():
        if row_subject != subject_id:
            continue
        if task_id is not None and row_task_id != task_id:
            continue
        for row in rows:
            for atom_id, value in (row.get("normalized_metrics") or {}).items():
                if atom_id in mapped and isinstance(value, (int, float)):
                    collected[atom_id].append(float(value))
    scores = {}
    for atom_id, values in collected.items():
        scores[atom_id] = max(values) if reducer == "max" else sum(values) / len(values)
    return scores


def _task_q_mask(
    tasks: dict[str, dict[str, Any]],
    task_ids: Optional[Sequence[str]] = None,
) -> dict[str, float]:
    mask = {axis: 0.0 for axis in CANONICAL_AXES}
    selected = tasks.values() if task_ids is None else (
        tasks[task_id] for task_id in task_ids if task_id in tasks
    )
    for task in selected:
        for axis, loading in task["capability_loadings"].items():
            mask[axis] = max(mask[axis], float(loading))
    return mask


def _atom_axis_coverage(
    dimension: str,
    *,
    observed_atoms: Sequence[str],
    bundle: dict[str, Any],
    tasks: dict[str, dict[str, Any]],
    contributing_task_ids: Sequence[str],
) -> float:
    weights = bundle["W"]
    mapped = set(bundle["mapped_atom_ids"])
    observed = set(observed_atoms)
    eligible_weight = 0.0
    observed_weight = 0.0
    for task_id in contributing_task_ids:
        task = tasks.get(task_id)
        if task is None or float(task["capability_loadings"].get(dimension, 0.0)) <= 0:
            continue
        for metric in task["metrics"]:
            atom_id = metric["metric_id"]
            if atom_id not in mapped or (atom_id, dimension) not in weights:
                continue
            weight = weights[(atom_id, dimension)]
            eligible_weight += weight
            if atom_id in observed:
                observed_weight += weight
    if eligible_weight <= 0:
        return 0.0
    return observed_weight / eligible_weight


def _overlay_atom_projection(
    evaluation_cells: list[dict[str, Any]],
    *,
    subjects: Sequence[str],
    tasks: dict[str, dict[str, Any]],
    attempts: dict[tuple[str, str], list[dict[str, Any]]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    bundle = load_j_a_mapping_bundle(Path(config["atom_projection"]["config_dir"]))
    by_key = {(cell["subject_id"], cell["dimension_id"]): cell for cell in evaluation_cells}
    for subject_id in subjects:
        contributing_tasks = sorted({
            task_id
            for (row_subject, task_id), rows in attempts.items()
            if row_subject == subject_id and any(row.get("normalized_metrics") for row in rows)
        })
        axis_values: dict[str, list[float]] = defaultdict(list)
        axis_atoms: dict[str, list[str]] = defaultdict(list)
        mapping_id = bundle["mapping_id"]
        for task_id in contributing_tasks:
            scores = _collect_atom_scores(
                subject_id,
                attempts=attempts,
                mapped_atom_ids=bundle["mapped_atom_ids"],
                reducer=config["attempt_reducer"],
                task_id=task_id,
            )
            if not scores:
                continue
            report = project_atoms_to_axes(
                scores, bundle, q_mask=_task_q_mask(tasks, [task_id])
            )
            mapping_id = report["mapping_id"]
            for row in report["cells"]:
                if row["status"] != "projected" or row["canonical_value"] is None:
                    continue
                axis_values[row["target_id"]].append(float(row["value"]))
                axis_atoms[row["target_id"]].extend(item["atom_id"] for item in row["atoms"])
        if not axis_values:
            for dimension in config["dimensions"]:
                cell = by_key[(subject_id, dimension)]
                by_key[(subject_id, dimension)] = {
                    **cell,
                    "value": None,
                    "source_value": None,
                    "source_stderr": None,
                    "canonical_value": None,
                    "canonical_stderr": None,
                    "stderr": None,
                    "ci_low": None,
                    "ci_high": None,
                    "coverage": 0.0,
                    "uncertainty_status": "unavailable_no_observations",
                    "publish_status": "insufficient_coverage",
                    "projection": "j_atom_map",
                    "mapping_id": mapping_id,
                    "atom_count": 0,
                    "source_atom_ids": [],
                }
            continue
        for dimension, values in axis_values.items():
            if dimension not in config["dimensions"]:
                continue
            value = max(values) if config["attempt_reducer"] == "max" else sum(values) / len(values)
            stderr = None
            if len(values) >= 2:
                stderr = statistics.stdev(values) / math.sqrt(len(values))
            aligned = align_probability_cell(value, stderr)
            atoms = list(dict.fromkeys(axis_atoms[dimension]))
            coverage = _atom_axis_coverage(
                dimension,
                observed_atoms=atoms,
                bundle=bundle,
                tasks=tasks,
                contributing_task_ids=contributing_tasks,
            )
            if coverage < config["min_coverage"]:
                publish_status = "insufficient_coverage"
            elif config["publication_scope"] == "synthetic_only":
                publish_status = "synthetic_only"
            elif not config["comparability_verified"]:
                publish_status = "shadow_only_unverified_comparability"
            else:
                publish_status = "publishable"
            by_key[(subject_id, dimension)] = {
                **by_key[(subject_id, dimension)],
                "value": value,
                "source_value": value,
                "source_stderr": stderr,
                "canonical_value": aligned["canonical_value"],
                "canonical_stderr": aligned["canonical_stderr"],
                "boundary_clipped": aligned["boundary_clipped"],
                "epsilon": aligned["epsilon"],
                "stderr": stderr,
                "ci_low": None,
                "ci_high": None,
                "coverage": coverage,
                "uncertainty_status": (
                    "atom_projection_task_stderr" if stderr is not None
                    else "atom_projection_point_estimate"
                ),
                "bootstrap_replicates_used": 0,
                "publish_status": publish_status,
                "projection": "j_atom_map",
                "mapping_id": mapping_id,
                "atom_count": len(atoms),
                "observed_task_count": len(values),
                "source_atom_ids": atoms,
            }
    return [by_key[key] for key in sorted(by_key)]


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


def write_evaluation_bundle(
    output_dir: Path,
    bundle: dict[str, Any],
    *,
    generator_config: dict[str, Any],
    input_files: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Write an immutable, hash-addressed evaluation bundle."""
    if output_dir.exists():
        raise DatasetError(f"evaluation bundle already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    _write_jsonl(output_dir / "objective_matrix.jsonl", bundle["objective_matrix"])
    _write_jsonl(output_dir / "evaluation_matrix.jsonl", bundle["evaluation_matrix"])
    _write_jsonl(output_dir / "rejected_results.jsonl", bundle["rejected_results"])
    _write_json(output_dir / "summary.json", bundle["summary"])
    _write_json(output_dir / "generator_config.json", generator_config)
    files = {
        item.name: sha256_file(item)
        for item in sorted(output_dir.iterdir())
        if item.is_file()
    }
    manifest = {
        "schema_version": "experience-evaluation-matrix-bundle-manifest/v0.1",
        "generator_version": GENERATOR_VERSION,
        "files": files,
        "inputs": input_files or {},
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest
