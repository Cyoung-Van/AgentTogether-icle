"""Build physically separated observed samples and derivative simulations."""

from __future__ import annotations

import json
import hashlib
import math
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, Optional, Sequence

from .public_dataset import DatasetError, sha256_file, stable_record_id


_GROUP_MATCH_FIELDS = (
    "benchmark_version",
    "model_revision",
    "provider_route",
    "reasoning_effort",
    "environment_revision",
    "resource_limits",
    "timeout_policy",
    "context_policy",
    "attempt_policy",
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, records: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )


def _observed_outcomes_by_task(
    outcomes: Sequence[dict[str, Any]], observation: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    observation_id = observation["observation_id"]
    rows: dict[str, dict[str, Any]] = {}
    for outcome in outcomes:
        if outcome.get("observation_id") != observation_id:
            continue
        if outcome.get("outcome_status") != "observed" or outcome.get("outcome") not in (0, 1):
            continue
        for field in ("benchmark_id", "model_id", "agent_id"):
            if outcome.get(field) is not None and observation.get(field) is not None:
                if str(outcome[field]) != str(observation[field]):
                    raise DatasetError(
                        f"{field} mismatch for {outcome.get('outcome_id')}: "
                        f"{outcome[field]!r} != {observation[field]!r}"
                    )
        task_id = str(outcome.get("task_id") or "")
        if not task_id:
            raise DatasetError(f"outcome lacks task_id: {outcome.get('outcome_id')}")
        if task_id in rows:
            raise DatasetError(f"duplicate observed task {task_id} for {observation_id}")
        rows[task_id] = outcome
    return rows


def _frozen(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _missing(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _sample_comparability_failures(
    observations: Sequence[dict[str, Any]],
    task_rows_by_observation: dict[str, dict[str, dict[str, Any]]],
    task_ids: Sequence[str],
) -> list[str]:
    failures: set[str] = set()
    for field in _GROUP_MATCH_FIELDS:
        values = [obs.get(field) for obs in observations]
        if any(_missing(value) for value in values):
            failures.add(f"{field}_unknown")
        elif len({_frozen(value) for value in values}) != 1:
            failures.add(f"{field}_mismatch")
    if any(_missing(obs.get("agent_version")) for obs in observations):
        failures.add("harness_revision_unknown")
    for task_id in task_ids:
        task_rows = [task_rows_by_observation[obs["observation_id"]][task_id] for obs in observations]
        task_revisions = [row.get("task_revision") for row in task_rows]
        fixture_hashes = [row.get("fixture_hash") for row in task_rows]
        if all(_missing(value) for value in task_revisions) and all(_missing(value) for value in fixture_hashes):
            failures.add("task_revision_and_fixture_unknown")
        elif len({(_frozen(revision), _frozen(fixture)) for revision, fixture in zip(task_revisions, fixture_hashes)}) != 1:
            failures.add("task_revision_or_fixture_mismatch")
        verifier_revisions = [row.get("verifier_revision") for row in task_rows]
        if any(_missing(value) for value in verifier_revisions):
            failures.add("verifier_revision_unknown")
        elif len({_frozen(value) for value in verifier_revisions}) != 1:
            failures.add("verifier_revision_mismatch")
    return sorted(failures)


def build_typical_sample_dataset(
    observations: Sequence[dict[str, Any]],
    outcomes: Sequence[dict[str, Any]],
    *,
    benchmark_id: str,
    model_id: str,
    harness_ids: Sequence[str],
    max_tasks: Optional[int] = None,
    source_snapshot_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Create observed typical groups over an exact intersection of task IDs.

    One upstream observation is treated as one source-declared configuration
    scope.  Missing environment metadata remains explicitly unverified.
    """
    if len(set(harness_ids)) != len(harness_ids) or len(harness_ids) < 2:
        raise DatasetError("typical sample requires at least two unique harness IDs")
    selected = []
    for harness_id in harness_ids:
        candidates = [
            row for row in observations
            if row.get("benchmark_id") == benchmark_id
            and row.get("model_id") == model_id
            and row.get("agent_id") == harness_id
            and row.get("identity_status") != "unresolved"
            and row.get("evidence_granularity") == "task_level"
        ]
        if len(candidates) != 1:
            raise DatasetError(
                f"expected one observation for {benchmark_id}/{model_id}/{harness_id}, got {len(candidates)}"
            )
        selected.append(candidates[0])

    task_rows_by_observation = {
        obs["observation_id"]: _observed_outcomes_by_task(outcomes, obs)
        for obs in selected
    }
    common_tasks = set.intersection(*(
        set(task_rows_by_observation[obs["observation_id"]]) for obs in selected
    ))
    common_task_ids = sorted(common_tasks)
    if max_tasks is not None:
        if max_tasks <= 0:
            raise DatasetError("max_tasks must be positive")
        common_task_ids = common_task_ids[:max_tasks]
    if not common_task_ids:
        raise DatasetError("typical sample has no common observed tasks")

    selected_evidence = {
        "source_snapshot_sha256": source_snapshot_sha256,
        "observations": sorted(selected, key=lambda row: row["observation_id"]),
        "outcomes": sorted(
            (
                task_rows_by_observation[obs["observation_id"]][task_id]
                for obs in selected for task_id in common_task_ids
            ),
            key=lambda row: str(row.get("outcome_id")),
        ),
    }
    dataset_content_sha256 = hashlib.sha256(_frozen(selected_evidence).encode("utf-8")).hexdigest()
    dataset_id = stable_record_id(
        "observed-response-sample", benchmark_id, model_id, dataset_content_sha256
    )
    comparability_failures = _sample_comparability_failures(
        selected, task_rows_by_observation, common_task_ids
    )
    exact_comparable = not comparability_failures
    groups = []
    responses = []
    for obs in selected:
        group_id = stable_record_id("typical-group", dataset_id, obs["observation_id"])
        groups.append({
            "schema_version": "experience-evaluation-typical-group/v0.1",
            "sample_dataset_id": dataset_id,
            "group_id": group_id,
            "benchmark_id": benchmark_id,
            "harness_id": obs["agent_id"],
            "harness_version": obs.get("agent_version"),
            "harness_version_label": obs.get("agent_version_label"),
            "harness_version_evidence_status": obs.get("agent_version_evidence_status"),
            "model_id": model_id,
            "model_revision": obs.get("model_revision"),
            "model_revision_evidence_status": obs.get("model_revision_evidence_status"),
            "provider_route": obs.get("provider_route"),
            "reasoning_effort": obs.get("reasoning_effort"),
            "environment_revision": obs.get("environment_revision"),
            "resource_limits": obs.get("resource_limits"),
            "timeout_policy": obs.get("timeout_policy"),
            "context_policy": obs.get("context_policy"),
            "attempt_policy": obs.get("attempt_policy"),
            "environment_consistency_status": "verified_common_configuration" if exact_comparable else "unverified",
            "grouping_basis": "single_source_observation",
            "evidence_tier": "observed_exact_configuration" if exact_comparable else "observed_configuration_unverified",
            "source_id": obs.get("source_id"),
            "source_record_id": obs.get("source_record_id"),
            "observation_id": obs["observation_id"],
            "observed_at": obs.get("observed_at"),
            "detail_url": obs.get("detail_url"),
        })
        by_task = task_rows_by_observation[obs["observation_id"]]
        for task_id in common_task_ids:
            outcome = by_task[task_id]
            responses.append({
                "schema_version": "experience-evaluation-observed-task-response/v0.2",
                "sample_response_id": stable_record_id("observed-response", dataset_id, group_id, task_id),
                "sample_dataset_id": dataset_id,
                "group_id": group_id,
                "benchmark_id": benchmark_id,
                "task_id": task_id,
                "harness_id": obs["agent_id"],
                "model_id": model_id,
                "outcome": int(outcome["outcome"]),
                "evidence_origin": "observed",
                "response_semantics": outcome.get("response_semantics") or obs.get("response_semantics") or "aggregated_binary_task_response",
                "trial_count": outcome.get("trial_count"),
                "aggregation_rule": outcome.get("aggregation_rule"),
                "environment_consistency_status": "verified_common_configuration" if exact_comparable else "unverified",
                "observation_id": obs["observation_id"],
                "outcome_id": outcome.get("outcome_id"),
                "source_id": outcome.get("source_id") or obs.get("source_id"),
                "source_record_id": obs.get("source_record_id"),
            })

    group_statistics = []
    for group in groups:
        values = [row["outcome"] for row in responses if row["group_id"] == group["group_id"]]
        group_statistics.append({
            "group_id": group["group_id"],
            "harness_id": group["harness_id"],
            "task_count": len(values),
            "successes": sum(values),
            "success_rate": sum(values) / len(values),
            "environment_consistency_status": group["environment_consistency_status"],
        })
    task_statistics = []
    for task_id in common_task_ids:
        values = [row["outcome"] for row in responses if row["task_id"] == task_id]
        task_statistics.append({
            "task_id": task_id,
            "group_count": len(values),
            "successes": sum(values),
            "empirical_success_rate": sum(values) / len(values),
        })
    statistics = {
        "schema_version": "experience-evaluation-observed-response-sample-statistics/v0.2",
        "sample_dataset_id": dataset_id,
        "dataset_content_sha256": dataset_content_sha256,
        "source_snapshot_sha256": source_snapshot_sha256,
        "benchmark_id": benchmark_id,
        "model_id": model_id,
        "group_count": len(groups),
        "common_task_count": len(common_task_ids),
        "observed_response_count": len(responses),
        "response_semantics": "aggregated_binary_task_response",
        "all_groups_environment_verified": exact_comparable,
        "comparability_failures": comparability_failures,
        "fit_status": "eligible_for_exact_fit_review" if exact_comparable else "exploratory_only",
        "group_statistics": group_statistics,
        "task_statistics": task_statistics,
    }
    return {
        "dataset_id": dataset_id,
        "dataset_content_sha256": dataset_content_sha256,
        "groups": groups,
        "responses": responses,
        "statistics": statistics,
    }


def simulate_block_bootstrap(
    sample: dict[str, Any], *, replicates: int, seed: int
) -> list[dict[str, Any]]:
    """Resample complete Task blocks so cross-group dependence is retained."""
    if replicates <= 0:
        raise DatasetError("replicates must be positive")
    rng = random.Random(seed)
    task_blocks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sample["responses"]:
        task_blocks[row["task_id"]].append(row)
    task_ids = sorted(task_blocks)
    group_count = len(sample["groups"])
    expected_group_ids = {group["group_id"] for group in sample["groups"]}
    if any(
        len(task_blocks[task_id]) != group_count
        or {row["group_id"] for row in task_blocks[task_id]} != expected_group_ids
        for task_id in task_ids
    ):
        raise DatasetError("bootstrap requires one response per group in every complete Task block")
    simulated = []
    for replicate in range(replicates):
        for draw_index in range(len(task_ids)):
            source_task_id = rng.choice(task_ids)
            for source in sorted(task_blocks[source_task_id], key=lambda row: row["group_id"]):
                simulated.append({
                    "schema_version": "experience-evaluation-bootstrap-response/v0.2",
                    "simulation_response_id": stable_record_id(
                        "bootstrap-response", sample["dataset_id"], seed, replicate, draw_index, source["group_id"]
                    ),
                    "parent_sample_dataset_id": sample["dataset_id"],
                    "simulation_method": "task_block_bootstrap",
                    "simulation_seed": seed,
                    "simulation_replicate": replicate,
                    "task_draw_index": draw_index,
                    "source_task_id": source_task_id,
                    "source_sample_response_id": source["sample_response_id"],
                    "group_id": source["group_id"],
                    "harness_id": source["harness_id"],
                    "model_id": source["model_id"],
                    "outcome": source["outcome"],
                    "evidence_origin": "bootstrap_resampled",
                })
    return simulated


def build_limited_experiment_report(sample: dict[str, Any]) -> dict[str, Any]:
    """Describe paired observed outcomes without promoting them to formal B evidence."""
    responses_by_group_task = {
        (row["group_id"], row["task_id"]): int(row["outcome"])
        for row in sample["responses"]
    }
    tasks = sorted({row["task_id"] for row in sample["responses"]})
    groups = sorted(sample["groups"], key=lambda row: row["group_id"])
    comparisons = []
    for left, right in combinations(groups, 2):
        deltas = [
            responses_by_group_task[(left["group_id"], task_id)]
            - responses_by_group_task[(right["group_id"], task_id)]
            for task_id in tasks
        ]
        comparisons.append({
            "comparison_id": stable_record_id(
                "sample-pair", sample["dataset_id"], left["group_id"], right["group_id"]
            ),
            "group_a": left["group_id"],
            "group_b": right["group_id"],
            "harness_a": left["harness_id"],
            "harness_b": right["harness_id"],
            "matched_task_count": len(deltas),
            "a_wins": sum(delta > 0 for delta in deltas),
            "b_wins": sum(delta < 0 for delta in deltas),
            "ties": sum(delta == 0 for delta in deltas),
            "mean_outcome_delta_a_minus_b": sum(deltas) / len(deltas),
            "evidence_tier": "observed_common_tasks_environment_unverified"
            if left["environment_consistency_status"] != "verified_common_configuration"
            or right["environment_consistency_status"] != "verified_common_configuration"
            else "observed_common_tasks_environment_verified",
        })
    return {
        "schema_version": "experience-evaluation-limited-experiment-report/v0.1",
        "sample_dataset_id": sample["dataset_id"],
        "inference_status": sample["statistics"]["fit_status"],
        "eligible_for_agent_ranking": False,
        "group_statistics": sample["statistics"]["group_statistics"],
        "pairwise_comparisons": comparisons,
        "limitations": [
            "environment and revision fields remain unverified unless explicitly present",
            "pairwise rows share Task outcomes and are not independent observations",
            "records are aggregated binary Task responses, not individual Trial runs",
            "results describe this historical sample only and are not formal Matrix B estimates",
        ],
    }


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise DatasetError("quantile requires values")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def build_simulation_experiment_report(
    sample: dict[str, Any],
    bootstrap_rows: Sequence[dict[str, Any]],
    parametric_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Compare simulated group-rate distributions with the observed sample."""
    observed_rates = {
        row["group_id"]: row["success_rate"]
        for row in sample["statistics"]["group_statistics"]
    }

    def summarize(rows: Sequence[dict[str, Any]], method: str) -> list[dict[str, Any]]:
        by_group_replicate: dict[tuple[str, int], list[int]] = defaultdict(list)
        for row in rows:
            by_group_replicate[(row["group_id"], int(row["simulation_replicate"]))].append(
                int(row["outcome"])
            )
        rates_by_group: dict[str, list[float]] = defaultdict(list)
        for (group_id, _), values in by_group_replicate.items():
            rates_by_group[group_id].append(sum(values) / len(values))
        summaries = []
        for group_id, observed_rate in sorted(observed_rates.items()):
            rates = rates_by_group[group_id]
            simulated_mean = sum(rates) / len(rates)
            summaries.append({
                "group_id": group_id,
                "method": method,
                "observed_success_rate": observed_rate,
                "simulated_mean_success_rate": simulated_mean,
                "simulated_p05": _quantile(rates, 0.05),
                "simulated_p95": _quantile(rates, 0.95),
                "absolute_mean_error": abs(simulated_mean - observed_rate),
                "replicate_count": len(rates),
            })
        return summaries

    bootstrap_summary = summarize(bootstrap_rows, "task_block_bootstrap")
    parametric_summary = summarize(parametric_rows, "beta_smoothed_empirical_logit_additive")
    return {
        "schema_version": "experience-evaluation-simulation-experiment-report/v0.1",
        "parent_sample_dataset_id": sample["dataset_id"],
        "experiment_status": "simulation_only_not_real_evidence",
        "bootstrap_group_rate_checks": bootstrap_summary,
        "parametric_group_rate_checks": parametric_summary,
        "max_parametric_group_rate_absolute_mean_error": max(
            row["absolute_mean_error"] for row in parametric_summary
        ),
        "permitted_conclusions": [
            "pipeline behavior", "sample-size sensitivity", "parameter-recovery design"
        ],
        "prohibited_conclusions": [
            "new real-world Agent evidence", "formal Agent ranking", "increased Exact coverage"
        ],
    }


def _bounded_probability(value: float) -> float:
    return min(max(value, 1e-6), 1 - 1e-6)


def _logit(value: float) -> float:
    value = _bounded_probability(value)
    return math.log(value / (1 - value))


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


def simulate_empirical_logit(
    sample: dict[str, Any], *, replicates: int, seed: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Simulate a Beta-smoothed additive group × task Bernoulli model."""
    if replicates <= 0:
        raise DatasetError("replicates must be positive")
    rng = random.Random(seed)
    groups = sorted(group["group_id"] for group in sample["groups"])
    tasks = sorted({row["task_id"] for row in sample["responses"]})
    values_by_group: dict[str, list[int]] = defaultdict(list)
    values_by_task: dict[str, list[int]] = defaultdict(list)
    all_values = []
    harness_by_group = {group["group_id"]: group["harness_id"] for group in sample["groups"]}
    for row in sample["responses"]:
        value = int(row["outcome"])
        values_by_group[row["group_id"]].append(value)
        values_by_task[row["task_id"]].append(value)
        all_values.append(value)
    parameters = {
        "schema_version": "experience-evaluation-simulation-parameters/v0.1",
        "parent_sample_dataset_id": sample["dataset_id"],
        "generator": "beta_smoothed_empirical_logit_additive/v0.1",
        "formula": "logit(p_gt)=logit(q_g)+logit(q_t)-logit(q_global)",
        "beta_prior": {"alpha": 0.5, "beta": 0.5},
        "seed": seed,
        "replicates": replicates,
        "observed_global": {"successes": sum(all_values), "trials": len(all_values)},
        "observed_groups": {
            group_id: {"successes": sum(values), "trials": len(values)}
            for group_id, values in sorted(values_by_group.items())
        },
        "observed_tasks": {
            task_id: {"successes": sum(values), "trials": len(values)}
            for task_id, values in sorted(values_by_task.items())
        },
        "permitted_use": ["pipeline_validation", "parameter_recovery_design", "power_and_sensitivity"],
        "prohibited_use": [
            "published_agent_ranking", "real_evidence_coverage", "formal_matrix_b_fit",
            "formal_parameter_recovery_or_sbc",
        ],
    }
    simulated = []
    for replicate in range(replicates):
        global_successes = sum(all_values)
        q_global = rng.betavariate(global_successes + 0.5, len(all_values) - global_successes + 0.5)
        q_groups = {
            group_id: rng.betavariate(sum(values_by_group[group_id]) + 0.5, len(values_by_group[group_id]) - sum(values_by_group[group_id]) + 0.5)
            for group_id in groups
        }
        q_tasks = {
            task_id: rng.betavariate(sum(values_by_task[task_id]) + 0.5, len(values_by_task[task_id]) - sum(values_by_task[task_id]) + 0.5)
            for task_id in tasks
        }
        for task_id in tasks:
            for group_id in groups:
                probability = _sigmoid(_logit(q_groups[group_id]) + _logit(q_tasks[task_id]) - _logit(q_global))
                simulated.append({
                    "schema_version": "experience-evaluation-parametric-simulation-response/v0.2",
                    "simulation_response_id": stable_record_id(
                        "parametric-response", sample["dataset_id"], seed, replicate, task_id, group_id
                    ),
                    "parent_sample_dataset_id": sample["dataset_id"],
                    "simulation_method": "beta_smoothed_empirical_logit_additive",
                    "simulation_seed": seed,
                    "simulation_replicate": replicate,
                    "task_id": task_id,
                    "group_id": group_id,
                    "harness_id": harness_by_group[group_id],
                    "model_id": sample["statistics"]["model_id"],
                    "success_probability": probability,
                    "outcome": int(rng.random() < probability),
                    "evidence_origin": "parametric_simulation",
                })
    return simulated, parameters


def simulate_known_parameter_logit(
    sample: dict[str, Any],
    *,
    group_effects: dict[str, float],
    task_difficulties: dict[str, float],
    intercept: float,
    replicates: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Generate responses from identifiable, persisted ground-truth parameters."""
    if replicates <= 0:
        raise DatasetError("replicates must be positive")
    expected_groups = {group["group_id"] for group in sample["groups"]}
    expected_tasks = {row["task_id"] for row in sample["responses"]}
    if set(group_effects) != expected_groups or set(task_difficulties) != expected_tasks:
        raise DatasetError("known parameters must cover every sample group and Task exactly")
    if abs(sum(group_effects.values()) / len(group_effects)) > 1e-12:
        raise DatasetError("group effects must be mean-centered for identifiability")
    if abs(sum(task_difficulties.values()) / len(task_difficulties)) > 1e-12:
        raise DatasetError("task difficulties must be mean-centered for identifiability")

    truth = {
        "schema_version": "experience-evaluation-known-parameters/v0.1",
        "parent_sample_dataset_id": sample["dataset_id"],
        "generator": "known_parameter_logit_additive/v0.1",
        "formula": "logit(p_gt)=intercept+group_effect_g-task_difficulty_t",
        "identifiability_constraints": ["mean(group_effects)=0", "mean(task_difficulties)=0"],
        "intercept": intercept,
        "group_effects": dict(sorted(group_effects.items())),
        "task_difficulties": dict(sorted(task_difficulties.items())),
        "replicates": replicates,
        "seed": seed,
    }
    harness_by_group = {group["group_id"]: group["harness_id"] for group in sample["groups"]}
    rng = random.Random(seed)
    simulated = []
    for replicate in range(replicates):
        for task_id, difficulty in sorted(task_difficulties.items()):
            for group_id, effect in sorted(group_effects.items()):
                linear_predictor = intercept + effect - difficulty
                probability = _sigmoid(linear_predictor)
                simulated.append({
                    "schema_version": "experience-evaluation-known-parameter-response/v0.1",
                    "simulation_response_id": stable_record_id(
                        "known-response", sample["dataset_id"], seed, replicate, task_id, group_id
                    ),
                    "parent_sample_dataset_id": sample["dataset_id"],
                    "simulation_method": "known_parameter_logit_additive",
                    "simulation_seed": seed,
                    "simulation_replicate": replicate,
                    "task_id": task_id,
                    "group_id": group_id,
                    "harness_id": harness_by_group[group_id],
                    "model_id": sample["statistics"]["model_id"],
                    "true_linear_predictor": linear_predictor,
                    "success_probability": probability,
                    "outcome": int(rng.random() < probability),
                    "evidence_origin": "known_parameter_simulation",
                })

    cell_values: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in simulated:
        cell_values[(row["group_id"], row["task_id"])].append(int(row["outcome"]))
    cell_logits = {
        cell: _logit((sum(values) + 0.5) / (len(values) + 1.0))
        for cell, values in cell_values.items()
    }
    estimated_intercept = sum(cell_logits.values()) / len(cell_logits)
    estimated_group_effects = {
        group_id: (
            sum(cell_logits[(group_id, task_id)] for task_id in task_difficulties)
            / len(task_difficulties)
            - estimated_intercept
        )
        for group_id in group_effects
    }
    estimated_task_difficulties = {
        task_id: (
            estimated_intercept
            - sum(cell_logits[(group_id, task_id)] for group_id in group_effects) / len(group_effects)
        )
        for task_id in task_difficulties
    }
    group_rmse = math.sqrt(sum(
        (estimated_group_effects[key] - group_effects[key]) ** 2 for key in group_effects
    ) / len(group_effects))
    task_rmse = math.sqrt(sum(
        (estimated_task_difficulties[key] - task_difficulties[key]) ** 2
        for key in task_difficulties
    ) / len(task_difficulties))
    recovery = {
        "schema_version": "experience-evaluation-parameter-recovery-report/v0.1",
        "parent_sample_dataset_id": sample["dataset_id"],
        "recovery_status": "completed",
        "estimator": "beta-smoothed-cell-logit-two-way-additive/v0.1",
        "true_intercept": intercept,
        "estimated_intercept": estimated_intercept,
        "intercept_absolute_error": abs(estimated_intercept - intercept),
        "true_group_effects": dict(sorted(group_effects.items())),
        "estimated_group_effects": dict(sorted(estimated_group_effects.items())),
        "group_effect_rmse": group_rmse,
        "task_difficulty_rmse": task_rmse,
        "interpretation": "algorithm recovery on synthetic ground truth only; not real Agent evidence",
    }
    return simulated, truth, recovery


def _default_known_parameters(sample: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    groups = sorted(group["group_id"] for group in sample["groups"])
    tasks = sorted({row["task_id"] for row in sample["responses"]})
    group_center = (len(groups) - 1) / 2
    task_center = (len(tasks) - 1) / 2
    group_effects = {group_id: 0.3 * (index - group_center) for index, group_id in enumerate(groups)}
    if len(tasks) == 1:
        task_difficulties = {tasks[0]: 0.0}
    else:
        task_difficulties = {
            task_id: 4.0 * (index - task_center) / (len(tasks) - 1)
            for index, task_id in enumerate(tasks)
        }
    return group_effects, task_difficulties


def _directory_manifest(directory: Path, *, schema_version: str) -> dict[str, Any]:
    files = {
        item.name: sha256_file(item)
        for item in sorted(directory.iterdir())
        if item.is_file() and item.name != "manifest.json"
    }
    return {"schema_version": schema_version, "files": files}


def _observed_outcome_matrix(sample: dict[str, Any]) -> list[dict[str, Any]]:
    by_task: dict[str, dict[str, int]] = defaultdict(dict)
    for row in sample["responses"]:
        by_task[row["task_id"]][row["group_id"]] = int(row["outcome"])
    return [{
        "schema_version": "experience-evaluation-observed-outcome-matrix-row/v0.1",
        "matrix_row_id": stable_record_id("observed-matrix-row", sample["dataset_id"], task_id),
        "sample_dataset_id": sample["dataset_id"],
        "task_id": task_id,
        "group_outcomes": dict(sorted(group_outcomes.items())),
        "evidence_origin": "observed",
    } for task_id, group_outcomes in sorted(by_task.items())]


def _bootstrap_outcome_matrices(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["simulation_replicate"]), int(row["task_draw_index"]))].append(row)
    result = []
    for (replicate, draw_index), block in sorted(grouped.items()):
        first = block[0]
        result.append({
            "schema_version": "experience-evaluation-bootstrap-outcome-matrix-row/v0.1",
            "matrix_row_id": stable_record_id(
                "bootstrap-matrix-row", first["parent_sample_dataset_id"], first["simulation_seed"], replicate, draw_index
            ),
            "parent_sample_dataset_id": first["parent_sample_dataset_id"],
            "simulation_replicate": replicate,
            "task_draw_index": draw_index,
            "source_task_id": first["source_task_id"],
            "group_outcomes": dict(sorted((row["group_id"], row["outcome"]) for row in block)),
            "evidence_origin": "bootstrap_resampled",
        })
    return result


def _parametric_outcome_matrices(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(int(row["simulation_replicate"]), row["task_id"])].append(row)
    result = []
    for (replicate, task_id), block in sorted(grouped.items()):
        first = block[0]
        result.append({
            "schema_version": "experience-evaluation-parametric-outcome-matrix-row/v0.1",
            "matrix_row_id": stable_record_id(
                "parametric-matrix-row", first["parent_sample_dataset_id"], first["simulation_seed"], replicate, task_id
            ),
            "parent_sample_dataset_id": first["parent_sample_dataset_id"],
            "simulation_replicate": replicate,
            "task_id": task_id,
            "group_success_probabilities": dict(sorted(
                (row["group_id"], row["success_probability"]) for row in block
            )),
            "group_outcomes": dict(sorted((row["group_id"], row["outcome"]) for row in block)),
            "evidence_origin": "parametric_simulation",
        })
    return result


def _known_parameter_outcome_matrices(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    matrices = _parametric_outcome_matrices(rows)
    for row in matrices:
        row["schema_version"] = "experience-evaluation-known-parameter-outcome-matrix-row/v0.1"
        row["evidence_origin"] = "known_parameter_simulation"
    return matrices


def write_sample_simulation_experiment(
    output_root: Path,
    sample: dict[str, Any],
    *,
    bootstrap_replicates: int,
    parametric_replicates: int,
    known_parameter_replicates: int,
    seed: int,
) -> dict[str, Any]:
    """Write observed and simulated artifacts into sibling, non-overlapping dirs."""
    observed_dir = output_root / "observed_responses"
    simulated_dir = output_root / "simulated"
    if observed_dir.exists() or simulated_dir.exists():
        raise DatasetError(f"experiment output already exists: {output_root}")
    observed_dir.mkdir(parents=True)
    simulated_dir.mkdir(parents=True)

    _write_jsonl(observed_dir / "sample_groups.jsonl", sample["groups"])
    _write_jsonl(observed_dir / "observed_task_responses.jsonl", sample["responses"])
    _write_jsonl(observed_dir / "observed_outcome_matrix.jsonl", _observed_outcome_matrix(sample))
    _write_json(observed_dir / "sample_statistics.json", sample["statistics"])
    _write_json(
        observed_dir / "limited_experiment_report.json",
        build_limited_experiment_report(sample),
    )
    _write_json(
        observed_dir / "manifest.json",
        _directory_manifest(observed_dir, schema_version="experience-evaluation-observed-response-manifest/v0.2"),
    )

    bootstrap_rows = simulate_block_bootstrap(sample, replicates=bootstrap_replicates, seed=seed)
    parametric_rows, parameters = simulate_empirical_logit(
        sample, replicates=parametric_replicates, seed=seed
    )
    group_effects, task_difficulties = _default_known_parameters(sample)
    known_rows, known_parameters, recovery_report = simulate_known_parameter_logit(
        sample,
        group_effects=group_effects,
        task_difficulties=task_difficulties,
        intercept=0.0,
        replicates=known_parameter_replicates,
        seed=seed + 1,
    )
    _write_jsonl(simulated_dir / "bootstrap_responses.jsonl", bootstrap_rows)
    _write_jsonl(simulated_dir / "parametric_responses.jsonl", parametric_rows)
    _write_jsonl(simulated_dir / "known_parameter_responses.jsonl", known_rows)
    _write_jsonl(
        simulated_dir / "bootstrap_outcome_matrices.jsonl",
        _bootstrap_outcome_matrices(bootstrap_rows),
    )
    _write_jsonl(
        simulated_dir / "parametric_outcome_matrices.jsonl",
        _parametric_outcome_matrices(parametric_rows),
    )
    _write_jsonl(
        simulated_dir / "known_parameter_outcome_matrices.jsonl",
        _known_parameter_outcome_matrices(known_rows),
    )
    _write_json(simulated_dir / "simulation_parameters.json", parameters)
    _write_json(simulated_dir / "known_parameters.json", known_parameters)
    _write_json(simulated_dir / "parameter_recovery_report.json", recovery_report)
    _write_json(
        simulated_dir / "simulation_experiment_report.json",
        build_simulation_experiment_report(sample, bootstrap_rows, parametric_rows),
    )
    _write_json(
        simulated_dir / "manifest.json",
        _directory_manifest(simulated_dir, schema_version="experience-evaluation-simulated-manifest/v0.1"),
    )

    observed_manifest_hash = sha256_file(observed_dir / "manifest.json")
    simulated_manifest_hash = sha256_file(simulated_dir / "manifest.json")
    experiment = {
        "schema_version": "experience-evaluation-sample-simulation-experiment/v0.2",
        "sample_dataset_id": sample["dataset_id"],
        "sample_dataset_content_sha256": sample["dataset_content_sha256"],
        "observed_dir": observed_dir.name,
        "simulated_dir": simulated_dir.name,
        "observed_manifest_sha256": observed_manifest_hash,
        "simulated_manifest_sha256": simulated_manifest_hash,
        "physical_separation": True,
        "observed_response_count": len(sample["responses"]),
        "bootstrap_response_count": len(bootstrap_rows),
        "parametric_response_count": len(parametric_rows),
        "known_parameter_response_count": len(known_rows),
        "seed": seed,
    }
    experiment_manifest_path = output_root / "experiment_manifest.json"
    _write_json(experiment_manifest_path, experiment)
    (output_root / "experiment_manifest.sha256").write_text(
        sha256_file(experiment_manifest_path) + "\n", encoding="utf-8"
    )
    return experiment
