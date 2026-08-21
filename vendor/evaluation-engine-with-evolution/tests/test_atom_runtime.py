from __future__ import annotations

import unittest
from pathlib import Path

from experience_evaluation.canonical_measurement import CANONICAL_AXES
from experience_evaluation.combo_simulation import combo_identities, combo_task_specs, simulate_combo_tasks
from experience_evaluation.evaluation_matrix import generate_evaluation_matrix
from experience_evaluation.matrix_pipeline import BuiltinDatasetRegistry, run_matrix_pipeline


ROOT = Path(__file__).resolve().parents[1]


class AtomRuntimeTests(unittest.TestCase):
    def test_pipeline_emits_seven_axis_j_and_c_for_each_agent(self) -> None:
        report = simulate_combo_tasks(
            config_dir=ROOT / "config",
            a_path=ROOT / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl",
            b_path=ROOT / "data/builtin_b/2026-08-21/capability_baselines.jsonl",
        )
        config = {
            "generator": {
                "dimensions": list(CANONICAL_AXES),
                "axis_contract_id": "livebench-capability-seven/v0.1",
                "source_scale_id": "normalized_0_1",
                "canonical_scale_id": "canonical_logit/v0.1",
                "bootstrap_replicates": 0,
                "min_coverage": 0.6,
                "comparability_verified": True,
                "comparison_contract_id": "atom-runtime-test",
                "publication_scope": "local_controlled",
                "evidence_origin": "combo_task_atom_simulation",
                "atom_projection": {"enabled": True, "config_dir": str(ROOT / "config")},
            },
            "calibration_contract": {
                "contract_id": "atom-runtime-c/v0.1",
                "verified": True,
                "scale_id": "canonical_logit/v0.1",
                "independence_assumption": True,
            },
        }
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(ROOT / "data/matrices"),
            task_specs=combo_task_specs(),
            execution_results=report["execution_results"],
            subject_identities=combo_identities(),
            pipeline_config=config,
        )
        subjects = {row["subject_id"] for row in pipeline["j_bundle"]["evaluation_matrix"]}
        self.assertEqual(subjects, {row["subject_id"] for row in combo_identities()})
        self.assertTrue(all(
            row["projection"] == "j_atom_map"
            for row in pipeline["j_bundle"]["evaluation_matrix"]
            if row["value"] is not None
        ))
        self.assertEqual(pipeline["summary"]["c_available_cells"], 42)
        self.assertTrue(all(
            row["stderr"] is None or isinstance(row["stderr"], float)
            for row in pipeline["c_matrix"]
            if row["status"] == "available"
        ))
        agentic = {
            row["subject_id"]: row["canonical_value"]
            for row in pipeline["j_bundle"]["evaluation_matrix"]
            if row["dimension_id"] == "agentic_coding"
        }
        self.assertGreater(agentic["sol-codex"], agentic["sol-aider"])
        self.assertGreater(agentic["fable-claude-code"], agentic["fable-mini-swe"])

    def test_atom_generator_does_not_require_pipeline(self) -> None:
        tasks = [row for row in combo_task_specs() if row["task_id"] == "rebuild-weekly-funnel"]
        results = [{
            "result_id": "one",
            "subject_id": "solo",
            "task_id": "rebuild-weekly-funnel",
            "task_revision": tasks[0]["task_revision"],
            "status": "completed",
            "metrics": {"table_join": 0.9, "table_reformat": 0.9, "event_sequence": 0.9},
        }]
        config = {
            "dimensions": ["data_analysis", "mathematics"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "atom_projection": {"enabled": True, "config_dir": str(ROOT / "config")},
        }
        cells = {
            row["dimension_id"]: row
            for row in generate_evaluation_matrix(tasks, results, config)["evaluation_matrix"]
        }
        self.assertEqual(cells["data_analysis"]["projection"], "j_atom_map")
        self.assertAlmostEqual(cells["data_analysis"]["value"], 0.9)
        self.assertIsNone(cells["mathematics"]["value"])


if __name__ == "__main__":
    unittest.main()
