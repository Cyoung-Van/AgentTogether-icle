"""v0.4 P13 (Batch 5) tests: eligibility, utility/confidence, routes, decision."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.cost import record_actual  # noqa: E402
from icle.judge import record_result_mark  # noqa: E402
from icle.recommendation import (  # noqa: E402
    eligible_agents,
    list_decisions,
    recommend_routes,
    save_decision,
)


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def test_eligible_filters_unlinked(self) -> None:
        eligible = eligible_agents(available=["kimi", "hermes", "codex"], linked=["kimi", "hermes"])
        self.assertEqual(eligible, ["kimi", "hermes"])
        # no linked → falls back to all available
        self.assertEqual(eligible_agents(available=["kimi"]), ["kimi"])

    def test_cold_start_agent_not_zero(self) -> None:
        """新 agent 无证据: Evidence=NONE + exploration,绝不 score=0 当最差。"""
        result = recommend_routes(
            self.store, title="t", description="d", difficulty="D3",
            available=["kimi", "hermes"],
        )
        routes = {r["agents"][0]: r for r in result["routes"]}
        self.assertEqual(routes["kimi"]["evidence"], "NONE")
        self.assertTrue(routes["kimi"]["exploration"])
        # exploration agents sort last, but they are NOT scored as zero
        self.assertIsNotNone(routes["kimi"]["expected_utility"])

    def test_experienced_agent_ranks_higher(self) -> None:
        record_result_mark(self.store, episode_id="ep-1", mark="accept", agent_id="kimi")
        record_result_mark(self.store, episode_id="ep-2", mark="accept", agent_id="kimi")
        result = recommend_routes(
            self.store, title="t", description="d", difficulty="D3",
            available=["kimi", "hermes"], include_cold_start=False,
        )
        kimi = next(r for r in result["routes"] if r["agents"] == ["kimi"])
        hermes = next(r for r in result["routes"] if r["agents"] == ["hermes"])
        self.assertGreater(kimi["expected_utility"], hermes["expected_utility"])
        self.assertNotEqual(kimi["evidence"], "NONE")
        self.assertTrue(any("accepted" in w for w in kimi["why"]))

    def test_cost_is_attributed_to_one_agent_and_changes_utility(self) -> None:
        record_result_mark(self.store, episode_id="ep-k", mark="accept", agent_id="kimi")
        record_result_mark(self.store, episode_id="ep-h", mark="accept", agent_id="hermes")
        before = recommend_routes(
            self.store, title="t", description="d", available=["kimi", "hermes"],
            include_cold_start=False,
        )
        before_routes = {route["agents"][0]: route for route in before["routes"]}
        record_actual(self.store, {
            "schema_version": "icle-cost-actual/v0.1",
            "agent": "kimi", "provider": "kimi-code", "model": "kimi-for-coding",
            "cash_cost": 2.5, "created_at": "2026-08-18T00:00:00+00:00",
        })
        after = recommend_routes(
            self.store, title="t", description="d", available=["kimi", "hermes"],
            include_cold_start=False,
        )
        routes = {route["agents"][0]: route for route in after["routes"]}
        self.assertEqual(routes["kimi"]["cost_sum"], 2.5)
        self.assertEqual(routes["hermes"]["cost_sum"], 0.0)
        self.assertLess(routes["kimi"]["expected_utility"], before_routes["kimi"]["expected_utility"])
        self.assertEqual(routes["hermes"]["expected_utility"], before_routes["hermes"]["expected_utility"])

    def test_high_difficulty_gets_decompose_route(self) -> None:
        result = recommend_routes(
            self.store, title="big", description="refactor modules", difficulty="D4", risk="R2",
            available=["kimi", "hermes", "codex"], include_cold_start=False,
        )
        strategies = [r["strategy"] for r in result["routes"]]
        self.assertIn("DECOMPOSE", strategies)

    def test_utility_differs_from_confidence(self) -> None:
        """1 条 accept: utility 可能不低,但 confidence 低(样本少)。"""
        record_result_mark(self.store, episode_id="ep-1", mark="accept", agent_id="kimi")
        result = recommend_routes(
            self.store, title="t", description="d", difficulty="D3",
            available=["kimi"], include_cold_start=False,
        )
        route = result["routes"][0]
        self.assertGreater(route["expected_utility"], 0)
        self.assertLess(route["confidence"], 0.3)
        self.assertTrue(route["uncertainty"])

    def test_save_and_list_decisions(self) -> None:
        decision = save_decision(
            self.store, task_id="t-1",
            chosen_route={"label": "kimi", "strategy": "DIRECT"},
            alternatives=[{"label": "hermes"}],
            reason="cheapest fast",
        )
        self.assertTrue(decision["decision_id"].startswith("rd-"))
        decisions = list_decisions(self.store)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["reason"], "cheapest fast")


if __name__ == "__main__":
    unittest.main()
