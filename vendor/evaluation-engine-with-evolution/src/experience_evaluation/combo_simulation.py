"""Simulate work-task atom scores for common model+agent pairs, then project J."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from .canonical_measurement import CANONICAL_AXES, CANONICAL_SCALE_ID, align_probability_cell
from .j_a_mapping import load_j_a_mapping_bundle, project_atoms_to_axes
from .public_dataset import DatasetError


SIM_ID = "combo-task-atom-sim/v0.1"
PUBLICATION_SCOPE = "synthetic_only"
EVIDENCE_ORIGIN = "combo_task_atom_simulation"

TASKS = [
    {
        "task_id": "diagnose-prod-outage",
        "family": "incident-response",
        "atoms": ["logical_correctness", "social_inference", "navigational_planning"],
        "capability_loadings": {"reasoning": 1.0},
        "difficulty": 1.10,
    },
    {
        "task_id": "implement-billing-endpoint",
        "family": "feature-work",
        "atoms": ["code_generation_correctness", "test_verification", "format_compliance"],
        "capability_loadings": {"coding": 0.7, "agentic_coding": 0.3, "instruction_following": 0.2},
        "difficulty": 1.25,
    },
    {
        "task_id": "fix-flaky-ci",
        "family": "repo-repair",
        "atoms": ["repo_localization", "multi_file_agentic_edit", "test_verification"],
        "capability_loadings": {"agentic_coding": 0.7, "coding": 0.4},
        "difficulty": 1.35,
    },
    {
        "task_id": "rebuild-weekly-funnel",
        "family": "data-analysis",
        "atoms": ["table_join", "table_reformat", "event_sequence"],
        "capability_loadings": {"data_analysis": 1.0},
        "difficulty": 0.95,
    },
    {
        "task_id": "derive-capacity-bound",
        "family": "quantitative",
        "atoms": ["competition_math", "applied_math_hardness", "symbolic_integration"],
        "capability_loadings": {"mathematics": 1.0},
        "difficulty": 1.55,
    },
    {
        "task_id": "rewrite-changelog",
        "family": "writing",
        "atoms": [
            "paraphrase_equivalence",
            "summarization_faithfulness",
            "instruction_completeness",
            "lexical_precision",
            "narrative_coherence",
        ],
        "capability_loadings": {"language": 0.5, "instruction_following": 0.6},
        "difficulty": 0.85,
    },
]

COMBOS = [
    {
        "subject_id": "sol-codex",
        "label": "GPT-5.6 Sol × Codex",
        "model_config_id": "openai-gpt-5-6-sol-max",
        "agent_id": "codex",
        "c_true": {"agentic_coding": 0.30, "coding": 0.10},
    },
    {
        "subject_id": "sol-aider",
        "label": "GPT-5.6 Sol × Aider",
        "model_config_id": "openai-gpt-5-6-sol-max",
        "agent_id": "aider",
        "c_true": {"agentic_coding": -0.15},
    },
    {
        "subject_id": "fable-claude-code",
        "label": "Claude Fable 5 × Claude Code",
        "model_config_id": "anthropic-claude-fable-5-max-effort",
        "agent_id": "claude-code",
        "c_true": {"reasoning": 0.20, "agentic_coding": 0.25},
    },
    {
        "subject_id": "fable-mini-swe",
        "label": "Claude Fable 5 × mini-SWE-agent",
        "model_config_id": "anthropic-claude-fable-5-max-effort",
        "agent_id": "mini-swe-agent",
        "c_true": {"agentic_coding": -0.25},
    },
    {
        "subject_id": "gemini-gemini-cli",
        "label": "Gemini 3.6 Flash × Gemini CLI",
        "model_config_id": "google-gemini-3-6-flash-high",
        "agent_id": "gemini-cli",
        "c_true": {"data_analysis": 0.25},
    },
    {
        "subject_id": "deepseek-openhands",
        "label": "DeepSeek V4 Pro × OpenHands",
        "model_config_id": "deepseek-v4-pro",
        "agent_id": "openhands",
        "c_true": {"coding": 0.20, "agentic_coding": 0.10},
    },
]


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_real_ab(
    *,
    a_path: Path,
    b_path: Path,
) -> tuple[dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, dict[str, Any]]]]:
    a_index: dict[str, dict[str, dict[str, Any]]] = {}
    for row in _read_jsonl(a_path):
        a_index.setdefault(row["model_config_id"], {})[row["dimension_id"]] = row
    b_index: dict[str, dict[str, dict[str, Any]]] = {}
    for row in _read_jsonl(b_path):
        b_index.setdefault(row["agent_id"], {})[row["dimension_id"]] = row
    return a_index, b_index


def _axis_eta(
    combo: dict[str, Any],
    axis: str,
    a_index: dict[str, dict[str, dict[str, Any]]],
    b_index: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, float]:
    a_cell = a_index.get(combo["model_config_id"], {}).get(axis)
    b_cell = b_index.get(combo["agent_id"], {}).get(axis)
    if a_cell is None or b_cell is None:
        raise DatasetError(f"{combo['subject_id']} missing A or B on {axis}")
    a_value = float(a_cell["canonical_value"])
    b_value = float(b_cell["canonical_value"])
    c_value = float(combo.get("c_true", {}).get(axis, 0.0))
    return {
        "A": a_value,
        "B": b_value,
        "C_true": c_value,
        "eta": a_value + b_value + c_value,
    }


def _atom_eta(atom_id: str, bundle: dict[str, Any], axis_etas: dict[str, dict[str, float]]) -> float:
    weights = [
        (axis, bundle["W"][(atom_id, axis)])
        for axis in CANONICAL_AXES
        if (atom_id, axis) in bundle["W"]
    ]
    if not weights:
        raise DatasetError(f"atom {atom_id} has no W coverage")
    total = sum(weight for _, weight in weights)
    return sum(axis_etas[axis]["eta"] * weight for axis, weight in weights) / total


def simulate_combo_tasks(
    *,
    config_dir: Path,
    a_path: Path,
    b_path: Path,
    seed: int = 20260821,
    noise_sd: float = 0.12,
) -> dict[str, Any]:
    """Draw atom scores from real A/B plus planted C, then project J."""
    bundle = load_j_a_mapping_bundle(config_dir)
    a_index, b_index = load_real_ab(a_path=a_path, b_path=b_path)
    rng = random.Random(seed)
    subjects = []
    execution_results = []
    for combo in COMBOS:
        axis_etas = {
            axis: _axis_eta(combo, axis, a_index, b_index) for axis in CANONICAL_AXES
        }
        atom_scores: dict[str, float] = {}
        axis_ps: dict[str, list[float]] = {}
        axis_atoms: dict[str, list[str]] = {}
        task_rows = []
        for task in TASKS:
            metrics = {}
            for atom_id in task["atoms"]:
                if atom_id not in bundle["mapped_atom_ids"]:
                    raise DatasetError(f"task {task['task_id']} uses unknown atom {atom_id}")
                eta = _atom_eta(atom_id, bundle, axis_etas)
                noisy = eta - float(task["difficulty"]) + rng.gauss(0.0, noise_sd)
                score = min(max(_sigmoid(noisy), 0.0), 1.0)
                metrics[atom_id] = score
                previous = atom_scores.get(atom_id)
                atom_scores[atom_id] = score if previous is None else (previous + score) / 2.0
            result = {
                "result_id": f"{combo['subject_id']}:{task['task_id']}",
                "subject_id": combo["subject_id"],
                "task_id": task["task_id"],
                "task_revision": SIM_ID,
                "status": "completed",
                "metrics": metrics,
                "provenance": {
                    "evidence_origin": EVIDENCE_ORIGIN,
                    "publication_scope": PUBLICATION_SCOPE,
                    "simulation_id": SIM_ID,
                    "model_config_id": combo["model_config_id"],
                    "agent_id": combo["agent_id"],
                },
            }
            execution_results.append(result)
            task_rows.append({"task_id": task["task_id"], "family": task["family"], "metrics": metrics})
            q_mask = {axis: 0.0 for axis in CANONICAL_AXES}
            q_mask.update(task["capability_loadings"])
            task_projection = project_atoms_to_axes(metrics, bundle, q_mask=q_mask)
            for row in task_projection["cells"]:
                if row["status"] != "projected" or row["value"] is None:
                    continue
                axis_ps.setdefault(row["target_id"], []).append(float(row["value"]))
                axis_atoms.setdefault(row["target_id"], []).extend(
                    item["atom_id"] for item in row["atoms"]
                )
        cells = []
        for axis in CANONICAL_AXES:
            truth = axis_etas[axis]
            values = axis_ps.get(axis) or []
            if not values:
                cells.append({
                    "dimension_id": axis,
                    "status": "insufficient_evidence",
                    "J": None,
                    "A": truth["A"],
                    "B": truth["B"],
                    "C_true": truth["C_true"],
                    "C_hat": None,
                    "eta": truth["eta"],
                })
                continue
            probability = sum(values) / len(values)
            aligned = align_probability_cell(probability, None)
            atoms = list(dict.fromkeys(axis_atoms.get(axis, [])))
            c_hat = float(aligned["canonical_value"]) - truth["A"] - truth["B"]
            cells.append({
                "dimension_id": axis,
                "status": "projected",
                "p": probability,
                "J": aligned["canonical_value"],
                "A": truth["A"],
                "B": truth["B"],
                "C_true": truth["C_true"],
                "C_hat": c_hat,
                "eta": truth["eta"],
                "atom_count": len(atoms),
                "source_atom_ids": atoms,
                "coverage": len(values),
                "scale_id": CANONICAL_SCALE_ID,
            })
        subjects.append({
            "subject_id": combo["subject_id"],
            "label": combo["label"],
            "model_config_id": combo["model_config_id"],
            "agent_id": combo["agent_id"],
            "c_true": {axis: axis_etas[axis]["C_true"] for axis in CANONICAL_AXES},
            "tasks": task_rows,
            "atom_scores": atom_scores,
            "cells": cells,
        })
    checks = _evaluate_expected_effects(subjects)
    return {
        "schema_version": "experience-evaluation-combo-task-sim/v0.1",
        "simulation_id": SIM_ID,
        "seed": seed,
        "noise_sd": noise_sd,
        "publication_scope": PUBLICATION_SCOPE,
        "evidence_origin": EVIDENCE_ORIGIN,
        "task_count": len(TASKS),
        "combo_count": len(COMBOS),
        "subjects": subjects,
        "execution_results": execution_results,
        "checks": checks,
    }


def _evaluate_expected_effects(subjects: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {row["subject_id"]: row for row in subjects}

    def cell(subject_id: str, axis: str) -> dict[str, Any]:
        match = next(item for item in by_id[subject_id]["cells"] if item["dimension_id"] == axis)
        if match["J"] is None:
            raise DatasetError(f"{subject_id}/{axis} was not projected")
        return match

    residuals = [
        item["C_hat"] - item["C_true"]
        for subject in subjects
        for item in subject["cells"]
        if item["C_hat"] is not None
    ]
    compressions = [
        item["eta"] - item["J"]
        for subject in subjects
        for item in subject["cells"]
        if item["J"] is not None
    ]
    rmse = math.sqrt(sum(value * value for value in residuals) / len(residuals))
    mean_compression = sum(compressions) / len(compressions)
    agentic_pairs = [
        (item["C_true"], item["C_hat"])
        for subject in subjects
        for item in subject["cells"]
        if item["dimension_id"] == "agentic_coding" and item["C_hat"] is not None
    ]
    sol_j_gap = cell("sol-codex", "agentic_coding")["J"] - cell("sol-aider", "agentic_coding")["J"]
    sol_expected = (
        (cell("sol-codex", "agentic_coding")["B"] + cell("sol-codex", "agentic_coding")["C_true"])
        - (cell("sol-aider", "agentic_coding")["B"] + cell("sol-aider", "agentic_coding")["C_true"])
    )
    sol_c_hat_gap = cell("sol-codex", "agentic_coding")["C_hat"] - cell("sol-aider", "agentic_coding")["C_hat"]
    sol_c_true_gap = cell("sol-codex", "agentic_coding")["C_true"] - cell("sol-aider", "agentic_coding")["C_true"]
    fable_c_hat_gap = (
        cell("fable-claude-code", "agentic_coding")["C_hat"]
        - cell("fable-mini-swe", "agentic_coding")["C_hat"]
    )
    fable_c_true_gap = (
        cell("fable-claude-code", "agentic_coding")["C_true"]
        - cell("fable-mini-swe", "agentic_coding")["C_true"]
    )
    checks = {
        "all_axes_projected": all(item["J"] is not None for subject in subjects for item in subject["cells"]),
        "same_model_sol_agentic_codex_beats_aider": (
            cell("sol-codex", "agentic_coding")["J"] > cell("sol-aider", "agentic_coding")["J"]
        ),
        "same_model_fable_agentic_claude_beats_mini": (
            cell("fable-claude-code", "agentic_coding")["J"]
            > cell("fable-mini-swe", "agentic_coding")["J"]
        ),
        "stronger_model_higher_math_j": (
            cell("sol-codex", "mathematics")["J"] > cell("deepseek-openhands", "mathematics")["J"]
        ),
        "same_model_sol_agentic_j_gap": sol_j_gap,
        "same_model_sol_agentic_expected_gap": sol_expected,
        "same_model_c_contrast_signs_match": sol_c_hat_gap * sol_c_true_gap > 0 and fable_c_hat_gap * fable_c_true_gap > 0,
        "agentic_c_hat_tracks_c_true": _spearman(
            [pair[0] for pair in agentic_pairs],
            [pair[1] for pair in agentic_pairs],
        ),
        "mean_eta_minus_j": mean_compression,
        "raw_c_recovery_rmse": rmse,
        "raw_c_is_difficulty_biased": mean_compression > 0.4,
    }
    checks["expected_effects_hold"] = all(
        [
            checks["all_axes_projected"],
            checks["same_model_sol_agentic_codex_beats_aider"],
            checks["same_model_fable_agentic_claude_beats_mini"],
            checks["stronger_model_higher_math_j"],
            checks["same_model_c_contrast_signs_match"],
            checks["agentic_c_hat_tracks_c_true"] >= 0.5,
        ]
    )
    return checks


def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(values: list[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda index: values[index])
        ranked = [0.0] * len(values)
        for rank, index in enumerate(order):
            ranked[index] = float(rank)
        return ranked

    rx, ry = ranks(xs), ranks(ys)
    mean_x = sum(rx) / len(rx)
    mean_y = sum(ry) / len(ry)
    num = sum((a - mean_x) * (b - mean_y) for a, b in zip(rx, ry))
    den_x = math.sqrt(sum((a - mean_x) ** 2 for a in rx))
    den_y = math.sqrt(sum((b - mean_y) ** 2 for b in ry))
    if den_x == 0 or den_y == 0:
        return 0.0
    return num / (den_x * den_y)


def combo_task_specs(task_revision: str = SIM_ID) -> list[dict[str, Any]]:
    """Frozen TaskSpecs whose metrics are J evaluation atoms."""
    specs = []
    for task in TASKS:
        specs.append({
            "task_id": task["task_id"],
            "task_revision": task_revision,
            "task_family": task["family"],
            "weight": 1.0,
            "capability_loadings": dict(task["capability_loadings"]),
            "metrics": [
                {
                    "metric_id": atom_id,
                    "min": 0,
                    "max": 1,
                    "direction": "maximize",
                    "weight": 1.0,
                    "required": True,
                }
                for atom_id in task["atoms"]
            ],
        })
    return specs


def combo_identities() -> list[dict[str, Any]]:
    return [
        {
            "subject_id": combo["subject_id"],
            "model_config_id": combo["model_config_id"],
            "harness_id": combo["agent_id"],
            "label": combo["label"],
        }
        for combo in COMBOS
    ]


def write_combo_simulation(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {key: report[key] for key in report if key != "execution_results"}
    paths = {
        "summary": output_dir / "summary.json",
        "results": output_dir / "execution_results.jsonl",
    }
    paths["summary"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    paths["results"].write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in report["execution_results"]),
        encoding="utf-8",
    )
    return paths
