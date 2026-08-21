from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from experience_evaluation.canonical_measurement import CANONICAL_AXES
from experience_evaluation.j_a_mapping import (
    A_CATALOG_ID,
    MAPPING_ID,
    load_j_a_mapping_bundle,
    project_atoms_to_axes,
    project_atoms_to_subtasks,
    validate_j_a_mapping_bundle,
)
from experience_evaluation.public_dataset import DatasetError


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"
LIVEBENCH_CATEGORIES = ROOT / "data/raw/2026-08-19/livebench/categories_2026_06_25.json"


class JAMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = load_j_a_mapping_bundle(CONFIG)

    def test_frozen_bundle_covers_every_livebench_current_subtask_and_axis(self) -> None:
        livebench = json.loads(LIVEBENCH_CATEGORIES.read_text(encoding="utf-8"))
        catalog_by_category = {}
        for row in self.bundle["subtasks"].values():
            catalog_by_category.setdefault(row["livebench_category"], []).append(row["subtask_id"])
        self.assertEqual(set(catalog_by_category), set(livebench))
        for category, items in livebench.items():
            self.assertEqual(sorted(catalog_by_category[category]), sorted(items))
        self.assertEqual(self.bundle["a_catalog"]["catalog_id"], A_CATALOG_ID)
        self.assertEqual(self.bundle["mapping_id"], MAPPING_ID)
        self.assertEqual(set(self.bundle["axis_membership"]), set(CANONICAL_AXES))
        self.assertTrue(self.bundle["unlinked_atom_ids"])

    def test_w_is_max_t_over_subtasks_on_the_axis(self) -> None:
        atom = "code_generation_correctness"
        coding = max(
            self.bundle["T"][(atom, subtask)]
            for subtask in self.bundle["axis_membership"]["coding"]
            if (atom, subtask) in self.bundle["T"]
        )
        agentic = max(
            self.bundle["T"][(atom, subtask)]
            for subtask in self.bundle["axis_membership"]["agentic_coding"]
            if (atom, subtask) in self.bundle["T"]
        )
        self.assertAlmostEqual(self.bundle["W"][(atom, "coding")], coding)
        self.assertAlmostEqual(self.bundle["W"][(atom, "agentic_coding")], agentic)
        self.assertNotIn((atom, "language"), self.bundle["W"])

    def test_axis_projection_is_coverage_weighted_mean_then_logit(self) -> None:
        report = project_atoms_to_axes(
            {"code_generation_correctness": 0.80, "code_completion_fidelity": 0.20},
            self.bundle,
        )
        cells = {row["target_id"]: row for row in report["cells"]}
        coding = cells["coding"]
        expected = (1.0 * 0.80 + 1.0 * 0.20) / 2.0
        self.assertEqual(coding["status"], "projected")
        self.assertAlmostEqual(coding["value"], expected)
        self.assertAlmostEqual(coding["canonical_value"], math.log(expected / (1 - expected)))
        self.assertEqual(cells["mathematics"]["status"], "insufficient_evidence")
        self.assertIsNone(cells["mathematics"]["value"])

    def test_missing_and_unlinked_atoms_are_not_scored_as_zero(self) -> None:
        report = project_atoms_to_axes(
            {"social_inference": 1.0, "tool_invocation": 0.0, "memory_retrieval": 0.9},
            self.bundle,
        )
        cells = {row["target_id"]: row for row in report["cells"]}
        self.assertAlmostEqual(cells["reasoning"]["value"], 1.0)
        self.assertEqual(cells["agentic_coding"]["status"], "insufficient_evidence")
        self.assertEqual(
            set(report["skipped_unlinked_atom_ids"]),
            {"tool_invocation", "memory_retrieval"},
        )

    def test_every_unlinked_atom_stays_out_of_the_projection(self) -> None:
        unlinked = {
            atom["atom_id"]
            for atom in self.bundle["atom_catalog"]["atoms"]
            if atom["atom_id"] not in self.bundle["mapped_atom_ids"]
        }
        self.assertIn("context_seeking", unlinked)
        report = project_atoms_to_axes(
            {atom: 1.0 for atom in unlinked},
            self.bundle,
        )
        self.assertEqual(set(report["skipped_unlinked_atom_ids"]), unlinked)
        for row in report["cells"]:
            self.assertEqual(row["status"], "insufficient_evidence")
            self.assertIsNone(row["value"])
            self.assertIsNone(row["canonical_value"])
        for atom in unlinked:
            self.assertNotIn(atom, {key[0] for key in self.bundle["T"]})
            self.assertNotIn(atom, {key[0] for key in self.bundle["W"]})

    def test_q_mask_can_zero_an_axis_without_renormalizing_others(self) -> None:
        scores = {"code_generation_correctness": 0.9, "social_inference": 1.0}
        masked = project_atoms_to_axes(scores, self.bundle, q_mask={"coding": 0.0})
        cells = {row["target_id"]: row for row in masked["cells"]}
        self.assertEqual(cells["coding"]["status"], "insufficient_evidence")
        self.assertEqual(cells["reasoning"]["status"], "projected")

    def test_misspelled_q_mask_axis_is_rejected_not_ignored(self) -> None:
        with self.assertRaises(DatasetError):
            project_atoms_to_axes(
                {"code_generation_correctness": 0.9},
                self.bundle,
                q_mask={"agentic-coding": 0.0},
            )

    def test_subtask_projection_uses_t_not_w(self) -> None:
        report = project_atoms_to_subtasks(
            {"table_join": 0.5, "table_reformat": 1.0},
            self.bundle,
        )
        cells = {row["target_id"]: row for row in report["cells"]}
        self.assertAlmostEqual(cells["tablejoin"]["value"], 0.5)
        self.assertAlmostEqual(cells["tablereformat"]["value"], 1.0)
        self.assertEqual(cells["consecutive_events"]["status"], "insufficient_evidence")

    def test_unlinked_atom_edge_is_rejected(self) -> None:
        mapping = json.loads((CONFIG / "j_to_a_mapping.json").read_text(encoding="utf-8"))
        mapping["edges"].append(
            {"atom_id": "tool_invocation", "a_subtask_id": "python", "weight": 0.2}
        )
        with self.assertRaises(DatasetError):
            validate_j_a_mapping_bundle(
                self.bundle["a_catalog"],
                self.bundle["atom_catalog"],
                mapping,
            )


if __name__ == "__main__":
    unittest.main()
