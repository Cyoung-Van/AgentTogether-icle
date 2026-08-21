"""v0.4 P12 (Batch 4) tests: strategy rules, Task Ledger, Route Candidates, Progress."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.planning import (  # noqa: E402
    build_task_ledger,
    plan_candidates,
    progress_ledger_from_run,
    save_candidates,
    save_ledger,
    suggest_strategy,
)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def test_strategy_rules_by_difficulty(self) -> None:
        self.assertEqual(suggest_strategy("D1"), ["DIRECT"])
        self.assertEqual(suggest_strategy("D2"), ["DIRECT"])
        self.assertIn("PLAN_FIRST", suggest_strategy("D3"))
        self.assertIn("DECOMPOSE", suggest_strategy("D4"))
        self.assertEqual(suggest_strategy("D5"), ["PLAN_FIRST"])  # 强制先计划

    def test_task_ledger_fields(self) -> None:
        ledger = build_task_ledger(
            title="Refactor router",
            description="Refactor the confidence algorithm in router.py and add tests",
            risk="R1",
        )
        self.assertEqual(ledger["goal"], "Refactor the confidence algorithm in router.py and add tests")
        self.assertTrue(any("existing code" in fact for fact in ledger["known_facts"]))
        self.assertTrue(any("verification" in fact for fact in ledger["known_facts"]))
        self.assertEqual(ledger["completion_contract"], "user accepts the result")

    def test_plan_candidates_low_difficulty_two_options(self) -> None:
        result = plan_candidates(
            self.store, title="Fix typo", description="fix a typo", difficulty="D2", risk="R0",
            agents=["deepseek/deepseek-v4-flash"],
        )
        ids = [c["id"] for c in result["candidates"]]
        self.assertEqual(ids, ["A", "B"])  # D2 → no Assurance option
        self.assertIsNotNone(result["cost_quote"]["expected"])

    def test_plan_candidates_high_difficulty_three_options(self) -> None:
        result = plan_candidates(
            self.store, title="Big refactor", description="refactor multiple modules",
            difficulty="D4", risk="R2",
            agents=["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro", "kimi"],
        )
        ids = [c["id"] for c in result["candidates"]]
        self.assertEqual(ids, ["A", "B", "C"])
        option_c = next(c for c in result["candidates"] if c["id"] == "C")
        self.assertEqual(option_c["strategy"], "DECOMPOSE")
        self.assertEqual(option_c["agents"], ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro", "kimi"])

    def test_progress_ledger_replan_reason(self) -> None:
        run = {
            "run_id": "run-1",
            "steps": [
                {"step_id": "S1", "status": "completed"},
                {"step_id": "S2", "status": "failed"},
                {"step_id": "S3", "status": "pending"},
            ],
        }
        progress = progress_ledger_from_run(run)
        self.assertEqual(progress["completed_steps"], ["S1"])
        self.assertEqual(progress["failed_steps"], ["S2"])
        self.assertIn("S2 failed", progress["replan_reason"])
        self.assertEqual(progress["remaining_plan"], ["S3"])

    def test_progress_ledger_clean_run_no_replan(self) -> None:
        run = {"run_id": "run-2", "steps": [{"step_id": "S1", "status": "completed"}]}
        progress = progress_ledger_from_run(run)
        self.assertIsNone(progress["replan_reason"])

    def test_save_ledger_and_candidates(self) -> None:
        ledger = build_task_ledger(title="t", description="d")
        save_ledger(self.store, ledger)
        self.assertTrue((self.store / "ledgers" / f"{ledger['ledger_id']}.json").is_file())
        candidates = plan_candidates(self.store, title="t", description="d", difficulty="D3",
                                     agents=["kimi"])
        save_candidates(self.store, candidates)
        self.assertTrue(
            list((self.store / "plan_candidates").glob("pc-*.json"))
        )


if __name__ == "__main__":
    unittest.main()
