"""v0.4 P11 (Batch 2) tests: ModelCatalog, BillingMode, CostActual, CostQuote."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.cost import (  # noqa: E402
    billing_mode,
    compute_actual,
    list_actuals,
    plan_cost_quote,
    quote_cold_start,
    quote_from_history,
    record_actual,
    set_price_override,
)


class CostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def test_known_model_price(self) -> None:
        price = __import__("icle.cost", fromlist=["model_price"]).model_price(
            self.store, "deepseek-v4-pro", provider="deepseek"
        )
        self.assertEqual(price["source"], "builtin")
        self.assertGreater(price["input"], 0)

    def test_price_normalized_case_insensitive(self) -> None:
        """修复回归:结算传 provider.display_name("DeepSeek")也能命中价格表。"""
        model_price = __import__("icle.cost", fromlist=["model_price"]).model_price
        upper = model_price(self.store, "deepseek-v4-flash", provider="DeepSeek")
        self.assertIsNotNone(upper)
        self.assertEqual(upper["source"], "builtin")
        self.assertEqual(upper["input"], 0.07)
        # 模型名大小写不同也能命中
        mixed = model_price(self.store, "DeepSeek-V4-Pro", provider="deepseek")
        self.assertIsNotNone(mixed)

    def test_compute_actual_with_display_name(self) -> None:
        """真实链路:display_name + 真实 usage → 结算出金额(修复前恒 null)。"""
        actual = compute_actual(
            self.store, provider="DeepSeek", model_id="deepseek-v4-flash",
            input_tokens=1200, output_tokens=800,
        )
        self.assertIsNotNone(actual["cost"])
        self.assertGreater(actual["cost"], 0)
        self.assertEqual(actual["price_source"], "builtin")

    def test_unknown_model_cost_is_null_not_zero(self) -> None:
        actual = compute_actual(
            self.store, provider="mystery", model_id="no-such-model",
            input_tokens=1000, output_tokens=500,
        )
        self.assertIsNone(actual["cost"])
        self.assertEqual(actual["reason"], "usage_unavailable")

    def test_compute_actual_metered(self) -> None:
        actual = compute_actual(
            self.store, provider="deepseek", model_id="deepseek-v4-pro",
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
        # input 0.27 + output 1.1 per 1M → 1.37
        self.assertAlmostEqual(actual["cost"], 1.37, places=4)
        self.assertEqual(actual["billing_mode"], "API_METERED")

    def test_billing_modes(self) -> None:
        self.assertEqual(billing_mode("ollama", "llama3"), "LOCAL_COMPUTE")
        self.assertEqual(billing_mode("deepseek", "deepseek-v4-pro"), "API_METERED")
        self.assertEqual(billing_mode("", "claude-code"), "SUBSCRIPTION")

    def test_override_wins(self) -> None:
        set_price_override(self.store, "deepseek-v4-pro", provider="deepseek",
                           input_per_m=9.9, output_per_m=9.9)
        actual = compute_actual(
            self.store, provider="deepseek", model_id="deepseek-v4-pro",
            input_tokens=1_000_000, output_tokens=0,
        )
        self.assertEqual(actual["cost"], 9.9)
        self.assertEqual(actual["price_source"], "override")

    def test_quote_from_history_median_and_confidence(self) -> None:
        history = [
            {"task_type": "CODING", "difficulty": "D3", "cost": 0.10, "duration_ms": 1000,
             "input_tokens": 100, "output_tokens": 50},
            {"task_type": "CODING", "difficulty": "D3", "cost": 0.20, "duration_ms": 2000,
             "input_tokens": 200, "output_tokens": 100},
            {"task_type": "CODING", "difficulty": "D3", "cost": 0.30, "duration_ms": 3000,
             "input_tokens": 300, "output_tokens": 150},
        ]
        quote = quote_from_history(self.store, task_type="CODING", difficulty="D3", history=history)
        self.assertEqual(quote["sample_size"], 3)
        self.assertEqual(quote["confidence"], "medium")
        self.assertEqual(quote["cost"]["median"], 0.20)
        self.assertIsNotNone(quote["cost"]["p25"])

    def test_cold_start_low_confidence(self) -> None:
        quote = quote_cold_start(difficulty="D4", plan_steps=3)
        self.assertEqual(quote["confidence"], "low")
        self.assertEqual(quote["sample_size"], 0)

    def test_plan_cost_quote(self) -> None:
        steps = [
            {"provider": "deepseek", "model": "deepseek-v4-flash", "estimated_input": 1_000_000, "estimated_output": 500_000},
            {"provider": "ollama", "model": "llama3"},  # local → no cost, not unknown
        ]
        quote = plan_cost_quote(self.store, steps=steps)
        self.assertIsNotNone(quote["expected"])
        self.assertEqual(quote["unknown_steps"], [])
        self.assertGreater(quote["expected"], 0)
        self.assertEqual(quote["cash_cost_status"], "partial")
        # subscription/local steps are excluded from the known API subtotal,
        # but explicitly prevent it from being described as a complete total.
        self.assertEqual(quote["steps"][1]["billing_mode"], "LOCAL_COMPUTE")
        self.assertIsNone(quote["steps"][1]["cost"])
        self.assertEqual(quote["non_cash_steps"], ["S2"])

    def test_all_local_plan_cash_cost_is_unknown_not_zero(self) -> None:
        quote = plan_cost_quote(
            self.store,
            steps=[{"provider": "ollama", "model": "llama3"}],
        )
        self.assertIsNone(quote["expected"])
        self.assertIsNone(quote["range"])
        self.assertEqual(quote["cash_cost_status"], "unknown")
        self.assertEqual(quote["non_cash_steps"], ["S1"])

    def test_record_and_list_actuals(self) -> None:
        actual = compute_actual(
            self.store, provider="deepseek", model_id="deepseek-v4-flash",
            input_tokens=100, output_tokens=100,
        )
        record_actual(self.store, actual)
        records = list_actuals(self.store)
        self.assertEqual(len(records), 1)
        self.assertIsNotNone(records[0]["cost"])


if __name__ == "__main__":
    unittest.main()
