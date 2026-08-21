"""tariff.py:BaseTariffModel — PricingSnapshot/TariffComponent/TariffEngine + models.dev sync。"""

import json
import tempfile
import unittest
from pathlib import Path

from icle.tariff import (
    TariffError,
    builtin_snapshots,
    catalog_lookup_keys,
    compute_actual_from_tariff,
    compute_from_snapshot,
    counterfactual_current_cost,
    list_pricing_snapshots,
    pricing_catalog_status,
    resolve_snapshot,
    save_snapshot,
    sync_litellm,
    sync_models_dev,
    validate_all_snapshots,
    validate_snapshot,
)

USAGE = {
    "input_tokens": 1000, "output_tokens": 500,
    "cache_read_tokens": 2000, "cache_write_tokens": 0,
    "reasoning_tokens": 0, "tool_calls": {},
}


class BaseTariffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def test_builtin_snapshots_validate(self) -> None:
        keys = validate_all_snapshots()
        self.assertGreaterEqual(len(keys), 15)
        for provider in ("openai", "anthropic", "google", "deepseek"):
            self.assertTrue(any(k.startswith(provider + "/") for k in keys))

    def test_snapshot_components_have_cache_and_output(self) -> None:
        snapshot = builtin_snapshots()["deepseek/deepseek-v4-flash"]
        categories = {c["category"] for c in snapshot["pricing"]}
        self.assertIn("input_uncached", categories)
        self.assertIn("cache_read", categories)
        self.assertIn("cache_write", categories)
        self.assertIn("output", categories)
        self.assertEqual(snapshot["source"]["kind"], "official_doc")

    def test_reject_invalid_snapshot(self) -> None:
        bad = dict(builtin_snapshots()["deepseek/deepseek-v4-flash"])
        bad["pricing"] = [{"kind": "token", "category": "nonsense", "unit": "1m_tokens", "price": 1.0}]
        with self.assertRaises(TariffError):
            validate_snapshot(bad)

    def test_resolve_case_insensitive_display_name(self) -> None:
        snapshot, kind = resolve_snapshot(self.store, "DeepSeek", "deepseek-v4-flash")
        self.assertEqual(snapshot["provider"], "deepseek")
        self.assertEqual(kind, "official_doc")

    def test_resolve_unknown_raises(self) -> None:
        with self.assertRaises(TariffError):
            resolve_snapshot(self.store, "mystery", "no-such-model")

    def test_user_override_wins(self) -> None:
        override = dict(builtin_snapshots()["deepseek/deepseek-v4-flash"])
        override["source"] = {"kind": "user_override", "url": "", "retrieved_at": "", "content_hash": "x"}
        override["pricing"] = [c for c in override["pricing"] if c["category"] == "input_uncached"]
        save_snapshot(self.store, override)
        snapshot, kind = resolve_snapshot(self.store, "deepseek", "deepseek-v4-flash")
        self.assertEqual(kind, "user_override")


class TariffEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()
        self.snapshot, _ = resolve_snapshot(self.store, "deepseek", "deepseek-v4-flash")

    def test_compute_matches_manual(self) -> None:
        calc = compute_from_snapshot(self.snapshot, USAGE)
        manual = 1000 * 0.07 / 1e6 + 2000 * 0.007 / 1e6 + 500 * 0.28 / 1e6
        self.assertAlmostEqual(calc["total"], manual, places=9)

    def test_multiplier_applies_above_threshold(self) -> None:
        with_multiplier = dict(self.snapshot)
        with_multiplier["pricing"] = list(self.snapshot["pricing"]) + [
            {"kind": "multiplier", "condition": "input_tokens > 200000", "factor": 2.0}]
        small = compute_from_snapshot(with_multiplier, USAGE)
        self.assertEqual(small["multipliers"], [])
        big = compute_from_snapshot(with_multiplier, {**USAGE, "input_tokens": 250000})
        self.assertEqual(len(big["multipliers"]), 1)
        self.assertAlmostEqual(big["total"], sum(big["by_category"].values()) * 2, places=9)

    def test_tool_call_component(self) -> None:
        snapshot = dict(self.snapshot)
        snapshot["pricing"] = list(self.snapshot["pricing"]) + [
            {"kind": "tool_call", "category": "web_search", "unit": "1000_requests", "price": 10.0}]
        calc = compute_from_snapshot(snapshot, {**USAGE, "tool_calls": {"web_search": 3}})
        self.assertIn("tool_web_search", calc["by_category"])
        self.assertAlmostEqual(calc["by_category"]["tool_web_search"], 0.03, places=9)

    def test_reasoning_tokens_are_not_double_charged_when_included_in_output(self) -> None:
        usage = {**USAGE, "output_tokens": 500, "reasoning_tokens": 300}
        calculation = compute_from_snapshot(self.snapshot, usage)
        self.assertNotIn("reasoning", calculation["by_category"])
        separate = compute_from_snapshot(
            self.snapshot,
            {**usage, "reasoning_tokens_in_output": False},
        )
        self.assertGreater(separate["total"], calculation["total"])


class CostActualTariffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()

    def test_actual_includes_snapshot_ref_and_calculation(self) -> None:
        actual = compute_actual_from_tariff(
            self.store, provider="DeepSeek", model_id="deepseek-v4-flash",
            usage=USAGE, usage_source="exact_provider")
        self.assertIsNotNone(actual["cash_cost"])
        self.assertTrue(actual["pricing_snapshot"].startswith("deepseek/deepseek-v4-flash@"))
        self.assertEqual(actual["usage_source"], "exact_provider")
        self.assertIn("input_uncached", actual["calculation"]["by_category"])

    def test_unknown_model_null_not_zero(self) -> None:
        actual = compute_actual_from_tariff(
            self.store, provider="mystery", model_id="nope", usage=USAGE)
        self.assertIsNone(actual["cash_cost"])
        self.assertEqual(actual["reason"], "no_tariff")

    def test_counterfactual_after_price_change(self) -> None:
        actual = compute_actual_from_tariff(
            self.store, provider="DeepSeek", model_id="deepseek-v4-flash",
            usage=USAGE, usage_source="exact_provider")
        # 模拟降价:用户覆盖价格减半
        halved = dict(builtin_snapshots()["deepseek/deepseek-v4-flash"])
        halved["source"] = {"kind": "user_override", "url": "", "retrieved_at": "", "content_hash": "x"}
        halved["pricing"] = [dict(c, price=c["price"] / 2) for c in halved["pricing"]]
        save_snapshot(self.store, halved)
        cc = counterfactual_current_cost(self.store, actual)
        self.assertIsNotNone(cc["historical_cost"])
        self.assertIsNotNone(cc["counterfactual_current_cost"])
        self.assertLess(cc["counterfactual_current_cost"], cc["historical_cost"])

    def test_usage_source_validated(self) -> None:
        with self.assertRaises(TariffError):
            compute_actual_from_tariff(self.store, provider="p", model_id="m",
                                       usage=USAGE, usage_source="bogus")


class ModelsDevSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()

    @staticmethod
    def _catalog(*, flash_input: float = 0.14) -> dict:
        providers = {}
        for provider_index in range(10):
            provider_id = "deepseek" if provider_index == 0 else f"provider-{provider_index}"
            models = {}
            for model_index in range(10):
                model_id = "deepseek-v4-flash" if provider_index == 0 and model_index == 0 else f"model-{model_index}"
                models[model_id] = {
                    "id": model_id,
                    "last_updated": "2026-08-17",
                    "cost": {
                        "input": flash_input if model_id == "deepseek-v4-flash" else 1.0,
                        "output": 0.28 if model_id == "deepseek-v4-flash" else 2.0,
                        "cache_read": 0.0028 if model_id == "deepseek-v4-flash" else 0.1,
                        "reasoning": 0.28 if model_id == "deepseek-v4-flash" else 2.0,
                    },
                }
            providers[provider_id] = {
                "id": provider_id,
                "doc": "https://example.test/pricing",
                "models": models,
            }
        return providers

    def test_sync_degrades_without_network(self) -> None:
        # 无网络/超时 → 优雅降级,snapshots=0,不抛异常
        result = sync_models_dev(self.store, timeout=0.001)
        self.assertIn(result["synced"], (True, False))
        if not result["synced"]:
            self.assertIn("network", result["reason"])

    def test_community_snapshot_persisted(self) -> None:
        # 直接保存一个 community_catalog snapshot,验证持久化与解析优先级
        snapshot = dict(builtin_snapshots()["deepseek/deepseek-v4-flash"])
        snapshot["source"] = {"kind": "community_catalog", "url": "https://models.dev/api.json",
                              "retrieved_at": "2026-08-16", "content_hash": "abc"}
        save_snapshot(self.store, snapshot)
        persisted = list_pricing_snapshots(self.store)
        self.assertIn("deepseek/deepseek-v4-flash", persisted)
        resolved, kind = resolve_snapshot(self.store, "deepseek", "deepseek-v4-flash")
        self.assertEqual(kind, "community_catalog")

    def test_live_catalog_is_licensed_versioned_and_exact_provider_only(self) -> None:
        raw = json.dumps(self._catalog())
        result = sync_models_dev(
            self.store,
            fetcher=lambda headers: (raw, {"ETag": '"catalog-v1"'}),
        )
        self.assertTrue(result["synced"])
        self.assertTrue(result["changed"])
        status = pricing_catalog_status(self.store)
        self.assertEqual(status["license"], "MIT")
        self.assertEqual(status["etag"], '"catalog-v1"')
        self.assertEqual(status["models"], 100)
        snapshot, source = resolve_snapshot(self.store, "deepseek", "deepseek-v4-flash")
        self.assertEqual(source, "community_catalog")
        rates = {component["category"]: component["price"] for component in snapshot["pricing"]}
        self.assertEqual(rates["input_uncached"], 0.14)
        self.assertEqual(rates["output"], 0.28)
        self.assertEqual(rates["cache_read"], 0.0028)
        self.assertEqual(snapshot["source"]["license"], "MIT")
        with self.assertRaises(TariffError):
            resolve_snapshot(self.store, "relay-20170428", "deepseek-v4-flash")

    def test_bad_refresh_retains_previous_catalog(self) -> None:
        first = json.dumps(self._catalog())
        sync_models_dev(self.store, fetcher=lambda headers: (first, {"etag": "v1"}))
        before = pricing_catalog_status(self.store)
        failed = sync_models_dev(
            self.store,
            fetcher=lambda headers: (json.dumps({"deepseek": {"models": {}}}), {"etag": "bad"}),
        )
        self.assertFalse(failed["synced"])
        after = pricing_catalog_status(self.store)
        self.assertEqual(after["content_hash"], before["content_hash"])
        snapshot, _ = resolve_snapshot(self.store, "deepseek", "deepseek-v4-flash")
        self.assertEqual(
            next(component["price"] for component in snapshot["pricing"] if component["category"] == "input_uncached"),
            0.14,
        )

    def test_product_alias_finds_catalog_price(self) -> None:
        catalog = self._catalog()
        catalog["moonshotai"] = {
            "id": "moonshotai",
            "doc": "https://example.test/kimi",
            "models": {
                "kimi-k2.7-code": {
                    "id": "kimi-k2.7-code",
                    "last_updated": "2026-08-20",
                    "cost": {"input": 1.15, "output": 8.0},
                }
            },
        }
        sync_models_dev(self.store, fetcher=lambda headers: (json.dumps(catalog), {}))
        snapshot, kind = resolve_snapshot(self.store, "kimi-code", "kimi-for-coding")
        self.assertEqual(kind, "community_catalog")
        rates = {component["category"]: component["price"] for component in snapshot["pricing"]}
        self.assertEqual(rates["input_uncached"], 1.15)
        self.assertIn(("moonshotai", "kimi-k2.7-code"), catalog_lookup_keys("kimi-code", "kimi-for-coding"))

    def test_litellm_fallback_prices_unknown_builtin(self) -> None:
        catalog = {"sample_spec": {"input_cost_per_token": 0}}
        for index in range(120):
            catalog[f"vendor/model-{index}"] = {
                "input_cost_per_token": 1e-6,
                "output_cost_per_token": 2e-6,
                "litellm_provider": "vendor",
            }
        catalog["moonshot/kimi-k2.7-code"] = {
            "input_cost_per_token": 1.15e-6,
            "output_cost_per_token": 8e-6,
            "litellm_provider": "moonshot",
        }
        result = sync_litellm(self.store, fetcher=lambda headers: (json.dumps(catalog), {}))
        self.assertTrue(result["synced"])
        snapshot, kind = resolve_snapshot(self.store, "kimi-code", "kimi-for-coding")
        self.assertEqual(kind, "community_catalog")
        rates = {component["category"]: component["price"] for component in snapshot["pricing"]}
        self.assertAlmostEqual(rates["input_uncached"], 1.15, places=6)


if __name__ == "__main__":
    unittest.main()
