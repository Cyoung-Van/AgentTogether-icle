"""experience.py:ExperienceCostObservation + Experience Model v0.1(加权统计+shrinkage)。"""

import json
import tempfile
import unittest
from pathlib import Path

from icle.experience import (
    EXPERIENCE_MODEL_VERSION,
    build_observation,
    outcome_prediction,
    record_observation,
    usage_prediction,
)

PROFILE = {"primary_type": "CODING", "subtype": "debugging", "difficulty": "D3",
           "risk": "R1", "project_id": "icle"}
ROUTE = {"agent": "codex", "model": "gpt-x", "provider": "openai",
         "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"}


def _step(agent: str, input_tokens: int, duration_ms: int, cash: float | None) -> dict:
    return {
        "step_id": "S1", "agent": agent, "recommended_agent": agent,
        "context_policy": "PROJECT_STATE", "status": "completed",
        "duration_ms": duration_ms, "usage_source": "exact_provider",
        "usage": {"input_tokens": input_tokens, "output_tokens": 100,
                  "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
                  "tool_calls": {}, "usage_source": "exact_provider"},
        "cost": {"cash_cost": cash, "pricing_snapshot": "deepseek/deepseek-v4-flash@2026-08-16"},
    }


def _make_task(project: str = "icle") -> dict:
    return {"task_id": "t-1", "title": "t", "description": "d", "project_id": project,
            "profile": {**PROFILE, "project_id": project}}


class ObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def test_build_observation_bundles_usage_cost_outcome(self) -> None:
        run = {"run_id": "run-1", "steps": [
            _step("codex/gpt-x", 120000, 90000, 0.19),
            _step("codex/gpt-x", 62000, 71000, 0.05),
        ]}
        obs = build_observation(task=_make_task(), run=run,
                                outcome={"mark": "accept", "user_corrections": 0, "rating": 4.7})
        self.assertEqual(obs["route"]["agent"], "codex")
        self.assertEqual(obs["task_profile"]["difficulty"], "D3")
        self.assertEqual(obs["usage"]["input_tokens"], 182000)
        self.assertEqual(obs["duration_ms"], 161000)
        self.assertEqual(obs["cost"]["cash_cost"], 0.24)
        self.assertEqual(obs["outcome"]["accept_without_rework"], 1.0)
        self.assertEqual(obs["outcome"]["rating"], 4.7)
        self.assertEqual(obs["model_version"]["experience_model_version"], EXPERIENCE_MODEL_VERSION)

    def test_edit_with_many_corrections_maps_lower(self) -> None:
        run = {"run_id": "r", "steps": [_step("a/b", 10, 5, None)]}
        obs = build_observation(task=_make_task(), run=run,
                                outcome={"mark": "edit", "user_corrections": 3, "rating": None})
        self.assertEqual(obs["outcome"]["accept_without_rework"], 0.4)

    def test_model_identity_is_carried_into_exact_observation(self) -> None:
        run = {"run_id": "r", "steps": [_step("kimi", 10, 5, None)]}
        obs = build_observation(
            task=_make_task(),
            run=run,
            outcome={"mark": "accept"},
            model_identity={"model": "kimi-for-coding", "provider": "kimi-code", "source": "local_config"},
        )
        self.assertEqual(obs["route"]["agent"], "kimi")
        self.assertEqual(obs["route"]["model"], "kimi-for-coding")
        self.assertEqual(obs["route"]["provider"], "kimi-code")
        self.assertEqual(obs["route"]["model_source"], "local_config")

    def test_record_and_list(self) -> None:
        run = {"run_id": "r", "steps": [_step("a/b", 10, 5, None)]}
        obs = build_observation(task=_make_task(), run=run, outcome={"mark": "accept"})
        record_observation(self.store, obs)
        listed = __import__("icle.experience", fromlist=["list_observations"]).list_observations(self.store)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["observation_id"], obs["observation_id"])


class ExperienceModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def _seed(self, count: int, *, input_base: int = 100000, agent: str = "codex", project: str = "icle") -> None:
        for index in range(count):
            run = {"run_id": f"r{index}", "steps": [
                _step(f"{agent}/gpt-x", input_base + index * 1000, 100000 + index * 1000, None)]}
            obs = build_observation(task=_make_task(project=project), run=run,
                                    outcome={"mark": "accept" if index % 3 else "redo"})
            record_observation(self.store, obs)

    def test_usage_prediction_distribution_and_shrinkage(self) -> None:
        self._seed(12)
        pred = usage_prediction(self.store, profile=PROFILE, route=ROUTE)
        self.assertEqual(pred["agent"], "codex")
        self.assertGreaterEqual(pred["expected_usage"]["input_tokens"]["p50"], 100000)
        self.assertLessEqual(pred["expected_usage"]["input_tokens"]["p25"], pred["expected_usage"]["input_tokens"]["p75"])
        self.assertGreaterEqual(pred["sample_count"], 10)
        self.assertGreater(pred["effective_sample_size"], 0)
        self.assertIn(pred["confidence"], ("high", "medium", "low", "very_low"))
        # 输出不含美元字段(学用量不学钱)
        self.assertNotIn("cash_cost", pred)

    def test_small_sample_shrinks_outcome(self) -> None:
        # n=1 全 accept → 不显示 100%,向 prior 收缩
        self._seed(1)
        outcome = outcome_prediction(self.store, profile=PROFILE, route=ROUTE)
        self.assertIsNotNone(outcome["accept_without_rework"])
        self.assertLess(outcome["accept_without_rework"], 1.0)
        self.assertEqual(outcome["confidence"], "very_low")

    def test_no_observations(self) -> None:
        pred = usage_prediction(self.store, profile=PROFILE, route=ROUTE)
        self.assertEqual(pred["sample_count"], 0)
        self.assertEqual(pred["confidence"], "very_low")
        outcome = outcome_prediction(self.store, profile=PROFILE, route=ROUTE)
        self.assertIsNone(outcome["accept_without_rework"])

    def test_route_weighting_different_model_scores_lower(self) -> None:
        from icle.experience import _similarity

        from datetime import datetime, timezone

        base = {"task_profile": {**PROFILE, "project_id": "icle"},
                "route": {"agent": "codex", "model": "gpt-x", "provider": "openai",
                          "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE"},
                # Keep this recency assertion deterministic as calendar time advances.
                "created_at": datetime.now(timezone.utc).isoformat()}
        same = _similarity(base, profile=PROFILE, route=ROUTE)
        other_model = _similarity(base, profile=PROFILE, route={**ROUTE, "model": "other"})
        other_agent = _similarity(base, profile=PROFILE, route={**ROUTE, "agent": "kimi"})
        self.assertAlmostEqual(same, 1.0)
        self.assertAlmostEqual(other_model, 0.4 * 1.0)  # 同 agent 他 model = 0.4
        self.assertLess(other_agent, other_model)  # 他 agent 更低
        # 非均匀权重(不同 project → 0.65)→ effective_n < sample_count
        self._seed(1)
        self._seed(1, project="other-project-1")
        self._seed(1, project="other-project-2")
        pred = usage_prediction(self.store, profile=PROFILE, route=ROUTE)
        self.assertLess(pred["effective_sample_size"], pred["sample_count"])


if __name__ == "__main__":
    unittest.main()


class CostQuoteTests(unittest.TestCase):
    """COST-07/08:经验预测 usage × 当前 tariff → 五维预测。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def _seed(self, count: int) -> None:
        from icle.experience import build_observation, record_observation

        for index in range(count):
            run = {"run_id": f"r{index}", "steps": [{
                "step_id": "S1", "agent": "codex/gpt-4.1", "recommended_agent": "codex/gpt-4.1",
                "context_policy": "PROJECT_STATE", "status": "completed",
                "duration_ms": 120000 + index, "usage_source": "exact_provider",
                "usage": {"input_tokens": 100000 + index * 500, "output_tokens": 15000,
                          "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0,
                          "tool_calls": {}, "usage_source": "exact_provider"},
                "cost": {"cash_cost": 0.1}}]}
            obs = build_observation(task={"task_id": f"t{index}", "title": "t", "description": "d",
                                          "project_id": "icle",
                                          "profile": {**PROFILE, "project_id": "icle"}},
                                    run=run, outcome={"mark": "accept"})
            record_observation(self.store, obs)

    def test_cost_quote_usage_times_tariff(self) -> None:
        from icle.cost import cost_quote

        self._seed(6)
        quote = cost_quote(
            self.store, profile=PROFILE, route=ROUTE,
            provider="openai", model="gpt-4.1")
        self.assertIsNotNone(quote["expected_cost"]["expected"])
        self.assertGreaterEqual(quote["expected_cost"]["low"], 0)
        self.assertLessEqual(quote["expected_cost"]["low"], quote["expected_cost"]["expected"])
        self.assertLessEqual(quote["expected_cost"]["expected"], quote["expected_cost"]["high"])
        self.assertGreater(quote["evidence"]["sample_count"], 0)
        # 预测是美元(usage × tariff),但来源是用量分布
        self.assertIn("expected_cost", quote)

    def test_route_prediction_five_dimensions(self) -> None:
        from icle.cost import route_prediction

        self._seed(8)
        prediction = route_prediction(
            self.store, profile=PROFILE, route=ROUTE, provider="openai", model="gpt-4.1")
        for key in ("agent", "quality", "cost", "time_s", "intervention", "confidence", "evidence"):
            self.assertIn(key, prediction)
        self.assertIsNotNone(prediction["quality"])
        self.assertIsNotNone(prediction["intervention"])
        self.assertEqual(prediction["agent"], "codex")

    def test_route_prediction_no_evidence(self) -> None:
        from icle.cost import route_prediction

        prediction = route_prediction(
            self.store, profile=PROFILE, route=ROUTE, provider="openai", model="gpt-4.1")
        self.assertEqual(prediction["confidence"], "very_low")
        self.assertEqual(prediction["evidence"]["sample_count"], 0)
        self.assertIsNone(prediction["cost"]["expected"])
