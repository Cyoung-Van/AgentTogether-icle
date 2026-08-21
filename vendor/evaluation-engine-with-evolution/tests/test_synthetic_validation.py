from __future__ import annotations

import math
import unittest

from experience_evaluation.public_dataset import DatasetError
from experience_evaluation.synthetic_validation import (
    SYNTHETIC_AXIS_PROBE_CONTRACT_ID,
    build_axis_probe_task_specs,
    run_synthetic_abjc_validation,
    validate_known_parameter_contract,
)


class SyntheticValidationTests(unittest.TestCase):
    def _rows(self):
        groups = {"g-low": -0.4, "g-high": 0.4}
        task_difficulties = {
            "task-0": -1.0,
            "task-1": -0.6,
            "task-2": -0.2,
            "task-3": 0.2,
            "task-4": 0.6,
            "task-5": 1.0,
            "task-6": 0.0,
        }
        parameters = {
            "schema_version": "experience-evaluation-known-parameters/v0.1",
            "parent_sample_dataset_id": "synthetic-test-parent",
            "generator": "known_parameter_logit_additive/v0.1",
            "formula": "logit(p_gt)=intercept+group_effect_g-task_difficulty_t",
            "identifiability_constraints": ["mean(group_effects)=0", "mean(task_difficulties)=0"],
            "intercept": 0.1,
            "group_effects": groups,
            "task_difficulties": task_difficulties,
        }
        rows = []
        for group_id, group_effect in groups.items():
            for task_id, difficulty in task_difficulties.items():
                linear_predictor = parameters["intercept"] + group_effect - difficulty
                rows.append({
                    "schema_version": "experience-evaluation-known-parameter-response/v0.1",
                    "simulation_response_id": f"response-{group_id}-{task_id}",
                    "parent_sample_dataset_id": "synthetic-test-parent",
                    "simulation_method": "known_parameter_logit_additive",
                    "simulation_seed": 7,
                    "simulation_replicate": 0,
                    "task_id": task_id,
                    "group_id": group_id,
                    "harness_id": group_id,
                    "model_id": "synthetic-model",
                    "true_linear_predictor": linear_predictor,
                    "success_probability": 1 / (1 + math.exp(-linear_predictor)),
                    "outcome": 1 if task_id in {"task-0", "task-2", "task-4"} else 0,
                    "evidence_origin": "known_parameter_simulation",
                })
        return rows, parameters

    def test_balanced_axis_probe_is_explicitly_nonsemantic(self) -> None:
        tasks, mapping = build_axis_probe_task_specs(
            [f"task-{index}" for index in range(7)]
        )
        self.assertEqual(set(mapping.values()), {
            "reasoning",
            "coding",
            "agentic_coding",
            "mathematics",
            "data_analysis",
            "language",
            "instruction_following",
        })
        self.assertTrue(all(task["semantic_capability_claim"] is False for task in tasks))
        self.assertTrue(all(task["axis_probe_contract_id"] == SYNTHETIC_AXIS_PROBE_CONTRACT_ID for task in tasks))

    def test_known_parameter_contract_checks_formula_and_rectangular_design(self) -> None:
        rows, parameters = self._rows()
        report = validate_known_parameter_contract(rows, parameters)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["group_count"], 2)
        self.assertEqual(report["task_count"], 7)
        self.assertEqual(report["replicate_count"], 1)
        self.assertLessEqual(report["max_linear_predictor_error"], 1e-12)
        self.assertLessEqual(report["max_probability_error"], 1e-12)

        tampered = dict(parameters)
        tampered["formula"] = "logit(p)=wrong"
        with self.assertRaises(DatasetError):
            validate_known_parameter_contract(rows, tampered)

    def test_synthetic_abjc_is_available_only_in_synthetic_scope_and_checks_arithmetic(self) -> None:
        rows, parameters = self._rows()
        validation = run_synthetic_abjc_validation(
            response_rows=rows,
            known_parameters=parameters,
            bootstrap_replicates=20,
            bootstrap_seed=3,
        )
        report = validation["validation_report"]
        self.assertEqual(report["validation_status"], "passed")
        self.assertTrue(report["synthetic_only"])
        self.assertEqual(report["j_publish_statuses"], ["synthetic_only"])
        self.assertEqual(report["c_available_cells"], 14)
        self.assertEqual(report["c_unavailable_cells"], 0)
        self.assertLessEqual(
            report["truth_projection_report"]["max_abs_c_formula_error"], 1e-12
        )
        self.assertIn("not the same as", report["non_commuting_transform_warning"])
        self.assertEqual(
            report["truth_projection_report"]["sampling_check_status"],
            "insufficient_replicates",
        )
        self.assertTrue(all(
            cell["evidence_origin"] == "known_parameter_simulation"
            and cell["publication_scope"] == "synthetic_only"
            for cell in validation["c_matrix"]
        ))


if __name__ == "__main__":
    unittest.main()
