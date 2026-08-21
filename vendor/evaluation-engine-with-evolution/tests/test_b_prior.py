from __future__ import annotations

import unittest

from experience_evaluation.b_prior import (
    build_default_b,
    is_family_usable_b_observation,
    specialize_b,
)
from experience_evaluation.canonical_measurement import CANONICAL_AXES
from experience_evaluation.public_dataset import DatasetError


class BPriorTests(unittest.TestCase):
    def test_default_b_is_seven_axis_weak_positive_logit_prior(self) -> None:
        cells = build_default_b(
            harness_id="unknown-shell",
            harness_revision_id=None,
            dimensions=CANONICAL_AXES,
        )
        self.assertEqual(len(cells), 7)
        self.assertTrue(all(cell["canonical_value"] == 0.15 for cell in cells))
        self.assertTrue(all(cell["canonical_stderr"] == 0.75 for cell in cells))
        self.assertTrue(all(cell["calibration_status"] == "default_prior_for_c" for cell in cells))
        self.assertTrue(all(cell["scale_id"] == "canonical_logit/v0.1" for cell in cells))

    def test_family_observation_specializes_prior_by_normal_normal_shrinkage(self) -> None:
        prior = build_default_b(
            harness_id="h", harness_revision_id="h@1", dimensions=["coding"]
        )[0]
        observed = {
            "baseline_id":"observed","harness_id":"h","dimension_id":"coding",
            "value":0.45,"stderr":0.1,"scale_id":"canonical_logit/v0.1",
            "calibration_status":"calibrated_for_c","evidence_tier":"family_observation",
        }
        specialized = specialize_b(prior, observed)
        expected_variance = 1 / (1 / 0.75**2 + 1 / 0.1**2)
        expected_mean = expected_variance * (0.15 / 0.75**2 + 0.45 / 0.1**2)
        self.assertAlmostEqual(specialized["canonical_value"], expected_mean)
        self.assertAlmostEqual(specialized["canonical_stderr"], expected_variance**0.5)
        self.assertEqual(specialized["calibration_status"], "specialized_for_c")
        self.assertEqual(specialized["evidence_tier"], "family_observation_shrunk_to_prior")
        self.assertEqual(specialized["prior_baseline_id"], prior["baseline_id"])
        self.assertEqual(specialized["observed_baseline_id"], "observed")

    def test_legacy_exact_observation_still_specializes(self) -> None:
        prior = build_default_b(
            harness_id="h", harness_revision_id="h@1", dimensions=["coding"]
        )[0]
        observed = {
            "baseline_id":"observed","harness_id":"h","dimension_id":"coding",
            "value":0.45,"stderr":0.1,"scale_id":"canonical_logit/v0.1",
            "evidence_tier":"exact_full_gate",
        }
        specialized = specialize_b(prior, observed)
        self.assertEqual(specialized["calibration_status"], "specialized_for_c")
        self.assertEqual(specialized["observed_evidence_tier"], "exact_full_gate")

    def test_provisional_blocks_cannot_specialize_prior(self) -> None:
        prior = build_default_b(
            harness_id="h", harness_revision_id="h@1", dimensions=["coding"]
        )[0]
        observed = {
            "baseline_id":"provisional","harness_id":"h","dimension_id":"coding",
            "value":0.45,"stderr":0.1,"scale_id":"canonical_logit/v0.1",
            "evidence_tier":"provisional_same_label_blocks",
        }
        self.assertFalse(is_family_usable_b_observation(observed))
        with self.assertRaises(DatasetError):
            specialize_b(prior, observed)


if __name__ == "__main__":
    unittest.main()
