from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.public_dataset import (
    canonical_model_id,
    build_b_matched_dataset,
    DatasetError,
    build_dataset,
    merge_repository_metadata,
    parse_livebench_subtasks,
    normalize_id,
    parse_agent_psychometrics,
    parse_tb21_submission,
    stable_record_id,
)


FIXTURES = Path(__file__).parent / "fixtures"


class PublicDatasetTests(unittest.TestCase):
    @staticmethod
    def _exact_observation(observation_id: str, harness: str, *, effort: str = "high", model_id: str = "m1", observed_at: str = "2026-08-19") -> dict:
        return {
            "observation_id": observation_id,
            "source_id": "fixture-source",
            "source_record_id": observation_id,
            "benchmark_id": "bench",
            "benchmark_version": "1.0",
            "agent_id": harness,
            "agent_version": "1.0.0",
            "model_id": model_id,
            "model_revision": "rev-001",
            "provider_route": "provider/direct",
            "reasoning_effort": effort,
            "environment_revision": "env-sha256:001",
            "resource_limits": {"cpu": 2, "memory_gb": 4},
            "timeout_policy": "wall-900s",
            "context_policy": "context-128k",
            "attempt_policy": "pass@1-single-attempt",
            "identity_status": "source_metadata",
            "evidence_granularity": "task_level",
            "observed_at": observed_at,
        }

    @staticmethod
    def _exact_outcome(outcome_id: str, observation_id: str, *, task_id: str = "same", outcome: int = 1) -> dict:
        return {
            "outcome_id": outcome_id,
            "observation_id": observation_id,
            "source_id": "fixture-source",
            "benchmark_id": "bench",
            "task_id": task_id,
            "task_revision": "task-rev-001",
            "fixture_hash": "sha256:fixture-001",
            "verifier_revision": "verifier-1.0",
            "outcome": outcome,
            "outcome_status": "observed",
        }

    def test_b_matching_requires_same_task_and_exact_model(self) -> None:
        observations = [
            self._exact_observation("o1", "h1"),
            self._exact_observation("o2", "h2"),
            self._exact_observation("o3", "h3", model_id="m2"),
        ]
        outcomes = [
            self._exact_outcome("y1", "o1", outcome=1),
            self._exact_outcome("y2", "o2", outcome=0),
            self._exact_outcome("y3", "o3", outcome=1),
            self._exact_outcome("y4", "o2", task_id="different", outcome=1),
        ]
        result = build_b_matched_dataset(observations, outcomes)
        self.assertEqual(len(result["matched_blocks"]), 1)
        block = result["matched_blocks"][0]
        self.assertEqual(block["task_id"], "same")
        self.assertEqual(block["model_id"], "m1")
        self.assertEqual({x["harness_id"] for x in block["members"]}, {"h1", "h2"})
        self.assertEqual(len(result["pairwise_comparisons"]), 1)
        reasons = {row["reason"] for row in result["rejected"]}
        self.assertIn("no_exact_peer", reasons)

    def test_b_matching_separates_reasoning_effort(self) -> None:
        observations = [
            self._exact_observation("o1", "h1", effort="high"),
            self._exact_observation("o2", "h2", effort="max"),
        ]
        outcomes = [
            self._exact_outcome("y1", "o1", outcome=1),
            self._exact_outcome("y2", "o2", outcome=0),
        ]
        result = build_b_matched_dataset(observations, outcomes)
        self.assertEqual(result["matched_blocks"], [])
        self.assertTrue(all(r["reason"] == "no_exact_peer" for r in result["rejected"]))
        self.assertTrue(all("reasoning_effort_mismatch" in r["reasons"] for r in result["rejected"]))

    def test_b_every_exact_gate_field_is_required(self) -> None:
        observation_fields = (
            "benchmark_version", "agent_version", "model_revision", "provider_route",
            "reasoning_effort", "environment_revision", "resource_limits",
            "timeout_policy", "context_policy", "attempt_policy",
        )
        for missing_field in observation_fields:
            with self.subTest(missing_field=missing_field):
                left = self._exact_observation("o1", "h1")
                right = self._exact_observation("o2", "h2")
                left[missing_field] = None
                result = build_b_matched_dataset(
                    [left, right],
                    [self._exact_outcome("y1", "o1"), self._exact_outcome("y2", "o2")],
                )
                self.assertEqual(result["matched_blocks"], [])

        for missing_fields in (("verifier_revision",), ("task_revision", "fixture_hash")):
            with self.subTest(missing_fields=missing_fields):
                left_outcome = self._exact_outcome("y1", "o1")
                for field in missing_fields:
                    left_outcome[field] = None
                result = build_b_matched_dataset(
                    [self._exact_observation("o1", "h1"), self._exact_observation("o2", "h2")],
                    [left_outcome, self._exact_outcome("y2", "o2")],
                )
                self.assertEqual(result["matched_blocks"], [])

    def test_b_rejects_observation_outcome_identity_conflicts(self) -> None:
        observation = self._exact_observation("o1", "h1")
        outcome = self._exact_outcome("y1", "o1")
        outcome["model_id"] = "different-model"
        result = build_b_matched_dataset([observation], [outcome])
        self.assertEqual(result["matched_blocks"], [])
        self.assertEqual(result["rejected"][0]["reason"], "model_identity_mismatch")
        self.assertEqual(result["rejected"][0]["stage"], "record_consistency")

    def test_b_missing_exact_fields_is_provisional_and_reports_all_gate_failures(self) -> None:
        observations = [
            {"observation_id":"o1","source_id":"s","source_record_id":"r1","benchmark_id":"bench","agent_id":"h1","agent_version":None,"model_id":"m1","reasoning_effort":None,"identity_status":"source_metadata","evidence_granularity":"task_level","observed_at":"2025-01-01"},
            {"observation_id":"o2","source_id":"s","source_record_id":"r2","benchmark_id":"bench","agent_id":"h2","agent_version":None,"model_id":"m1","reasoning_effort":None,"identity_status":"source_metadata","evidence_granularity":"task_level","observed_at":"2025-01-02"},
        ]
        outcomes = [
            {"outcome_id":"y1","observation_id":"o1","source_id":"s","benchmark_id":"bench","task_id":"t","outcome":1,"outcome_status":"observed"},
            {"outcome_id":"y2","observation_id":"o2","source_id":"s","benchmark_id":"bench","task_id":"t","outcome":0,"outcome_status":"observed"},
        ]
        result = build_b_matched_dataset(observations, outcomes)
        self.assertEqual(result["matched_blocks"], [])
        self.assertEqual(len(result["provisional_blocks"]), 1)
        rejection = result["rejected"][0]
        self.assertEqual(rejection["stage"], "exact_gate_completeness")
        self.assertIn("harness_revision_unknown", rejection["reasons"])
        self.assertIn("model_revision_unknown", rejection["reasons"])
        self.assertIn("environment_revision_unknown", rejection["reasons"])

    def test_b_non_comparable_model_labels_never_enter_exact(self) -> None:
        observations = [
            self._exact_observation("o1", "h1", model_id="multiple"),
            self._exact_observation("o2", "h2", model_id="multiple"),
        ]
        outcomes = [self._exact_outcome("y1", "o1"), self._exact_outcome("y2", "o2")]
        result = build_b_matched_dataset(observations, outcomes)
        self.assertEqual(result["matched_blocks"], [])
        self.assertTrue(all(r["reason"] == "model_identity_non_comparable" for r in result["rejected"]))

    def test_b_unknown_versions_keep_dated_submissions_separate_and_traceable(self) -> None:
        observations = [
            {"observation_id":"o1","source_id":"s","source_record_id":"submission-a","benchmark_id":"bench","agent_id":"h1","agent_version":None,"model_id":"m1","reasoning_effort":None,"identity_status":"source_metadata","evidence_granularity":"task_level","observed_at":"2025-01-01"},
            {"observation_id":"o2","source_id":"s","source_record_id":"submission-b","benchmark_id":"bench","agent_id":"h1","agent_version":None,"model_id":"m1","reasoning_effort":None,"identity_status":"source_metadata","evidence_granularity":"task_level","observed_at":"2025-02-01"},
            {"observation_id":"o3","source_id":"s","source_record_id":"submission-c","benchmark_id":"bench","agent_id":"h2","agent_version":None,"model_id":"m1","reasoning_effort":None,"identity_status":"source_metadata","evidence_granularity":"task_level","observed_at":"2025-01-01"},
        ]
        outcomes = [
            {"outcome_id":"y1","observation_id":"o1","source_id":"s","benchmark_id":"bench","task_id":"t","outcome":0,"outcome_status":"observed"},
            {"outcome_id":"y2","observation_id":"o2","source_id":"s","benchmark_id":"bench","task_id":"t","outcome":1,"outcome_status":"observed"},
            {"outcome_id":"y3","observation_id":"o3","source_id":"s","benchmark_id":"bench","task_id":"t","outcome":1,"outcome_status":"observed"},
        ]
        result = build_b_matched_dataset(observations, outcomes)
        block = result["provisional_blocks"][0]
        h1_members = [member for member in block["members"] if member["harness_id"] == "h1"]
        self.assertEqual(len(h1_members), 2)
        self.assertEqual({member["mean_outcome"] for member in h1_members}, {0.0, 1.0})
        self.assertEqual(
            {ref["observation_id"] for member in h1_members for ref in member["evidence_refs"]},
            {"o1", "o2"},
        )

    def test_livebench_becomes_matrix_a_not_harness_data(self) -> None:
        records = parse_livebench_subtasks(
            FIXTURES / "livebench_table.csv",
            FIXTURES / "livebench_categories.json",
            source_id="livebench-2026-06-25",
        )
        self.assertEqual(len(records), 4)
        coding = next(
            row for row in records
            if row["source_model_id"] == "gpt-5.6-terra-max"
            and row["benchmark_item_id"] == "code_generation"
        )
        self.assertEqual(coding["matrix"], "A")
        self.assertEqual(coding["category"], "Coding")
        self.assertEqual(coding["score"], 80.0)
        self.assertNotIn("harness_id", coding)

    def test_authoritative_model_org_overrides_gateway_prefix(self) -> None:
        self.assertEqual(
            canonical_model_id("cursor/grok-4.5", "xAI"),
            "xai-grok-4-5",
        )
        self.assertEqual(
            canonical_model_id("gemini/gemini-3.1-pro-preview", "Google"),
            "google-gemini-3-1-pro-preview",
        )

    def test_record_ids_do_not_collide_after_slug_normalization(self) -> None:
        self.assertNotEqual(
            stable_record_id("outcome", "task/a"),
            stable_record_id("outcome", "task-a"),
        )

    def test_repository_metadata_is_a_sidecar_not_identity(self) -> None:
        harnesses = [
            {
                "harness_id": "codex",
                "display_name": "Codex",
                "source_url": "https://github.com/openai/codex",
            }
        ]
        merged = merge_repository_metadata(
            harnesses,
            {
                "https://github.com/openai/codex": {
                    "stargazers_count": 100,
                    "forks_count": 20,
                    "pushed_at": "2026-08-19T00:00:00Z",
                    "archived": False,
                    "license": {"spdx_id": "Apache-2.0"},
                }
            },
        )
        self.assertEqual(merged[0]["harness_id"], "codex")
        self.assertEqual(merged[0]["repository"]["stars"], 100)
        self.assertEqual(merged[0]["repository"]["license"], "Apache-2.0")

    def test_normalize_id_is_stable_and_keeps_versions_distinct(self) -> None:
        self.assertEqual(normalize_id("Claude Opus 4.6"), "claude-opus-4-6")
        self.assertEqual(normalize_id("openai/gpt-5.6-terra"), "openai-gpt-5-6-terra")
        self.assertNotEqual(normalize_id("GPT-5.6 Terra"), normalize_id("GPT-5.6 Luna"))

    def test_agent_psychometrics_expands_task_level_outcomes(self) -> None:
        pairs, outcomes = parse_agent_psychometrics(
            FIXTURES / "agent_psychometrics.jsonl",
            source_id="agent-psychometrics-terminalbench-v2",
            benchmark_id="terminal-bench-2.0",
        )
        self.assertEqual(len(pairs), 2)
        self.assertEqual(len(outcomes), 4)
        claude = next(pair for pair in pairs if pair["agent_id"] == "claude-code")
        self.assertEqual(claude["model_id"], "anthropic-claude-opus-4-6")
        self.assertEqual(claude["agent_version"], "2.0.31")
        self.assertEqual(claude["model_revision"], "claude-opus-4-6-20260205")
        self.assertEqual(claude["provider_route"], "anthropic")
        self.assertEqual(claude["detail_url_identity_status"], "parsed_from_source_url")
        self.assertIn("detail_url", claude)
        deepseek = next(pair for pair in pairs if pair["agent_id"] == "terminus-2")
        self.assertEqual(deepseek["model_id"], "deepseek-v3-2")
        self.assertEqual(claude["evidence_granularity"], "task_level")
        self.assertEqual({row["outcome"] for row in outcomes}, {0, 1})

    def test_agent_psychometrics_uses_upstream_identity_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "responses.jsonl"
            path.write_text(
                json.dumps({"subject_id": "opaque-row", "responses": {"task": 1}}) + "\n",
                encoding="utf-8",
            )
            pairs, _ = parse_agent_psychometrics(
                path,
                source_id="fixture",
                benchmark_id="swebench-verified",
                identity_map={
                    "opaque-row": {"model": "GPT-5", "harness": "SWE-agent"}
                },
            )
            self.assertEqual(pairs[0]["agent_id"], "swe-agent")
            self.assertEqual(pairs[0]["model_id"], "openai-gpt-5")
            self.assertEqual(pairs[0]["identity_status"], "upstream_mapping")

    def test_tb21_submission_preserves_exact_identity_and_effort(self) -> None:
        record = parse_tb21_submission(
            FIXTURES / "tb21_submission.json",
            source_id="terminal-bench-2.1-official",
        )
        self.assertEqual(record["agent_id"], "codex")
        self.assertEqual(record["agent_version"], "0.144.1")
        self.assertEqual(record["model_id"], "openai-gpt-5-6-terra")
        self.assertEqual(record["reasoning_effort"], "max")
        self.assertEqual(record["evidence_granularity"], "submission_summary")
        self.assertEqual(record["n_trials"], 445)

    def test_build_dataset_rejects_duplicate_catalog_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = {
                "models": [
                    {"model_id": "same", "display_name": "A"},
                    {"model_id": "same", "display_name": "B"},
                ],
                "harnesses": [],
            }
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            with self.assertRaises(DatasetError):
                build_dataset(
                    catalog_path=catalog_path,
                    agent_psychometrics_sources=[],
                    tb21_submission_dir=None,
                    output_dir=root / "out",
                )

    def test_build_dataset_writes_manifest_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = {
                "models": [
                    {
                        "model_id": "claude-opus-4-6",
                        "display_name": "Claude Opus 4.6",
                        "provider": "anthropic",
                        "source_url": "https://example.test/model",
                    }
                ],
                "harnesses": [
                    {
                        "harness_id": "claude-code",
                        "display_name": "Claude Code",
                        "source_url": "https://example.test/agent",
                    }
                ],
            }
            catalog_path = root / "catalog.json"
            catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
            output = root / "out"
            build_dataset(
                catalog_path=catalog_path,
                agent_psychometrics_sources=[
                    {
                        "path": FIXTURES / "agent_psychometrics.jsonl",
                        "source_id": "fixture-ap",
                        "benchmark_id": "terminal-bench-2.0",
                    }
                ],
                tb21_submission_dir=None,
                output_dir=output,
            )
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            coverage = json.loads((output / "coverage.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "experience-evaluation-dataset-manifest/v0.1")
            self.assertEqual(coverage["task_outcome_records"], 4)
            self.assertEqual(coverage["catalog_models"], 1)
            self.assertIn("fixture-ap", manifest["source_ids"])

    def test_unresolved_identities_are_reported_not_treated_as_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            responses = root / "responses.jsonl"
            responses.write_text(
                json.dumps({"subject_id": "opaque", "responses": {"task": 1}}) + "\n",
                encoding="utf-8",
            )
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps({"models": [], "harnesses": []}), encoding="utf-8")
            result = build_dataset(
                catalog_path=catalog,
                agent_psychometrics_sources=[
                    {"path": responses, "source_id": "fixture", "benchmark_id": "bench"}
                ],
                tb21_submission_dir=None,
                output_dir=root / "out",
            )
            coverage = result["coverage"]
            self.assertEqual(coverage["unresolved_identity_observations"], 1)
            self.assertEqual(coverage["task_outcomes_with_resolved_identity"], 0)
            self.assertEqual(coverage["observed_models_not_in_catalog"], [])


if __name__ == "__main__":
    unittest.main()
