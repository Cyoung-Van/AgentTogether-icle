from __future__ import annotations

import math
import unittest

from experience_evaluation.a_calibration import calibrate_a_baselines
from experience_evaluation.public_dataset import DatasetError


class ACalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = {
            "contract_id": "livebench-current-axis-mean/v0.1",
            "approved_category_map": {
                "Reasoning": "reasoning",
                "Coding": "coding",
                "Agentic Coding": "agentic_coding",
                "Mathematics": "mathematics",
                "Data Analysis": "data_analysis",
                "Language": "language",
                "IF": "instruction_following",
            },
            "evidence_tier": "livebench_current_axis_mean",
            "min_items": 2,
        }

    def test_current_approved_axes_are_calibrated_with_logit_and_stderr(self) -> None:
        observations = [
            {"record_id":"r1","model_id":"openai-gpt-5-6-terra-max","category":"Reasoning","score":80.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"r2","model_id":"openai-gpt-5-6-terra-max","category":"Reasoning","score":90.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"c1","model_id":"openai-gpt-5-6-terra-max","category":"Coding","score":70.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"c2","model_id":"openai-gpt-5-6-terra-max","category":"Coding","score":80.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"m1","model_id":"openai-gpt-5-6-terra-max","category":"Mathematics","score":90.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"m2","model_id":"openai-gpt-5-6-terra-max","category":"Mathematics","score":80.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"x1","model_id":"openai-gpt-5-6-terra-max","category":"NotALiveBenchCategory","score":50.0,"score_status":"observed","freshness_status":"active_current"},
        ]
        report = calibrate_a_baselines(observations, self.contract, as_of="2026-08-19")
        cells = {row["dimension_id"]: row for row in report["cells"]}
        self.assertEqual(report["cell_count"], 3)
        self.assertEqual(cells["reasoning"]["calibration_status"], "calibrated_for_c")
        self.assertAlmostEqual(cells["reasoning"]["value"], 0.85)
        self.assertAlmostEqual(cells["reasoning"]["stderr"], 0.05)
        self.assertAlmostEqual(cells["reasoning"]["canonical_value"], math.log(0.85 / 0.15))
        self.assertEqual(cells["reasoning"]["canonical_scale_id"], "canonical_logit/v0.1")
        self.assertAlmostEqual(cells["mathematics"]["value"], 0.85)
        self.assertNotIn("tool_use", cells)
        self.assertTrue(any(row["reason"] == "category_not_approved" for row in report["skipped_observations"]))

    def test_single_item_axis_is_not_calibrated(self) -> None:
        observations = [
            {"record_id":"r1","model_id":"m","category":"Reasoning","score":80.0,"score_status":"observed","freshness_status":"active_current"},
        ]
        report = calibrate_a_baselines(observations, self.contract, as_of="2026-08-19")
        self.assertEqual(report["cells"], [])
        self.assertEqual(report["rejected_groups"][0]["reason"], "insufficient_items")

    def test_history_and_invalid_scores_are_rejected(self) -> None:
        observations = [
            {"record_id":"old","model_id":"m","category":"Reasoning","score":80.0,"score_status":"observed","freshness_status":"superseded"},
            {"record_id":"bad","model_id":"m","category":"Reasoning","score":120.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"ok1","model_id":"m","category":"Coding","score":50.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"ok2","model_id":"m","category":"Coding","score":60.0,"score_status":"observed","freshness_status":"active_current"},
        ]
        report = calibrate_a_baselines(observations, self.contract, as_of="2026-08-19")
        self.assertEqual([row["dimension_id"] for row in report["cells"]], ["coding"])
        reasons = {row["reason"] for row in report["skipped_observations"]}
        self.assertIn("not_active_current", reasons)
        self.assertIn("score_outside_0_100", reasons)

    def test_unknown_score_status_is_skipped_not_scored_as_zero(self) -> None:
        observations = [
            {"record_id":"u1","model_id":"m","category":"Reasoning","score":80.0,"score_status":"unknown","freshness_status":"active_current"},
            {"record_id":"u2","model_id":"m","category":"Reasoning","score":0.0,"score_status":"unknown","freshness_status":"active_current"},
            {"record_id":"ok1","model_id":"m","category":"Coding","score":50.0,"score_status":"observed","freshness_status":"active_current"},
            {"record_id":"ok2","model_id":"m","category":"Coding","score":60.0,"score_status":"observed","freshness_status":"active_current"},
        ]
        report = calibrate_a_baselines(observations, self.contract, as_of="2026-08-19")
        self.assertEqual([row["dimension_id"] for row in report["cells"]], ["coding"])
        self.assertEqual(
            {row["record_id"] for row in report["skipped_observations"] if row["reason"] == "score_not_observed"},
            {"u1", "u2"},
        )

    def test_missing_freshness_status_is_not_treated_as_current(self) -> None:
        observations = [
            {"record_id":"n1","model_id":"m","category":"Reasoning","score":80.0,"score_status":"observed"},
            {"record_id":"n2","model_id":"m","category":"Reasoning","score":90.0,"score_status":"observed"},
        ]
        report = calibrate_a_baselines(observations, self.contract, as_of="2026-08-19")
        self.assertEqual(report["cells"], [])
        self.assertEqual(
            {row["reason"] for row in report["skipped_observations"]},
            {"freshness_status_missing"},
        )

    def test_contract_cannot_invent_or_remap_livebench_categories(self) -> None:
        invented = dict(self.contract)
        invented["approved_category_map"] = {"Vibes": "reasoning"}
        with self.assertRaises(DatasetError):
            calibrate_a_baselines([], invented, as_of="2026-08-19")
        remapped = dict(self.contract)
        remapped["approved_category_map"] = {"Coding": "mathematics"}
        with self.assertRaises(DatasetError):
            calibrate_a_baselines([], remapped, as_of="2026-08-19")

    def test_contract_requires_identity(self) -> None:
        with self.assertRaises(DatasetError):
            calibrate_a_baselines([], {}, as_of="2026-08-19")


if __name__ == "__main__":
    unittest.main()
