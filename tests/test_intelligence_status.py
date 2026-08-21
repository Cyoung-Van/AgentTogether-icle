"""Global intelligence status: operation lifecycle and API projection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from icle.api.app import _intelligence_operation, create_app
from icle.intelligence_status import IntelligenceBusyError, IntelligenceStatus


class FakeProvider:
    name = "test-provider"
    model = "test-model"


class IntelligenceStatusTests(unittest.TestCase):
    def test_tracks_concurrent_operations_and_last_result(self) -> None:
        tracker = IntelligenceStatus()
        first = tracker.start("analyze_session")
        second = tracker.start("split_task")
        busy = tracker.snapshot()
        self.assertEqual(busy["state"], "busy")
        self.assertEqual(busy["active_count"], 2)
        self.assertEqual({item["operation"] for item in busy["active"]}, {"analyze_session", "split_task"})
        self.assertNotIn("prompt", busy)

        tracker.finish(first, outcome="completed")
        self.assertEqual(tracker.snapshot()["active_count"], 1)
        tracker.finish(second, outcome="failed")
        idle = tracker.snapshot()
        self.assertEqual(idle["state"], "idle")
        self.assertEqual(idle["last"]["operation"], "split_task")
        self.assertEqual(idle["last"]["outcome"], "failed")

    def test_configuration_mutation_only_runs_while_idle(self) -> None:
        tracker = IntelligenceStatus()
        self.assertEqual(tracker.run_when_idle(lambda: "changed"), "changed")
        operation_id = tracker.start("plan_task")
        with self.assertRaises(IntelligenceBusyError):
            tracker.run_when_idle(lambda: "must-not-run")
        tracker.finish(operation_id, outcome="completed")
        self.assertEqual(tracker.run_when_idle(lambda: "changed-after"), "changed-after")

    def test_llm_route_mapping_is_specific(self) -> None:
        cases = {
            "/api/sessions/claude/s-1/analyze": "analyze_session",
            "/api/sessions/extract-tasks": "extract_tasks",
            "/api/tasks/t-1/split-proposal": "split_task",
            "/api/tasks/t-1/split-refine": "refine_split",
            "/api/tasks/t-1/plan-from-template": "customize_template",
            "/api/tasks/t-1/analyze": "analyze_task",
            "/api/tasks/t-1/plan/llm": "plan_task",
            "/api/tasks/t-1/replan-llm": "replan_task",
            "/api/tasks/t-1/judge-llm": "judge_result",
            "/api/agents/qwen/capture-llm": "capture_session",
        }
        for path, operation in cases.items():
            with self.subTest(path=path):
                self.assertEqual(_intelligence_operation(path, "POST"), operation)
        self.assertIsNone(_intelligence_operation("/api/tasks/t-1/plan", "POST"))
        self.assertIsNone(_intelligence_operation("/api/tasks/t-1/analyze", "GET"))

    def test_status_endpoint_includes_provider_without_sensitive_content(self) -> None:
        store = Path(tempfile.mkdtemp()) / "store"
        app = create_app(store=store)
        operation_id = app.state.intelligence_status.start("plan_task")
        with patch("icle.api.health.resolve_provider", return_value=FakeProvider()):
            body = TestClient(app).get("/api/intelligence-status").json()
        self.assertEqual(body["state"], "busy")
        self.assertEqual(body["provider"]["name"], "test-provider")
        self.assertEqual(body["provider"]["display_name"], "test-provider")
        self.assertEqual(body["provider"]["model"], "test-model")
        self.assertEqual(body["active"][0]["operation"], "plan_task")
        self.assertNotIn("prompt", str(body).lower())
        app.state.intelligence_status.finish(operation_id, outcome="completed")


if __name__ == "__main__":
    unittest.main()
