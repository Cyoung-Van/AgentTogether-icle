"""usage.py:ExecutionUsage 归一化(COST-03)+ runner 结算接线(COST-04)。"""

import json
import tempfile
import unittest
from pathlib import Path

from icle.usage import (
    anthropic_usage,
    estimate_usage,
    google_usage,
    normalize_usage,
    openai_compatible_usage,
)


class UsageAdapterTests(unittest.TestCase):
    def test_openai_compatible_with_cached_details(self) -> None:
        raw = {"prompt_tokens": 1200, "completion_tokens": 800,
               "prompt_tokens_details": {"cached_tokens": 400}}
        usage = openai_compatible_usage(raw)
        self.assertEqual(usage["input_tokens"], 1200)
        self.assertEqual(usage["output_tokens"], 800)
        self.assertEqual(usage["cache_read_tokens"], 400)
        self.assertEqual(usage["usage_source"], "exact_provider")

    def test_anthropic_cache_creation_and_read(self) -> None:
        raw = {"input_tokens": 500, "output_tokens": 300,
               "cache_creation_input_tokens": 200, "cache_read_input_tokens": 100}
        usage = anthropic_usage(raw)
        self.assertEqual(usage["cache_write_tokens"], 200)
        self.assertEqual(usage["cache_read_tokens"], 100)

    def test_google_usage_metadata(self) -> None:
        raw = {"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 700,
                                 "cachedContentTokenCount": 300, "thoughtsTokenCount": 50,
                                 "toolsTokenCount": 20}}
        usage = google_usage(raw)
        self.assertEqual(usage["input_tokens"], 900)
        self.assertEqual(usage["output_tokens"], 750)  # candidates + thoughts
        self.assertEqual(usage["cache_read_tokens"], 300)
        self.assertEqual(usage["reasoning_tokens"], 50)
        self.assertIn("other", usage["tool_calls"])

    def test_normalize_auto_detect_openai(self) -> None:
        usage = normalize_usage({"prompt_tokens": 10, "completion_tokens": 5})
        self.assertEqual(usage["usage_source"], "exact_provider")
        self.assertEqual(usage["input_tokens"], 10)

    def test_normalize_unknown_shape_keeps_unknown(self) -> None:
        usage = normalize_usage({"weird": "shape"})
        self.assertEqual(usage["usage_source"], "unknown")

    def test_estimate_usage_marks_estimated(self) -> None:
        usage = estimate_usage("hello world " * 10)
        self.assertEqual(usage["usage_source"], "estimated")
        self.assertGreater(usage["input_tokens"], 0)


class RunnerCostSettlementTests(unittest.TestCase):
    """runner → _settle_step_cost → tariff → CostActual 落盘(COST-04 验收链)。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()
        for directory in ("episodes", "marks", "steps"):
            (self.store / directory).mkdir(exist_ok=True)
        # 与真实 experience-store 相同的 provider 配置(display_name=DeepSeek)
        (self.store / "providers.json").write_text(json.dumps({
            "p-1": {"schema_version": "icle-provider-config/v0.1", "provider_id": "p-1",
                    "display_name": "DeepSeek", "type": "openai-compatible",
                    "base_url": "https://api.deepseek.com/v1", "secret_ref": "store",
                    "models": [{"id": "deepseek-v4-flash"}], "roles": ["general"],
                    "status": "connected"}}))

    def _run(self, executor) -> dict:
        from icle.task import approve_plan, create_task, run_task, save_plan

        task = create_task(self.store, title="t", description="d", project_id="p", project_path="/tmp")
        save_plan(self.store, task["task_id"], strategy="DECOMPOSE", steps=[
            {"title": "s", "description": "d", "recommended_agent": "p-1/deepseek-v4-flash",
             "type": "implementation", "context_policy": "ARTIFACT_ONLY",
             "risk": "R0", "verification": "v"}])
        approve_plan(self.store, task["task_id"])
        return run_task(self.store, task["task_id"], executor_factory=lambda agent: executor)

    def test_openai_style_usage_settles_cost(self) -> None:
        run = self._run(lambda workspace, prompt, timeout: (
            "completed", "ok", "", 0, {"prompt_tokens": 1200, "completion_tokens": 800}))
        record = run["steps"][0]
        self.assertEqual(record["cost"]["usage_source"], "exact_provider")
        self.assertIsNotNone(record["cost"]["cash_cost"])
        manual = 1200 * 0.07 / 1e6 + 800 * 0.28 / 1e6
        self.assertAlmostEqual(record["cost"]["cash_cost"], manual, places=6)
        # 落盘 cost_actuals.jsonl
        actuals = (self.store / "cost_actuals.jsonl")
        self.assertTrue(actuals.exists())
        self.assertIn("pricing_snapshot", actuals.read_text(encoding="utf-8"))

    def test_cli_usage_unknown_settles_null(self) -> None:
        run = self._run(lambda workspace, prompt, timeout: ("completed", "ok", "", 0))
        record = run["steps"][0]
        self.assertEqual(record["cost"]["usage_source"], "unknown")
        self.assertIsNone(record["cost"]["cash_cost"])
        self.assertIsNotNone(record["cost"]["duration_ms"])

    def test_agent_reported_tokens_settle(self) -> None:
        form = (
            "ICLE_TASK_REPORT\n"
            '{"requirements_total":1,"requirements_met":1,"verification_ran":true,'
            '"verification_passed":true,"tests_total":0,"tests_passed":0,'
            '"input_tokens":1200,"output_tokens":800,"duration_s":2.5,"blocked":false,'
            '"summary":"done"}'
        )
        run = self._run(lambda workspace, prompt, timeout: ("completed", form, "", 0))
        record = run["steps"][0]
        self.assertEqual(record["cost"]["usage_source"], "agent_reported")
        self.assertIsNotNone(record["cost"]["cash_cost"])
        self.assertIsNotNone(record["cost"]["duration_ms"])


if __name__ == "__main__":
    unittest.main()
