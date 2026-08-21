"""Local calendar boundaries: UTC evidence, computer-local periods."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.active import consume_budget, get_budget, remaining_budget  # noqa: E402
from icle.api.control import _calendar_cost_totals  # noqa: E402
from icle.cost import current_month_cost, record_actual  # noqa: E402
from icle.localtime import local_date, local_day_key, local_month_key  # noqa: E402


class LocalCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()
        self.shanghai = ZoneInfo("Asia/Shanghai")

    def test_utc_timestamp_crosses_local_day_and_month(self) -> None:
        timestamp = "2026-07-31T16:30:00+00:00"
        self.assertEqual(
            local_day_key(timestamp, local_timezone=self.shanghai), "2026-08-01"
        )
        self.assertEqual(
            local_month_key(timestamp, local_timezone=self.shanghai), "2026-08"
        )
        # Legacy timestamps without an offset are UTC by policy, not local.
        self.assertEqual(
            local_day_key("2026-07-31T16:30:00", local_timezone=self.shanghai),
            "2026-08-01",
        )

    def test_overview_today_uses_local_calendar_date(self) -> None:
        actuals = [
            {"cash_cost": 2.5, "created_at": "2026-08-17T16:30:00+00:00"},
            {"cash_cost": None, "created_at": "2026-08-17T17:00:00+00:00"},
        ]
        today, week = _calendar_cost_totals(
            actuals, today=date(2026, 8, 18), local_timezone=self.shanghai
        )
        self.assertEqual(today, 2.5)
        self.assertEqual(week, 2.5)

    def test_monthly_budget_uses_same_local_month_boundary(self) -> None:
        record_actual(
            self.store,
            {
                "schema_version": "icle-cost-actual/v0.1",
                "cash_cost": 3.0,
                "created_at": "2026-07-31T16:30:00+00:00",
            },
        )
        self.assertEqual(
            current_month_cost(
                self.store,
                now="2026-08-15T00:00:00+00:00",
                local_timezone=self.shanghai,
            ),
            3.0,
        )

    def test_daily_execution_budget_uses_shared_local_day_key(self) -> None:
        with patch("icle.active.local_day_key", return_value="2026-08-18"):
            consume_budget(self.store, reason="test")
            self.assertEqual(get_budget(self.store)["used"], {"2026-08-18": 1})
            self.assertEqual(remaining_budget(self.store), 4)


if __name__ == "__main__":
    unittest.main()
