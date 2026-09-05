"""UI-20..29 API tests: Task CRUD / profile / plan / execute / evaluate.

LLM endpoints (analyze / plan/llm / judge-llm) are tested in their NO-LLM
mode (default settings = provider none → HTTP 400 with a clear message).
Execution uses an injected fake executor via app.state.executor_factory.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from icle.api.app import create_app  # noqa: E402


def fake_executor_factory(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(f"done by {agent}", encoding="utf-8")
        return ("completed", f"output of {agent}", "", 0, {"prompt_tokens": 12, "completion_tokens": 8})

    return executor


STEPS = [
    {"title": "Inspect", "description": "read", "recommended_agent": "kimi",
     "type": "analysis", "context_policy": "CLEAN", "risk": "R0"},
    {"title": "Implement", "description": "write", "recommended_agent": "kimi",
     "type": "implementation", "context_policy": "ARTIFACT_ONLY", "risk": "R1"},
]


class TaskApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.app = create_app(store=self.root / "store")
        self.app.state.executor_factory = fake_executor_factory
        self.client = TestClient(self.app)

    def _make_task(self, **kw) -> dict:
        body = {"title": "Fix router", "description": "Make confidence stable", "project_id": "icle"}
        body.update(kw)
        response = self.client.post("/api/tasks", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _make_task_full(self) -> dict:
        task = self._make_task()
        self.client.post(
            f"/api/tasks/{task['task_id']}/profile",
            json={"primary_type": "CODING", "subtype": "refactor", "difficulty": "D3",
                  "risk": "R1", "context_requirement": "MEDIUM",
                  "tool_requirement": ["filesystem"], "estimated_duration": "medium",
                  "decomposition": "recommended", "review": "recommended", "reason": "t"},
        )
        self.client.post(
            f"/api/tasks/{task['task_id']}/plan",
            json={"strategy": "DECOMPOSE", "steps": STEPS},
        )
        self.client.post(f"/api/tasks/{task['task_id']}/plan/approve")
        return task

    def test_crud(self) -> None:
        task = self._make_task()
        self.assertEqual(task["status"], "draft")
        listed = self.client.get("/api/tasks").json()["tasks"]
        self.assertEqual(len(listed), 1)
        detail = self.client.get(f"/api/tasks/{task['task_id']}").json()
        self.assertEqual(detail["title"], "Fix router")
        patched = self.client.patch(
            f"/api/tasks/{task['task_id']}", json={"title": "Renamed"}
        ).json()
        self.assertEqual(patched["title"], "Renamed")
        self.assertEqual(self.client.get("/api/tasks/nope").status_code, 404)

    def test_manual_profile_and_plan_flow(self) -> None:
        task = self._make_task()
        response = self.client.post(
            f"/api/tasks/{task['task_id']}/profile",
            json={"primary_type": "CODING", "subtype": "refactor", "difficulty": "D3",
                  "risk": "R1", "context_requirement": "HIGH",
                  "tool_requirement": ["filesystem"], "estimated_duration": "medium",
                  "decomposition": "recommended", "review": "recommended", "reason": "t"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()
        self.assertEqual(task["status"], "profiled")
        self.assertEqual(task["profile"]["source"], "manual")
        # bad enum rejected
        bad = self.client.post(
            f"/api/tasks/{task['task_id']}/profile",
            json={"primary_type": "CODING", "difficulty": "D9"},
        )
        self.assertEqual(bad.status_code, 400)

    def test_manual_plan_approve_execute_accept(self) -> None:
        task = self._make_task_full()
        detail = self.client.get(f"/api/tasks/{task['task_id']}").json()
        self.assertEqual(detail["status"], "approved")
        self.assertEqual(detail["plan"]["strategy"], "DECOMPOSE")
        self.assertEqual(len(detail["plan"]["steps"]), 2)

        run = self.client.post(f"/api/tasks/{task['task_id']}/execute")
        self.assertEqual(run.status_code, 200, run.text)
        run_body = run.json()["run"]
        self.assertEqual(run_body["status"], "completed")
        self.assertEqual(len(run_body["steps"]), 2)

        # task enters review; accept creates an episode + accept mark
        accepted = self.client.post(f"/api/tasks/{task['task_id']}/accept").json()
        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(accepted["episode_id"])
        run_record = self.client.get(f"/api/tasks/{task['task_id']}/run")
        self.assertEqual(run_record.status_code, 200)

    def test_execute_requires_approved_plan(self) -> None:
        task = self._make_task()
        response = self.client.post(f"/api/tasks/{task['task_id']}/execute")
        self.assertEqual(response.status_code, 400)
        self.assertIn("no plan", response.json()["detail"])

    def test_plan_editor_steps_update(self) -> None:
        task = self._make_task()
        self.client.post(
            f"/api/tasks/{task['task_id']}/plan",
            json={"strategy": "DECOMPOSE", "steps": STEPS},
        )
        edited = [
            {"step_id": "S1", "title": "Renamed step", "description": "x", "recommended_agent": "hermes",
             "type": "review", "context_policy": "CLEAN"},
        ]
        response = self.client.post(
            f"/api/tasks/{task['task_id']}/plan/steps",
            json={"steps": edited, "strategy": "PLAN_FIRST"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()["plan"]
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["steps"][0]["step_id"], "S1")
        self.assertEqual(plan["strategy"], "PLAN_FIRST")
        self.assertEqual(plan["status"], "revised")

    def test_llm_endpoints_fail_loudly_without_provider(self) -> None:
        """No-LLM mode: analyze/plan-llm/judge-llm return 400 with clear text."""
        task = self._make_task()
        for endpoint in ("analyze", "plan/llm"):
            response = self.client.post(f"/api/tasks/{task['task_id']}/{endpoint}")
            self.assertEqual(response.status_code, 400, endpoint)
            self.assertIn("LLM", response.json()["detail"])
        # judge-llm needs a run; with no LLM it must still fail on the provider first
        self._make_task_full()
        response = self.client.post(f"/api/tasks/{task['task_id']}/judge-llm")
        self.assertEqual(response.status_code, 400)

    def test_accept_requires_review(self) -> None:
        task = self._make_task()
        response = self.client.post(f"/api/tasks/{task['task_id']}/accept")
        self.assertEqual(response.status_code, 400)

    def test_task_cost_estimates_compare_executable_provider_models(self) -> None:
        task = self._make_task()
        self.client.post(
            f"/api/tasks/{task['task_id']}/profile",
            json={"primary_type": "CODING", "difficulty": "D4", "risk": "R2",
                  "context_requirement": "HIGH", "tool_requirement": ["filesystem"],
                  "estimated_duration": "long", "decomposition": "recommended",
                  "review": "recommended", "reason": "t"},
        )
        providers = [{
            "provider_id": "p-deepseek", "display_name": "DeepSeek",
            "type": "openai-compatible", "status": "connected", "configured": True,
            "roles": ["general"], "models": [{"id": "deepseek-v4-flash"}],
        }]
        with (
            patch("icle.discovery.scan_agents", return_value=[]),
            patch("icle.provider.list_providers", return_value=providers),
        ):
            response = self.client.get(f"/api/tasks/{task['task_id']}/cost-estimates")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["profile_source"], "saved")
        self.assertEqual(len(body["estimates"]), 1)
        estimate = body["estimates"][0]
        self.assertEqual(estimate["agent_id"], "p-deepseek/deepseek-v4-flash")
        self.assertEqual(estimate["provider"], "deepseek")
        self.assertEqual(estimate["workload"]["input_tokens"], 150000)
        self.assertEqual(estimate["workload"]["output_tokens"], 30000)
        self.assertIsNotNone(estimate["api_equivalent_cost"])
        self.assertEqual(estimate["execution_cost"], estimate["api_equivalent_cost"])
        self.assertEqual(estimate["execution_cost_status"], "estimated")

    def test_task_cost_estimates_use_rule_preview_for_draft(self) -> None:
        task = self._make_task(title="Fix code", description="Implement parser fix")
        with (
            patch("icle.discovery.scan_agents", return_value=[]),
            patch("icle.provider.list_providers", return_value=[]),
        ):
            response = self.client.get(f"/api/tasks/{task['task_id']}/cost-estimates")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["profile_source"], "rule_preview")
        self.assertEqual(response.json()["profile"]["difficulty"], "D2")
        self.assertEqual(response.json()["estimates"], [])

    def test_route_candidates_endpoint(self) -> None:
        """v0.4 §30/§44: POST /tasks/{id}/candidates returns A/B/C routes."""
        task = self._make_task()
        self.client.post(
            f"/api/tasks/{task['task_id']}/profile",
            json={"primary_type": "CODING", "difficulty": "D4", "risk": "R2",
                  "context_requirement": "HIGH", "tool_requirement": ["filesystem"],
                  "estimated_duration": "long", "decomposition": "recommended",
                  "review": "recommended", "reason": "t"},
        )
        detected = [{
            "agent_type": "kimi", "status": "linked", "execution_supported": True,
        }]
        with patch("icle.discovery.scan_agents", return_value=detected):
            response = self.client.post(f"/api/tasks/{task['task_id']}/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        ids = [c["id"] for c in body["candidates"]]
        self.assertEqual(ids, ["A", "B", "C"])  # D4/R2 → three options
        self.assertIn("cost_quote", body)
        self.assertTrue(all(step.get("title") for c in body["candidates"] for step in c["steps"]))

    def test_route_candidates_require_real_execution_target(self) -> None:
        task = self._make_task()
        with patch("icle.discovery.scan_agents", return_value=[]):
            response = self.client.post(f"/api/tasks/{task['task_id']}/candidates")
        self.assertEqual(response.status_code, 409)

    def test_status_patch_is_rejected(self) -> None:
        task = self._make_task()
        response = self.client.patch(
            f"/api/tasks/{task['task_id']}", json={"status": "running"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(f"/api/tasks/{task['task_id']}").json()["status"], "draft")

    def test_overview_active_matches_unfinished_task_workspace(self) -> None:
        draft = self._make_task(title="Draft work")
        planned = self._make_task(title="Planned work")
        profile = self.client.post(
            f"/api/tasks/{planned['task_id']}/profile",
            json={"primary_type": "CODING", "difficulty": "D2", "risk": "R1",
                  "context_requirement": "MEDIUM", "tool_requirement": ["filesystem"],
                  "estimated_duration": "short", "decomposition": "not_recommended",
                  "review": "recommended", "reason": "test"},
        )
        self.assertEqual(profile.status_code, 200, profile.text)
        plan = self.client.post(
            f"/api/tasks/{planned['task_id']}/plan",
            json={"strategy": "DIRECT", "steps": [STEPS[0]]},
        )
        self.assertEqual(plan.status_code, 200, plan.text)
        ended = self._make_task(title="Ended work")
        self.assertEqual(
            self.client.post(f"/api/tasks/{ended['task_id']}/end").status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/api/tasks/{ended['task_id']}/archive").status_code,
            200,
        )

        response = self.client.get("/api/overview")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        active_ids = {item["task_id"] for item in body["active"]}
        needs_ids = {item["task_id"] for item in body["needs_you"]}
        self.assertIn(draft["task_id"], active_ids)
        self.assertIn(planned["task_id"], active_ids)
        self.assertNotIn(ended["task_id"], active_ids)
        self.assertIn(planned["task_id"], needs_ids)
        self.assertNotIn(ended["task_id"], needs_ids)

    def test_report_endpoint_exposes_the_fixed_template(self) -> None:
        task = self._make_task_full()
        self.client.post(f"/api/tasks/{task['task_id']}/execute")
        response = self.client.get(f"/api/tasks/{task['task_id']}/report")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["report_contract_id"], "icle-agent-task-report/v0.1")
        self.assertIn("requirements_met", body["fields"])
        self.assertFalse(body["intelligence_enabled"])
        # The fake executor prints no form, so nothing is invented for J.
        self.assertEqual(body["consolidated"]["source"], "none")
        self.assertEqual(body["consolidated"]["metrics"], {})
        self.assertEqual([e["report_status"] for e in body["entries"]], ["missing", "missing"])

    def test_report_can_be_filled_by_hand(self) -> None:
        task = self._make_task_full()
        self.client.post(f"/api/tasks/{task['task_id']}/execute")
        filled = self.client.post(
            f"/api/tasks/{task['task_id']}/report",
            json={
                "requirements_total": 2, "requirements_met": 2,
                "verification_ran": True, "verification_passed": True,
                "tests_total": 0, "tests_passed": 0,
                "input_tokens": 100, "output_tokens": 40, "duration_s": 3.2,
                "blocked": False, "summary": "done",
            },
        )
        self.assertEqual(filled.status_code, 200, filled.text)
        self.assertEqual(filled.json()["metrics"]["requirement_coverage"], 1.0)
        body = self.client.get(f"/api/tasks/{task['task_id']}/report").json()
        self.assertEqual(body["consolidated"]["source"], "finishing_agent")
        self.assertEqual(body["consolidated"]["metrics"]["verification_passed"], 1.0)
        self.assertEqual(body["entries"][-1]["report_origin"], "user")

    def test_report_without_evidence_is_rejected(self) -> None:
        task = self._make_task_full()
        self.client.post(f"/api/tasks/{task['task_id']}/execute")
        response = self.client.post(
            f"/api/tasks/{task['task_id']}/report",
            json={"summary": "looks good to me"},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("yields no metric", response.json()["detail"])

    def test_baseline_probe_reports_why_it_cannot_run(self) -> None:
        task = self._make_task_full()
        self.client.post(f"/api/tasks/{task['task_id']}/execute")
        status = self.client.get(
            f"/api/tasks/{task['task_id']}/baseline-probe", params={"agent": "aider"}
        )
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertFalse(body["target"]["available"])
        self.assertEqual(body["target"]["reason"], "agent_model_unknown")
        self.assertEqual(body["probed_task_count"], 0)
        self.assertEqual(body["required_task_count"], 2)
        self.assertFalse(body["ready"])
        run = self.client.post(
            f"/api/tasks/{task['task_id']}/baseline-probe", params={"agent": "aider"}
        )
        self.assertEqual(run.status_code, 400, run.text)
        self.assertIn("agent_model_unknown", run.json()["detail"])

    def test_end_archive_and_restore_have_dedicated_endpoints(self) -> None:
        task = self._make_task()
        self.assertEqual(
            self.client.post(f"/api/tasks/{task['task_id']}/archive").status_code,
            409,
        )
        ended = self.client.post(f"/api/tasks/{task['task_id']}/end")
        self.assertEqual(ended.status_code, 200, ended.text)
        self.assertEqual(ended.json()["status"], "cancelled")
        archived = self.client.post(f"/api/tasks/{task['task_id']}/archive")
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertTrue(archived.json()["archived_at"])
        listed = self.client.get("/api/tasks").json()["tasks"]
        self.assertTrue(next(item for item in listed if item["task_id"] == task["task_id"])["archived_at"])
        restored = self.client.post(f"/api/tasks/{task['task_id']}/unarchive")
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertIsNone(restored.json()["archived_at"])


if __name__ == "__main__":
    unittest.main()
