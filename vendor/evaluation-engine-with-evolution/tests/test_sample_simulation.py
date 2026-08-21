from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.sample_simulation import (
    build_limited_experiment_report,
    build_typical_sample_dataset,
    simulate_block_bootstrap,
    simulate_empirical_logit,
    simulate_known_parameter_logit,
    write_sample_simulation_experiment,
)
from experience_evaluation.public_dataset import DatasetError


class SampleSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observations = [
            {
                "observation_id": "o1", "source_id": "source", "source_record_id": "r1",
                "benchmark_id": "bench", "agent_id": "h1", "agent_version": None,
                "model_id": "model", "model_revision": None, "identity_status": "source_metadata",
                "evidence_granularity": "task_level", "environment_revision": None,
            },
            {
                "observation_id": "o2", "source_id": "source", "source_record_id": "r2",
                "benchmark_id": "bench", "agent_id": "h2", "agent_version": None,
                "model_id": "model", "model_revision": None, "identity_status": "source_metadata",
                "evidence_granularity": "task_level", "environment_revision": None,
            },
        ]
        self.outcomes = [
            {"outcome_id": "y11", "observation_id": "o1", "source_id": "source", "benchmark_id": "bench", "task_id": "t1", "outcome": 1, "outcome_status": "observed"},
            {"outcome_id": "y12", "observation_id": "o1", "source_id": "source", "benchmark_id": "bench", "task_id": "t2", "outcome": 0, "outcome_status": "observed"},
            {"outcome_id": "y13", "observation_id": "o1", "source_id": "source", "benchmark_id": "bench", "task_id": "only-h1", "outcome": 1, "outcome_status": "observed"},
            {"outcome_id": "y21", "observation_id": "o2", "source_id": "source", "benchmark_id": "bench", "task_id": "t1", "outcome": 0, "outcome_status": "observed"},
            {"outcome_id": "y22", "observation_id": "o2", "source_id": "source", "benchmark_id": "bench", "task_id": "t2", "outcome": 1, "outcome_status": "observed"},
        ]

    def _sample(self):
        return build_typical_sample_dataset(
            self.observations,
            self.outcomes,
            benchmark_id="bench",
            model_id="model",
            harness_ids=["h1", "h2"],
        )

    def test_typical_groups_use_only_common_observed_tasks_and_keep_provenance(self) -> None:
        sample = self._sample()
        self.assertEqual(len(sample["groups"]), 2)
        self.assertEqual(len(sample["responses"]), 4)
        self.assertEqual({row["task_id"] for row in sample["responses"]}, {"t1", "t2"})
        self.assertTrue(all(row["evidence_origin"] == "observed" for row in sample["responses"]))
        self.assertTrue(all(row["response_semantics"] == "aggregated_binary_task_response" for row in sample["responses"]))
        self.assertTrue(all(row["observation_id"] and row["outcome_id"] for row in sample["responses"]))
        self.assertTrue(all(group["environment_consistency_status"] == "unverified" for group in sample["groups"]))
        self.assertEqual(sample["statistics"]["common_task_count"], 2)

    def test_group_comparability_requires_equal_complete_configuration(self) -> None:
        common = {
            "benchmark_version": "1", "model_revision": "model-rev", "provider_route": "direct",
            "reasoning_effort": "high", "environment_revision": "env-a",
            "resource_limits": {"cpu": 2}, "timeout_policy": "900s",
            "context_policy": "128k", "attempt_policy": "pass@1",
        }
        for index, observation in enumerate(self.observations):
            observation.update(common)
            observation["agent_version"] = f"harness-{index}"
        for outcome in self.outcomes:
            outcome.update({"task_revision": "task-rev", "fixture_hash": "fixture", "verifier_revision": "verifier"})
        exact = self._sample()
        self.assertEqual(exact["statistics"]["fit_status"], "eligible_for_exact_fit_review")
        self.assertEqual(exact["statistics"]["comparability_failures"], [])

        self.observations[1]["environment_revision"] = "env-b"
        mismatch = self._sample()
        self.assertEqual(mismatch["statistics"]["fit_status"], "exploratory_only")
        self.assertIn("environment_revision_mismatch", mismatch["statistics"]["comparability_failures"])

    def test_outcome_identity_mismatch_is_rejected(self) -> None:
        self.outcomes[0]["model_id"] = "different"
        with self.assertRaises(DatasetError):
            self._sample()

    def test_dataset_id_changes_when_observed_content_changes(self) -> None:
        first = self._sample()
        self.outcomes[0]["outcome"] = 0
        second = self._sample()
        self.assertNotEqual(first["dataset_id"], second["dataset_id"])
        self.assertNotEqual(first["dataset_content_sha256"], second["dataset_content_sha256"])

    def test_block_bootstrap_resamples_whole_task_blocks_and_is_deterministic(self) -> None:
        sample = self._sample()
        first = simulate_block_bootstrap(sample, replicates=3, seed=17)
        second = simulate_block_bootstrap(sample, replicates=3, seed=17)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 3 * 2 * 2)
        self.assertTrue(all(row["evidence_origin"] == "bootstrap_resampled" for row in first))
        for replicate in range(3):
            rows = [row for row in first if row["simulation_replicate"] == replicate]
            for draw_index in range(2):
                block = [row for row in rows if row["task_draw_index"] == draw_index]
                self.assertEqual(len(block), 2)
                self.assertEqual(len({row["source_task_id"] for row in block}), 1)

    def test_block_bootstrap_rejects_duplicate_group_inside_task_block(self) -> None:
        sample = self._sample()
        sample["responses"][2]["group_id"] = sample["responses"][0]["group_id"]
        with self.assertRaises(DatasetError):
            simulate_block_bootstrap(sample, replicates=1, seed=1)

    def test_parametric_simulation_is_seeded_and_never_claims_observed_origin(self) -> None:
        sample = self._sample()
        rows, parameters = simulate_empirical_logit(sample, replicates=4, seed=23)
        rows_again, parameters_again = simulate_empirical_logit(sample, replicates=4, seed=23)
        self.assertEqual(rows, rows_again)
        self.assertEqual(parameters, parameters_again)
        self.assertEqual(len(rows), 4 * 2 * 2)
        self.assertTrue(all(row["evidence_origin"] == "parametric_simulation" for row in rows))
        self.assertTrue(all(row["outcome"] in (0, 1) for row in rows))
        self.assertTrue(all("outcome_id" not in row and "observation_id" not in row for row in rows))

    def test_known_parameter_simulation_persists_ground_truth(self) -> None:
        sample = self._sample()
        group_ids = sorted(group["group_id"] for group in sample["groups"])
        task_ids = sorted({row["task_id"] for row in sample["responses"]})
        rows, truth, recovery = simulate_known_parameter_logit(
            sample,
            group_effects={group_ids[0]: -0.5, group_ids[1]: 0.5},
            task_difficulties={task_ids[0]: -1.0, task_ids[1]: 1.0},
            intercept=0.0,
            replicates=200,
            seed=31,
        )
        self.assertEqual(len(rows), 200 * 2 * 2)
        self.assertEqual(truth["group_effects"], {group_ids[0]: -0.5, group_ids[1]: 0.5})
        self.assertTrue(all(row["evidence_origin"] == "known_parameter_simulation" for row in rows))
        self.assertEqual(recovery["recovery_status"], "completed")
        self.assertIn("group_effect_rmse", recovery)

    def test_limited_experiment_uses_paired_common_tasks_and_stays_exploratory(self) -> None:
        sample = self._sample()
        report = build_limited_experiment_report(sample)
        self.assertEqual(report["pairwise_comparisons"][0]["matched_task_count"], 2)
        self.assertEqual(report["pairwise_comparisons"][0]["a_wins"], 1)
        self.assertEqual(report["pairwise_comparisons"][0]["b_wins"], 1)
        self.assertEqual(report["inference_status"], "exploratory_only")
        self.assertFalse(report["eligible_for_agent_ranking"])

    def test_writer_physically_separates_observed_and_simulated_files(self) -> None:
        sample = self._sample()
        with tempfile.TemporaryDirectory() as tmp:
            result = write_sample_simulation_experiment(
                Path(tmp), sample, bootstrap_replicates=2, parametric_replicates=2,
                known_parameter_replicates=10, seed=7
            )
            self.assertFalse(Path(result["observed_dir"]).is_absolute())
            self.assertFalse(Path(result["simulated_dir"]).is_absolute())
            observed_dir = Path(tmp) / result["observed_dir"]
            simulated_dir = Path(tmp) / result["simulated_dir"]
            self.assertNotEqual(observed_dir, simulated_dir)
            self.assertTrue((observed_dir / "observed_task_responses.jsonl").exists())
            self.assertTrue((observed_dir / "observed_outcome_matrix.jsonl").exists())
            self.assertTrue((observed_dir / "limited_experiment_report.json").exists())
            self.assertTrue((simulated_dir / "bootstrap_responses.jsonl").exists())
            self.assertTrue((simulated_dir / "bootstrap_outcome_matrices.jsonl").exists())
            self.assertTrue((simulated_dir / "parametric_outcome_matrices.jsonl").exists())
            self.assertTrue((simulated_dir / "known_parameter_responses.jsonl").exists())
            self.assertTrue((simulated_dir / "known_parameters.json").exists())
            self.assertTrue((simulated_dir / "parameter_recovery_report.json").exists())
            self.assertTrue((simulated_dir / "simulation_experiment_report.json").exists())
            self.assertTrue((Path(tmp) / "experiment_manifest.sha256").exists())
            observed_rows = [json.loads(line) for line in (observed_dir / "observed_task_responses.jsonl").read_text().splitlines()]
            simulated_rows = [json.loads(line) for line in (simulated_dir / "parametric_responses.jsonl").read_text().splitlines()]
            self.assertTrue(all(row["evidence_origin"] == "observed" for row in observed_rows))
            self.assertTrue(all(row["evidence_origin"] != "observed" for row in simulated_rows))


if __name__ == "__main__":
    unittest.main()
