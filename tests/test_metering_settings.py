"""Batch: unified intelligence metering, cost budget, and system settings."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.api.app import create_app  # noqa: E402
from icle.cost import record_actual  # noqa: E402
from icle.metering import MeteredProvider  # noqa: E402
from icle.settings import get_system_settings  # noqa: E402


class FakeUsageProvider:
    name = "openai"
    model = "deepseek-v4-flash"
    revision = "fake"

    def complete_with_usage(self, prompt: str):
        return "proposal", {"prompt_tokens": 1000, "completion_tokens": 500}


class MeteringTests(unittest.TestCase):
    def test_provider_usage_is_settled_and_attributed(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        provider = MeteredProvider(
            FakeUsageProvider(),
            store=store,
            billing_provider="deepseek",
            provider_id="p-test",
        )
        self.assertEqual(provider.complete("classify"), "proposal")
        records = [json.loads(line) for line in (store / "cost_actuals.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertGreater(records[0]["cash_cost"], 0)
        self.assertEqual(records[0]["usage_source"], "exact_provider")
        self.assertEqual(records[0]["provider_id"], "p-test")
        self.assertEqual(records[0]["scope"], "intelligence")
        self.assertIsNotNone(records[0].get("duration_ms"))

    def test_pricing_catalog_api_exposes_license_and_refresh_status(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        client = TestClient(create_app(store=store, capture_store=root / "capture"))
        status = client.get("/api/pricing-catalog")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["license"], "MIT")
        self.assertFalse(status.json()["available"])
        refreshed = {
            **status.json(), "synced": True, "changed": True, "available": True,
            "retrieved_at": "2026-08-18T00:00:00Z", "providers": 189,
            "models": 6690, "priced_models": 6000, "reason": "ok",
        }
        with patch("icle.tariff.sync_models_dev", return_value=refreshed), patch(
            "icle.tariff.sync_litellm",
            return_value={"synced": True, "changed": False, "available": False, "reason": "ok"},
        ):
            response = client.post("/api/pricing-catalog/refresh", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["license"], "MIT")
        self.assertEqual(response.json()["models"], 6690)

    def test_settings_and_cost_center_have_budget_and_dimensions(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        client = TestClient(create_app(store=store, capture_store=root / "capture"))
        response = client.post(
            "/api/settings",
            json={
                "replay_per_day": 9,
                "value_policy": "cost_first",
                "cost_budget_enabled": True,
                "monthly_cost_budget_usd": 5,
                "cost_warning_percent": 60,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["value_policy"], "cost_first")
        self.assertEqual(get_system_settings(store)["value_policy"], "cost_first")
        record_actual(
            store,
            {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "cash_cost": 0.25,
                "created_at": "2026-08-17T12:00:00+00:00",
                "project_id": "demo",
                "task_type": "CODING",
                "operation": "plan_task",
                "scope": "intelligence",
                "usage_source": "exact_provider",
            },
        )
        cost = client.get("/api/cost")
        self.assertEqual(cost.status_code, 200)
        self.assertEqual(cost.json()["by_project"]["demo"]["known_count"], 1)
        self.assertEqual(cost.json()["budget"]["budget"], 5.0)


if __name__ == "__main__":
    unittest.main()
