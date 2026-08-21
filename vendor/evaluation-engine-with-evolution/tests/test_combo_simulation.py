from __future__ import annotations

import unittest
from pathlib import Path

from experience_evaluation.canonical_measurement import CANONICAL_AXES
from experience_evaluation.combo_simulation import COMBOS, TASKS, simulate_combo_tasks


ROOT = Path(__file__).resolve().parents[1]


class ComboSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = simulate_combo_tasks(
            config_dir=ROOT / "config",
            a_path=ROOT / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl",
            b_path=ROOT / "data/builtin_b/2026-08-21/capability_baselines.jsonl",
            seed=20260821,
            noise_sd=0.12,
        )

    def test_six_combos_cover_seven_axes_from_work_tasks(self) -> None:
        self.assertEqual(self.report["combo_count"], 6)
        self.assertEqual(self.report["task_count"], 6)
        self.assertEqual(len(self.report["execution_results"]), len(COMBOS) * len(TASKS))
        self.assertTrue(self.report["checks"]["all_axes_projected"])
        for subject in self.report["subjects"]:
            self.assertEqual({row["dimension_id"] for row in subject["cells"]}, set(CANONICAL_AXES))

    def test_common_pairings_recover_model_and_agent_effects(self) -> None:
        checks = self.report["checks"]
        self.assertTrue(checks["same_model_sol_agentic_codex_beats_aider"])
        self.assertTrue(checks["same_model_fable_agentic_claude_beats_mini"])
        self.assertTrue(checks["stronger_model_higher_math_j"])
        self.assertTrue(checks["same_model_c_contrast_signs_match"])
        self.assertGreaterEqual(checks["agentic_c_hat_tracks_c_true"], 0.5)
        self.assertTrue(checks["raw_c_is_difficulty_biased"])
        self.assertTrue(checks["expected_effects_hold"])

    def test_math_j_uses_only_math_task_atoms(self) -> None:
        math_cell = next(
            row for row in self.report["subjects"][0]["cells"]
            if row["dimension_id"] == "mathematics"
        )
        self.assertEqual(
            set(math_cell["source_atom_ids"]),
            {"competition_math", "applied_math_hardness", "symbolic_integration"},
        )
        self.assertNotIn("logical_correctness", math_cell["source_atom_ids"])

    def test_results_are_atom_metrics_not_livebench_items(self) -> None:
        sample = self.report["execution_results"][0]
        self.assertTrue(sample["metrics"])
        self.assertNotIn("success", sample["metrics"])
        self.assertTrue(all("_" in key for key in sample["metrics"]))


if __name__ == "__main__":
    unittest.main()
