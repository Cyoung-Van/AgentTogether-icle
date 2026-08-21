from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.evaluation_matrix import (
    generate_evaluation_matrix,
    write_evaluation_bundle,
)
from experience_evaluation.j_a_mapping import load_j_a_mapping_bundle, project_atoms_to_axes
from experience_evaluation.public_dataset import DatasetError


class EvaluationMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tasks = [
            {
                "task_id": "t1", "task_revision": "r1", "task_family": "debug",
                "weight": 2.0, "capability_loadings": {"reasoning": 1.0},
                "metrics": [{"metric_id": "pass", "min": 0, "max": 1, "direction": "maximize", "weight": 1, "required": True}],
            },
            {
                "task_id": "t2", "task_revision": "r1", "task_family": "coding",
                "weight": 1.0, "capability_loadings": {"reasoning": 0.5, "coding": 1.0},
                "metrics": [{"metric_id": "errors", "min": 0, "max": 10, "direction": "minimize", "weight": 1, "required": True}],
            },
        ]
        self.results = [
            {"result_id": "a1", "subject_id": "agent-a", "task_id": "t1", "task_revision": "r1", "status": "completed", "metrics": {"pass": 1}},
            {"result_id": "a2", "subject_id": "agent-a", "task_id": "t2", "task_revision": "r1", "status": "completed", "metrics": {"errors": 2}},
            {"result_id": "b1", "subject_id": "agent-b", "task_id": "t1", "task_revision": "r1", "status": "completed", "metrics": {"pass": 0}},
            {"result_id": "b2", "subject_id": "agent-b", "task_id": "t2", "task_revision": "r1", "status": "infrastructure_failure", "metrics": {}},
        ]
        self.config = {
            "dimensions": ["reasoning", "coding"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "attempt_reducer": "mean",
            "subject_failure_score": 0.0,
            "bootstrap_replicates": 200,
            "bootstrap_seed": 17,
            "min_coverage": 0.75,
            "comparability_verified": True,
            "comparison_contract_id": "fixture-contract-v1",
        }

    def _generate(self):
        return generate_evaluation_matrix(self.tasks, self.results, self.config)

    def test_generates_weighted_dimension_cells_with_j_canonical_projection(self) -> None:
        bundle = self._generate()
        cells = {(row["subject_id"], row["dimension_id"]): row for row in bundle["evaluation_matrix"]}
        reasoning = cells[("agent-a", "reasoning")]
        coding = cells[("agent-a", "coding")]
        self.assertAlmostEqual(reasoning["value"], 0.96)
        self.assertAlmostEqual(reasoning["canonical_value"], math.log(0.96 / 0.04))
        self.assertAlmostEqual(coding["value"], 0.8)
        self.assertAlmostEqual(coding["canonical_value"], math.log(4))
        self.assertEqual(reasoning["source_scale_id"], "normalized_0_1")
        self.assertEqual(reasoning["canonical_scale_id"], "canonical_logit/v0.1")
        self.assertEqual(reasoning["axis_contract_id"], "livebench-capability-seven/v0.1")
        self.assertEqual(reasoning["scale_transform"], "logit")
        self.assertEqual(cells[("agent-a", "reasoning")]["coverage"], 1.0)
        self.assertNotIn("total_score", bundle["summary"])
        self.assertFalse(any(row["dimension_id"] == "total" for row in bundle["evaluation_matrix"]))

    def test_generator_summary_freezes_j_axis_and_scale_contract(self) -> None:
        summary = self._generate()["summary"]
        self.assertEqual(summary["axis_contract_id"], "livebench-capability-seven/v0.1")
        self.assertEqual(summary["source_scale_id"], "normalized_0_1")
        self.assertEqual(summary["canonical_scale_id"], "canonical_logit/v0.1")

    def test_single_task_dimension_does_not_claim_zero_uncertainty(self) -> None:
        tasks = [
            {"task_id":"only","task_revision":"r1","weight":1,"capability_loadings":{"instruction_following":1},"metrics":[{"metric_id":"pass","min":0,"max":1,"direction":"maximize"}]}
        ]
        results = [
            {"result_id":"r","subject_id":"agent","task_id":"only","task_revision":"r1","status":"completed","metrics":{"pass":1}}
        ]
        config = {"dimensions":["instruction_following"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":100,"bootstrap_seed":1}
        cell = generate_evaluation_matrix(tasks, results, config)["evaluation_matrix"][0]
        self.assertEqual(cell["uncertainty_status"], "insufficient_task_diversity")
        self.assertIsNone(cell["stderr"])
        self.assertIsNone(cell["ci_low"])
        self.assertIsNone(cell["ci_high"])

    def test_sufficient_coverage_without_comparability_is_shadow_only(self) -> None:
        config = dict(self.config)
        config["comparability_verified"] = False
        config.pop("comparison_contract_id")
        cells = generate_evaluation_matrix(self.tasks, self.results, config)["evaluation_matrix"]
        self.assertTrue(all(
            cell["publish_status"] == "shadow_only_unverified_comparability"
            for cell in cells if cell["value"] is not None and cell["coverage"] >= 0.75
        ))

    def test_missing_is_not_zero_and_coverage_is_reported(self) -> None:
        bundle = self._generate()
        cells = {(row["subject_id"], row["dimension_id"]): row for row in bundle["evaluation_matrix"]}
        reasoning = cells[("agent-b", "reasoning")]
        coding = cells[("agent-b", "coding")]
        self.assertEqual(reasoning["value"], 0.0)
        self.assertAlmostEqual(reasoning["coverage"], 0.8)
        self.assertEqual(reasoning["publish_status"], "publishable")
        self.assertIsNone(coding["value"])
        self.assertEqual(coding["coverage"], 0.0)
        self.assertEqual(coding["publish_status"], "insufficient_coverage")
        missing = next(row for row in bundle["objective_matrix"] if row["subject_id"] == "agent-b" and row["task_id"] == "t2")
        self.assertEqual(missing["score_status"], "not_observed")
        self.assertIsNone(missing["task_score"])

    def test_subject_failure_is_scored_zero_but_infrastructure_failure_is_missing(self) -> None:
        results = list(self.results)
        results[-1] = {"result_id": "b2", "subject_id": "agent-b", "task_id": "t2", "task_revision": "r1", "status": "subject_failure", "metrics": {}}
        bundle = generate_evaluation_matrix(self.tasks, results, self.config)
        row = next(row for row in bundle["objective_matrix"] if row["subject_id"] == "agent-b" and row["task_id"] == "t2")
        self.assertEqual(row["task_score"], 0.0)
        self.assertEqual(row["score_status"], "observed_subject_failure")

    def test_attempts_are_reduced_explicitly(self) -> None:
        results = list(self.results) + [
            {"result_id": "a1-repeat", "subject_id": "agent-a", "task_id": "t1", "task_revision": "r1", "status": "completed", "metrics": {"pass": 0}}
        ]
        bundle = generate_evaluation_matrix(self.tasks, results, self.config)
        row = next(row for row in bundle["objective_matrix"] if row["subject_id"] == "agent-a" and row["task_id"] == "t1")
        self.assertEqual(row["attempt_count"], 2)
        self.assertEqual(row["task_score"], 0.5)
        self.assertEqual(row["source_result_ids"], ["a1", "a1-repeat"])

    def test_invalid_metric_is_rejected_and_not_silently_clamped(self) -> None:
        results = list(self.results) + [
            {"result_id": "bad", "subject_id": "agent-c", "task_id": "t2", "task_revision": "r1", "status": "completed", "metrics": {"errors": 99}}
        ]
        bundle = generate_evaluation_matrix(self.tasks, results, self.config)
        rejection = next(row for row in bundle["rejected_results"] if row["result_id"] == "bad")
        self.assertEqual(rejection["reason"], "metric_out_of_range")
        row = next(row for row in bundle["objective_matrix"] if row["subject_id"] == "agent-c" and row["task_id"] == "t2")
        self.assertIsNone(row["task_score"])

    def test_bootstrap_is_deterministic_and_emits_uncertainty(self) -> None:
        first = self._generate()
        second = self._generate()
        self.assertEqual(first, second)
        cell = next(row for row in first["evaluation_matrix"] if row["subject_id"] == "agent-a" and row["dimension_id"] == "reasoning")
        self.assertIsNotNone(cell["stderr"])
        self.assertLessEqual(cell["ci_low"], cell["value"])
        self.assertGreaterEqual(cell["ci_high"], cell["value"])
        self.assertEqual(cell["bootstrap_replicates_used"], 200)

    def test_atom_projection_overlays_j_from_mapped_metric_ids(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tasks = [{
            "task_id": "fix-flaky-ci",
            "task_revision": "r1",
            "weight": 1,
            "capability_loadings": {"coding": 0.4, "agentic_coding": 0.7},
            "metrics": [
                {"metric_id": "repo_localization", "min": 0, "max": 1, "direction": "maximize", "weight": 1, "required": True},
                {"metric_id": "multi_file_agentic_edit", "min": 0, "max": 1, "direction": "maximize", "weight": 1, "required": True},
            ],
        }]
        results = [{
            "result_id": "r1",
            "subject_id": "codex",
            "task_id": "fix-flaky-ci",
            "task_revision": "r1",
            "status": "completed",
            "metrics": {"repo_localization": 0.8, "multi_file_agentic_edit": 0.2},
        }]
        config = {
            "dimensions": ["coding", "agentic_coding", "mathematics"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "min_coverage": 0.6,
            "comparability_verified": True,
            "comparison_contract_id": "atom-fixture",
            "atom_projection": {"enabled": True, "config_dir": str(root / "config")},
        }
        cells = {
            row["dimension_id"]: row
            for row in generate_evaluation_matrix(tasks, results, config)["evaluation_matrix"]
        }
        expected = {
            row["target_id"]: row
            for row in project_atoms_to_axes(
                {"repo_localization": 0.8, "multi_file_agentic_edit": 0.2},
                load_j_a_mapping_bundle(root / "config"),
                q_mask={"coding": 0.4, "agentic_coding": 0.7, "mathematics": 0.0},
            )["cells"]
        }
        self.assertEqual(cells["agentic_coding"]["projection"], "j_atom_map")
        self.assertEqual(cells["coding"]["projection"], "j_atom_map")
        self.assertAlmostEqual(cells["agentic_coding"]["value"], expected["agentic_coding"]["value"])
        self.assertAlmostEqual(cells["coding"]["value"], expected["coding"]["value"])
        self.assertNotAlmostEqual(cells["agentic_coding"]["value"], 0.5)
        self.assertNotAlmostEqual(cells["coding"]["value"], 0.5)
        self.assertIsNone(cells["mathematics"]["value"])
        self.assertEqual(cells["mathematics"]["projection"], "capability_loadings")

    def test_atom_projection_rejects_a_constant_stderr_fallback(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = {
            "dimensions": ["coding"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "atom_projection": {
                "enabled": True,
                "config_dir": str(root / "config"),
                "default_stderr": 0.15,
            },
        }
        with self.assertRaises(DatasetError):
            generate_evaluation_matrix([], [], config)

    def test_atom_q_mask_uses_observed_tasks_only(self) -> None:
        root = Path(__file__).resolve().parents[1]
        from experience_evaluation.combo_simulation import combo_task_specs

        tasks = combo_task_specs()
        results = [{
            "result_id": "solo-data",
            "subject_id": "solo",
            "task_id": "rebuild-weekly-funnel",
            "task_revision": tasks[0]["task_revision"],
            "status": "completed",
            "metrics": {"table_join": 0.9, "table_reformat": 0.9, "event_sequence": 0.9},
        }]
        config = {
            "dimensions": list(self.config["dimensions"]) + [
                "agentic_coding", "mathematics", "data_analysis", "language", "instruction_following"
            ],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "min_coverage": 0.6,
            "atom_projection": {"enabled": True, "config_dir": str(root / "config")},
        }
        cells = {
            row["dimension_id"]: row
            for row in generate_evaluation_matrix(tasks, results, config)["evaluation_matrix"]
        }
        self.assertEqual(cells["data_analysis"]["projection"], "j_atom_map")
        self.assertAlmostEqual(cells["data_analysis"]["coverage"], 1.0)
        self.assertEqual(cells["mathematics"]["projection"], "capability_loadings")
        self.assertIsNone(cells["mathematics"]["value"])
        self.assertEqual(cells["reasoning"]["projection"], "capability_loadings")

    def test_atom_overlay_does_not_pool_off_axis_atoms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        from experience_evaluation.combo_simulation import combo_task_specs, simulate_combo_tasks

        report = simulate_combo_tasks(
            config_dir=root / "config",
            a_path=root / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl",
            b_path=root / "data/builtin_b/2026-08-21/capability_baselines.jsonl",
        )
        config = {
            "dimensions": [
                "reasoning", "coding", "agentic_coding", "mathematics",
                "data_analysis", "language", "instruction_following",
            ],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "min_coverage": 0.6,
            "comparability_verified": True,
            "comparison_contract_id": "atom-no-pool",
            "atom_projection": {"enabled": True, "config_dir": str(root / "config")},
        }
        cells = [
            row for row in generate_evaluation_matrix(
                combo_task_specs(), report["execution_results"], config
            )["evaluation_matrix"]
            if row["subject_id"] == "sol-codex" and row["dimension_id"] == "mathematics"
        ]
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["projection"], "j_atom_map")
        self.assertEqual(
            set(cells[0]["source_atom_ids"]),
            {"competition_math", "applied_math_hardness", "symbolic_integration"},
        )
        self.assertNotIn("logical_correctness", cells[0]["source_atom_ids"])

    def test_overlay_without_mapped_atoms_does_not_publish_capability_j(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tasks = [{
            "task_id": "tool-only",
            "task_revision": "r1",
            "weight": 1,
            "capability_loadings": {"agentic_coding": 1.0},
            "metrics": [
                {"metric_id": "tool_invocation", "min": 0, "max": 1, "direction": "maximize", "required": True},
            ],
        }]
        results = [{
            "result_id": "r1",
            "subject_id": "solo",
            "task_id": "tool-only",
            "task_revision": "r1",
            "status": "completed",
            "metrics": {"tool_invocation": 0.25},
        }]
        config = {
            "dimensions": ["agentic_coding"],
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "bootstrap_replicates": 0,
            "comparability_verified": True,
            "comparison_contract_id": "unlinked-only",
            "atom_projection": {"enabled": True, "config_dir": str(root / "config")},
        }
        cell = generate_evaluation_matrix(tasks, results, config)["evaluation_matrix"][0]
        self.assertIsNone(cell["value"])
        self.assertEqual(cell["projection"], "j_atom_map")
        self.assertEqual(cell["publish_status"], "insufficient_coverage")

    def test_bundle_writer_hashes_every_output(self) -> None:
        bundle = self._generate()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "bundle"
            manifest = write_evaluation_bundle(output, bundle, generator_config=self.config)
            self.assertTrue((output / "objective_matrix.jsonl").exists())
            self.assertTrue((output / "evaluation_matrix.jsonl").exists())
            self.assertTrue((output / "rejected_results.jsonl").exists())
            self.assertTrue((output / "summary.json").exists())
            for name, expected in manifest["files"].items():
                actual = hashlib.sha256((output / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)
            saved = json.loads((output / "manifest.json").read_text())
            self.assertEqual(saved, manifest)


if __name__ == "__main__":
    unittest.main()
