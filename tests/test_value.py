"""value.py:Value Engine — 五维 RouteCandidate + Pareto + policy ranking。"""

import json
import tempfile
import unittest
from pathlib import Path

from icle.value import (
    DEFAULT_POLICY,
    POLICIES,
    build_route_candidate,
    pareto_keep,
    rank_candidates,
)

PROFILE = {"primary_type": "CODING", "subtype": "debugging", "difficulty": "D3", "risk": "R1"}


def _write_j(store: Path, agent: str, model: str, value: float) -> None:
    from icle.evolution import _safe_subject, subject_id_for

    route = {"agent": agent, "provider_id": "", "model": model}
    path = store / "evolution" / "layers" / f"{_safe_subject(subject_id_for(route))}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "j": {
            "status": "observed",
            "cells": [
                {"dimension_id": "coding", "value": value},
                {"dimension_id": "agentic_coding", "value": value},
            ],
        }
    }), encoding="utf-8")


class ValueEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()
        # 造两个 route 的经验:codex(高质量高成本)vs kimi(低成本低质量)
        from icle.experience import build_observation, record_observation

        def seed(agent: str, model: str, provider: str, input_tokens: int,
                 duration_ms: int, cash: float, mark: str) -> None:
            run = {"run_id": f"{agent}-{input_tokens}", "steps": [{
                "step_id": "S1", "agent": f"{agent}/{model}", "recommended_agent": f"{agent}/{model}",
                "context_policy": "PROJECT_STATE", "status": "completed", "duration_ms": duration_ms,
                "usage_source": "exact_provider",
                "usage": {"input_tokens": input_tokens, "output_tokens": 5000,
                          "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
                          "tool_calls": {}, "usage_source": "exact_provider"},
                "cost": {"cash_cost": cash}}]}
            obs = build_observation(task={"task_id": run["run_id"], "title": "t", "description": "d",
                                          "project_id": "icle", "profile": {**PROFILE, "project_id": "icle"}},
                                    run=run, outcome={"mark": mark, "user_corrections": 0})
            record_observation(self.store, obs)

        for _ in range(6):  # codex:高质量高成本
            seed("codex", "gpt-4.1", "openai", 120000, 160000, 0.19, "accept")
        for _ in range(6):  # kimi:低成本低质量
            seed("kimi", "kimi-k3", "kimi", 40000, 60000, 0.05, "edit")
        _write_j(self.store, "codex", "gpt-4.1", 2.2)
        _write_j(self.store, "kimi", "kimi-k3", -0.4)

    def _routes(self) -> list[dict]:
        return [
            {"agent": "codex", "model": "gpt-4.1", "provider": "openai",
             "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"},
            {"agent": "kimi", "model": "kimi-k3", "provider": "kimi",
             "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"},
        ]

    def test_build_route_candidate_five_dimensions(self) -> None:
        candidate = build_route_candidate(self.store, profile=PROFILE, route=self._routes()[0])
        self.assertEqual(candidate["agent"], "codex")
        self.assertIsNotNone(candidate["expected_quality"])
        self.assertIsNotNone(candidate["expected_cost"]["expected"])
        self.assertIsNotNone(candidate["expected_time_s"]["p50"])
        self.assertIsNotNone(candidate["expected_intervention"])
        self.assertIn("confidence", candidate)
        self.assertIn("evidence", candidate)
        self.assertIsInstance(candidate["reason"], list)

    def test_cost_first_prefers_cheap(self) -> None:
        routes = [build_route_candidate(self.store, profile=PROFILE, route=r, policy="cost_first")
                  for r in self._routes()]
        ranked = rank_candidates(routes, policy="cost_first")
        self.assertEqual(ranked[0]["agent"], "kimi")
        self.assertTrue(ranked[0]["recommended"])

    def test_quality_first_prefers_quality(self) -> None:
        routes = [build_route_candidate(self.store, profile=PROFILE, route=r, policy="quality_first")
                  for r in self._routes()]
        ranked = rank_candidates(routes, policy="quality_first")
        self.assertEqual(ranked[0]["agent"], "codex")

    def test_unknown_policy_rejected(self) -> None:
        from icle.value import ValueError_

        with self.assertRaises(ValueError_):
            build_route_candidate(self.store, profile=PROFILE, route=self._routes()[0], policy="nope")

    def test_pareto_keeps_non_dominated(self) -> None:
        routes = [build_route_candidate(self.store, profile=PROFILE, route=r) for r in self._routes()]
        kept = pareto_keep(routes)
        # codex 质量高成本高 vs kimi 质量低成本低 → 都非被支配(不同维胜出)
        self.assertEqual(len(kept), 2)
        # 人为加一个被支配候选:质量低成本高
        dominated = {"agent": "bad", "expected_quality": 0.1,
                     "expected_cost": {"expected": 5.0}, "expected_time_s": {"p50": 9999},
                     "expected_intervention": 1.0}
        kept2 = pareto_keep(routes + [dominated])
        self.assertNotIn(dominated, kept2)

    def test_local_compute_route_marks_cash_cost_unknown(self) -> None:
        route = {"agent": "ollama", "model": "llama3", "provider": "ollama",
                 "execution_provider": "provider_api", "context_policy": "PROJECT_STATE"}
        candidate = build_route_candidate(self.store, profile=PROFILE, route=route)
        self.assertEqual(candidate["billing_mode"], "LOCAL_COMPUTE")
        self.assertIsNone(candidate["expected_cost"]["expected"])

    def test_subscription_route_marks_cost_unknown(self) -> None:
        route = {"agent": "codex", "model": "gpt-4.1", "provider": "claude-code",
                 "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"}
        candidate = build_route_candidate(self.store, profile=PROFILE, route=route)
        self.assertEqual(candidate["billing_mode"], "SUBSCRIPTION")
        self.assertIsNone(candidate["expected_cost"]["expected"])


