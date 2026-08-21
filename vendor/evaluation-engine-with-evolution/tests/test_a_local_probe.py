from __future__ import annotations

import json
import unittest
from pathlib import Path

from experience_evaluation.a_local_probe import (
    calibrate_local_probe_a,
    overlay_local_a,
    split_results_by_role,
)
from experience_evaluation.public_dataset import DatasetError


ROOT = Path(__file__).resolve().parents[1]


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class LocalProbeATests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = _read_jsonl(ROOT / "examples/local_usage/task_specs.jsonl")
        self.results = _read_jsonl(ROOT / "examples/local_usage/execution_results.jsonl")
        self.identities = _read_jsonl(ROOT / "examples/local_usage/identities.jsonl")
        self.contract = {
            "contract_id": "local-probe-same-taskspec/v0.1",
            "evidence_tier": "local_probe_same_taskspec",
            "min_observed_tasks": 2,
        }
        self.generator = {
            "dimensions": ["reasoning", "coding", "agentic_coding", "mathematics", "data_analysis", "language", "instruction_following"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "attempt_reducer": "mean",
            "subject_failure_score": 0.0,
            "bootstrap_replicates": 20,
            "bootstrap_seed": 20260820,
            "min_coverage": 0.6,
            "comparability_verified": True,
            "comparison_contract_id": "local-usage-controlled-v1",
            "publication_scope": "local_controlled",
            "evidence_origin": "local_controlled_runs",
        }

    def test_splits_baseline_model_out_of_agent_results(self) -> None:
        agent_results, baseline_results = split_results_by_role(self.results, self.identities)
        self.assertTrue(all(row["subject_id"] != "model-baseline" for row in agent_results))
        self.assertTrue(all(row["subject_id"] == "model-baseline" for row in baseline_results))
        self.assertEqual(len(baseline_results), 7)

    def test_same_taskspec_probe_calibrates_all_seven_axes(self) -> None:
        _, baseline_results = split_results_by_role(self.results, self.identities)
        report = calibrate_local_probe_a(
            self.tasks,
            baseline_results,
            self.generator,
            self.contract,
            baseline_subject_id="model-baseline",
            model_config_id="openai-gpt-5-6-terra-max",
        )
        self.assertEqual(report["cell_count"], 7)
        self.assertEqual(
            set(report["approved_dimensions"]),
            {
                "reasoning",
                "coding",
                "agentic_coding",
                "mathematics",
                "data_analysis",
                "language",
                "instruction_following",
            },
        )
        for cell in report["cells"]:
            self.assertEqual(cell["calibration_status"], "calibrated_for_c")
            self.assertEqual(cell["calibration_contract_id"], "local-probe-same-taskspec/v0.1")
            self.assertIsNotNone(cell["canonical_value"])
            self.assertIsNotNone(cell["canonical_stderr"])

    def test_single_task_axis_is_not_calibrated(self) -> None:
        tasks = [row for row in self.tasks if row["task_id"] == "verify-release"]
        results = [
            row for row in self.results
            if row["subject_id"] == "model-baseline" and row["task_id"] == "verify-release"
        ]
        report = calibrate_local_probe_a(
            tasks,
            results,
            self.generator,
            self.contract,
            baseline_subject_id="model-baseline",
            model_config_id="openai-gpt-5-6-terra-max",
        )
        self.assertEqual(report["cells"], [])
        self.assertTrue(any(row["reason"] == "insufficient_items" for row in report["rejected_dimensions"]))

    def test_unverified_comparability_cannot_become_a(self) -> None:
        config = dict(self.generator)
        config["comparability_verified"] = False
        del config["comparison_contract_id"]
        _, baseline_results = split_results_by_role(self.results, self.identities)
        report = calibrate_local_probe_a(
            self.tasks,
            baseline_results,
            config,
            self.contract,
            baseline_subject_id="model-baseline",
            model_config_id="openai-gpt-5-6-terra-max",
        )
        self.assertEqual(report["cells"], [])
        self.assertTrue(all(row["reason"] == "j_not_publishable" for row in report["rejected_dimensions"]))

    def test_overlay_prefers_local_probe_and_keeps_missing_as_missing(self) -> None:
        public = {
            "status": "partial",
            "reasons": ["missing_dimension:agentic_coding"],
            "cells": [
                {
                    "dimension_id": "reasoning",
                    "calibration_status": "calibrated_for_c",
                    "calibration_contract_id": "livebench-current-axis-mean/v0.1",
                    "canonical_value": 1.0,
                }
            ],
        }
        merged = overlay_local_a(
            public,
            [
                {
                    "dimension_id": "reasoning",
                    "calibration_status": "calibrated_for_c",
                    "calibration_contract_id": "local-probe-same-taskspec/v0.1",
                    "canonical_value": 0.2,
                },
                {
                    "dimension_id": "agentic_coding",
                    "calibration_status": "calibrated_for_c",
                    "calibration_contract_id": "local-probe-same-taskspec/v0.1",
                    "canonical_value": 0.1,
                },
            ],
            ["reasoning", "coding", "agentic_coding"],
        )
        self.assertEqual(merged["status"], "partial")
        by_dim = {row["dimension_id"]: row for row in merged["cells"]}
        self.assertEqual(by_dim["reasoning"]["canonical_value"], 0.2)
        self.assertEqual(by_dim["agentic_coding"]["canonical_value"], 0.1)
        self.assertNotIn("coding", by_dim)
        self.assertIn("missing_dimension:coding", merged["reasons"])

    def test_contract_requires_identity(self) -> None:
        with self.assertRaises(DatasetError):
            calibrate_local_probe_a([], [], self.generator, {}, baseline_subject_id="x", model_config_id="y")


if __name__ == "__main__":
    unittest.main()
