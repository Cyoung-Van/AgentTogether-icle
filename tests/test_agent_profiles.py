"""Agent decision profile: external base prior + exact local observations."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.agent_profiles import CAPABILITY_DOMAINS, build_agent_profile  # noqa: E402
from icle.cost import record_actual  # noqa: E402
from icle.external_evaluation import SCHEMA as EXTERNAL_SCHEMA, save_snapshot  # noqa: E402
from icle.experience import record_observation  # noqa: E402


class AgentDecisionProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fixture bindings must not depend on the developer's installed CLI model.
        local_config = patch("icle.external_evaluation._configured_model_identity", return_value=None)
        local_config.start()
        self.addCleanup(local_config.stop)
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()
        now = datetime.now(timezone.utc)
        snapshot = {
            "schema_version": EXTERNAL_SCHEMA,
            "source_id": "swebench_verified",
            "source_url": "https://www.swebench.com/",
            "authority": "benchmark_maintainer",
            "benchmark": "SWE-bench Verified",
            "benchmark_version": "Verified",
            "domain": "coding",
            "metric": "resolved_rate",
            "license": "MIT",
            "evaluation_scope": "agent_model",
            "provenance_tier": "official_leaderboard",
            "retrieved_at": now.isoformat(),
            "fresh_until": (now + timedelta(days=10)).isoformat(),
            "content_hash": "sha256:fixture",
            "parser_version": "fixture",
            "records": [{
                "source_id": "swebench_verified",
                "benchmark": "SWE-bench Verified",
                "domain": "coding",
                "model": "model-x",
                "model_aliases": ["modelx"],
                "evaluated_agent": "agent-a",
                "agent_aliases": ["agenta"],
                "score": 0.8,
                "result_date": now.date().isoformat(),
                "retrieved_at": now.isoformat(),
                "metadata": {"n_trials": 40},
            }],
        }
        save_snapshot(self.store, snapshot)
        (self.store / "agent-models.json").write_text(
            json.dumps({"agent-a": {"model": "model-x", "provider": "test"}}),
            encoding="utf-8",
        )

    def _observation(self, *, outcome: float = 1.0, cost: float | None = 0.12) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "schema_version": "icle-experience-observation/v0.1",
            "observation_id": "obs-" + now.replace(":", "").replace("+", "").replace(".", ""),
            "task_id": "t-1",
            "run_id": "run-1",
            "task_profile": {
                "primary_type": "CODING", "subtype": "debugging", "difficulty": "D3",
                "risk": "R1", "project_id": "project-a", "context_tokens": 0, "steps": 1,
            },
            "route": {
                "agent": "agent-a", "model": "model-x", "provider": "test",
                "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE",
            },
            "usage": {
                "input_tokens": 1000, "output_tokens": 200, "cache_read_tokens": 10,
                "cache_write_tokens": 0, "reasoning_tokens": 0, "tool_calls": {},
                "usage_source": "exact_provider",
            },
            "cost": {"cash_cost": cost, "per_step": [cost] if cost is not None else []},
            "duration_ms": 12000,
            "outcome": {
                "mark": "accept", "accept_without_rework": outcome,
                "user_corrections": 0, "rating": None,
            },
            "model_version": {"experience_model_version": "fixture", "similarity_version": "fixture"},
            "created_at": now,
        }

    def test_profile_keeps_base_local_and_fused_values_separate(self) -> None:
        record_observation(self.store, self._observation())
        profile = build_agent_profile(self.store, agent_id="agent-a", metadata={"capabilities": ["shell"]})
        self.assertEqual(profile["schema_version"], "icle-agent-decision-profile/v0.2")
        coding = next(item for item in profile["capabilities"] if item["domain"] == "coding")
        self.assertEqual(coding["base_prior"]["score"], 0.8)
        self.assertEqual(coding["base_prior"]["match_strength"], "agent+model")
        self.assertEqual(coding["local_experience"]["sample_count"], 1)
        self.assertIsNone(coding["local_experience"]["quality"])
        self.assertEqual(coding["local_experience"]["quality_source"], "deferred_to_measurement")
        self.assertEqual(coding["quality"]["source"], "base")
        self.assertIsNotNone(coding["quality"]["score"])
        self.assertEqual(profile["measurement"]["ranking"], False)
        self.assertNotIn("total", profile["measurement"])
        self.assertEqual(len(profile["measurement"]["axes"]), 7)
        self.assertEqual(coding["cost"]["typical_task_cost"]["expected"], 0.12)
        self.assertIsNotNone(coding["cost"]["cost_to_accept"])
        self.assertIsNone(coding["cost"]["base_cost_prior"])
        self.assertIn("coding", profile["decision_summary"]["measured_domains"])
        self.assertEqual(profile["coverage"]["exact_model_observation_count"], 1)
        self.assertIn("coding", profile["coverage"]["domains_with_exact_model_experience"])
        self.assertEqual(coding["evidence_coverage"]["status"], "partial")

    def test_catalog_model_gets_base_cost_without_local_observations(self) -> None:
        from icle.tariff import builtin_snapshots

        (self.store / "agent-models.json").write_text(
            json.dumps({"codex": {"model": "gpt-5.6-sol", "provider": "openai"}}),
            encoding="utf-8",
        )
        profile = build_agent_profile(self.store, agent_id="codex", metadata={"capabilities": ["shell"]})
        coding = next(item for item in profile["capabilities"] if item["domain"] == "coding")
        self.assertIsNone(coding["local_experience"]["quality"])
        self.assertIsNone(coding["cost"]["typical_task_cost"]["expected"])
        self.assertEqual(coding["cost"]["api_equivalent_task_cost"]["source"], "base_workload_prior")
        self.assertEqual(coding["cost"]["api_equivalent_task_cost"]["expected"], 0.66)
        self.assertIsNone(coding["cost"]["execution_task_cost"]["expected"])
        self.assertIn("coding", profile["coverage"]["domains_with_api_cost"])
        self.assertEqual(coding["evidence_coverage"]["status"], "partial")
        base = coding["cost"]["base_cost_prior"]
        self.assertEqual(base["workload_version"], "coding-agent-d2-d4-v0.1")
        self.assertEqual(base["expected"], 0.66)
        self.assertEqual(coding["cost"]["pricing_source"]["revision"], "973329e9864154d429a7d739fb6a552fe03a9b3e")
        self.assertIn("openai/gpt-5.6-sol", builtin_snapshots())

    def test_local_cash_cost_overrides_unknown_cli_billing_mode(self) -> None:
        (self.store / "agent-models.json").write_text(
            json.dumps({"codex": {"model": "gpt-5.6-sol", "provider": "openai"}}),
            encoding="utf-8",
        )
        observation = self._observation(cost=0.12)
        observation["route"] = {
            "agent": "codex", "model": "gpt-5.6-sol", "provider": "openai",
            "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE",
        }
        record_observation(self.store, observation)
        profile = build_agent_profile(self.store, agent_id="codex")
        coding = next(item for item in profile["capabilities"] if item["domain"] == "coding")
        self.assertEqual(coding["cost"]["typical_task_cost"]["expected"], 0.12)
        self.assertEqual(coding["cost"]["execution_task_cost"]["expected"], 0.12)
        self.assertEqual(coding["cost"]["execution_task_cost"]["cash_cost_status"], "measured")
        self.assertEqual(coding["cost"]["api_equivalent_task_cost"]["expected"], 0.66)

    def test_actual_request_cost_stays_separate_from_task_cost(self) -> None:
        record_observation(self.store, self._observation(cost=None))
        record_actual(self.store, {
            "provider": "test", "model": "model-x", "cash_cost": 0.003,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        profile = build_agent_profile(self.store, agent_id="agent-a")
        coding = next(item for item in profile["capabilities"] if item["domain"] == "coding")
        self.assertIsNone(coding["cost"]["typical_task_cost"]["expected"])
        self.assertEqual(coding["cost"]["local_call_cost"]["p50"], 0.003)
        self.assertEqual(coding["cost"]["local_call_cost"]["scope"], "per_llm_call")

    def test_unresolved_agent_has_no_fabricated_score_or_cost(self) -> None:
        profile = build_agent_profile(self.store, agent_id="unknown-agent")
        self.assertEqual({item["domain"] for item in profile["capabilities"]}, set(CAPABILITY_DOMAINS))
        for capability in profile["capabilities"]:
            self.assertIsNone(capability["quality"]["score"])
            self.assertEqual(capability["quality"]["source"], "none")
            self.assertEqual(capability["evidence_coverage"]["status"], "missing")
            self.assertEqual(capability["cost"]["billing_mode"], "UNKNOWN")
            self.assertEqual(capability["cost"]["unknown_reason"], "model_identity_unresolved")
            self.assertIsNone(capability["cost"]["api_equivalent_cost_to_accept"])
            self.assertIsNone(capability["cost"]["execution_cost_to_accept"])


if __name__ == "__main__":
    unittest.main()