if __name__ == "__main__":
    unittest.main()


class AcceptanceTests(unittest.TestCase):
    """计划书 §34 验收实验:经验进入模型 → 下一次相似任务推荐分化。

    Balanced → 推荐质量高的 route;Cost First → 推荐便宜的 route。
    """

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()
        from icle.experience import build_observation, record_observation

        def seed(agent: str, model: str, provider: str, input_tokens: int,
                 duration_ms: int, cash: float, mark: str) -> None:
            run = {"run_id": f"{agent}-{input_tokens}", "steps": [{
                "step_id": "S1", "agent": f"{agent}/{model}", "recommended_agent": f"{agent}/{model}",
                "context_policy": "PROJECT_STATE", "status": "completed", "duration_ms": duration_ms,
                "usage_source": "exact_provider",
                "usage": {"input_tokens": input_tokens, "output_tokens": 5000,
                          "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
                          "tool_calls": {}, "usage_source": "exact_provider"},
                "cost": {"cash_cost": cash}}]}
            obs = build_observation(task={"task_id": run["run_id"], "title": "t", "description": "d",
                                          "project_id": "icle", "profile": {**PROFILE, "project_id": "icle"}},
                                    run=run, outcome={"mark": mark, "user_corrections": 0})
            record_observation(self.store, obs)

        # 高返工率低成本的 route(kimi):便宜但几乎每次都要重来(§18/§34 数值)
        for _ in range(5):
            seed("kimi", "kimi-k3", "kimi", 40000, 80000, 0.04, "redo")
        # 高质量高成本的 route(codex):贵一点但一次过
        for _ in range(5):
            seed("codex", "gpt-4.1", "openai", 120000, 120000, 0.15, "accept")
        _write_j(self.store, "codex", "gpt-4.1", 2.2)
        _write_j(self.store, "kimi", "kimi-k3", -0.4)

    def _routes(self) -> list[dict]:
        return [
            {"agent": "codex", "model": "gpt-4.1", "provider": "openai",
             "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"},
            {"agent": "kimi", "model": "kimi-k3", "provider": "kimi",
             "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"},
        ]

    def test_balanced_prefers_quality_offsets_cost(self) -> None:
        """§34:Balanced → 推荐 codex(质量优势覆盖额外成本)。"""
        candidates = [build_route_candidate(self.store, profile=PROFILE, route=r, policy="balanced")
                      for r in self._routes()]
        ranked = rank_candidates(candidates, policy="balanced")
        self.assertEqual(ranked[0]["agent"], "codex")
        self.assertTrue(ranked[0]["recommended"])

    def test_cost_first_prefers_cheap_route(self) -> None:
        """§34:Cost First → 推荐 kimi(单次更便宜,接受返工)。"""
        candidates = [build_route_candidate(self.store, profile=PROFILE, route=r, policy="cost_first")
                      for r in self._routes()]
        ranked = rank_candidates(candidates, policy="cost_first")
        self.assertEqual(ranked[0]["agent"], "kimi")
