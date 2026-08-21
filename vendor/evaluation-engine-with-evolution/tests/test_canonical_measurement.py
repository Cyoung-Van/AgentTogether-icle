from __future__ import annotations

import math
import unittest

from experience_evaluation.canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_AXES,
    CANONICAL_SCALE_ID,
    align_probability_cell,
    validate_axis_subset,
)
from experience_evaluation.public_dataset import DatasetError


class CanonicalMeasurementTests(unittest.TestCase):
    def test_seven_axis_contract_follows_livebench_categories(self) -> None:
        self.assertEqual(CANONICAL_AXES, (
            "reasoning",
            "coding",
            "agentic_coding",
            "mathematics",
            "data_analysis",
            "language",
            "instruction_following",
        ))
        self.assertEqual(AXIS_CONTRACT_ID, "livebench-capability-seven/v0.1")

    def test_probability_and_stderr_are_transformed_to_additive_logit(self) -> None:
        aligned = align_probability_cell(0.8, 0.04)
        self.assertAlmostEqual(aligned["canonical_value"], math.log(4))
        self.assertAlmostEqual(aligned["canonical_stderr"], 0.04 / (0.8 * 0.2))
        self.assertEqual(aligned["canonical_scale_id"], CANONICAL_SCALE_ID)
        self.assertFalse(aligned["boundary_clipped"])

    def test_probability_boundaries_are_explicitly_clipped_not_infinite(self) -> None:
        aligned = align_probability_cell(1.0, None)
        self.assertTrue(aligned["boundary_clipped"])
        self.assertTrue(math.isfinite(aligned["canonical_value"]))
        self.assertIsNone(aligned["canonical_stderr"])

    def test_noncanonical_axis_is_rejected(self) -> None:
        with self.assertRaises(DatasetError):
            validate_axis_subset(["reasoning", "helpfulness"])


if __name__ == "__main__":
    unittest.main()
