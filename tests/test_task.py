"""UI-20/22/24/27/29 core tests: Task/TaskProfile/TaskPlan lifecycle + runner.

Uses injected fake executors — never real CLI agents, no LLM, no network.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.active import get_budget  # noqa: E402
from icle.task import (  # noqa: E402
    DIFFICULTY_LEVELS,
    RISK_LEVELS,
    STRATEGIES,
    TASK_STATUSES,
    TASK_TYPES,
    TaskError,
    accept_task,
    apply_agent_assignments,
    approve_plan,
    create_task,
    end_task,
    list_tasks,
    profile_for_task,
    run_task,
    save_plan,
    set_task_archived,
    set_task_profile,
    show_task,
    update_plan_steps,
    update_task,
    validate_task_plan,
    validate_task_profile,
)


def fake_executor_factory(agent: str):
    """Injected executor: writes a marker file, returns success."""

    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(f"done by {agent}", encoding="utf-8")
        return ("completed", f"output of {agent}", "", 0, {"prompt_tokens": 12, "completion_tokens": 8})

    return executor


def failing_executor_factory(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        return ("failed", "", "boom", 1)

    return executor


class TaskCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def _task(self, **kw) -> dict:
        defaults = dict(title="Fix the router", description="Make confidence stable", project_id="icle")
        defaults.update(kw)
        return create_task(self.store, **defaults)

    def _plan(self, task: dict) -> dict:
        return save_plan(
            self.store,
            task["task_id"],
            strategy="DECOMPOSE",
            planner="manual",
            steps=[
                {"title": "Inspect", "description": "read router", "recommended_agent": "kimi",
                 "type": "analysis", "context_policy": "PROJECT_STATE", "risk": "R0",
                 "expected_output": "notes", "verification": "notes exist"},
                {"title": "Implement", "description": "write new algorithm", "recommended_agent": "kimi",
                 "type": "implementation", "context_policy": "ARTIFACT_ONLY", "risk": "R1",
                 "expected_output": "code", "verification": "tests pass"},
            ],
        )

    def test_end_and_archive_are_explicit_lifecycle_actions(self) -> None:
        task = self._task()
        ended = end_task(self.store, task["task_id"])
        self.assertEqual(ended["status"], "cancelled")
        self.assertTrue(ended["cancelled_at"])
        archived = set_task_archived(self.store, task["task_id"], archived=True)
        self.assertTrue(archived["archived_at"])
        restored = set_task_archived(self.store, task["task_id"], archived=False)
        self.assertIsNone(restored["archived_at"])

    def test_archive_rejects_nonterminal_and_end_rejects_running(self) -> None:
        task = self._task()
        with self.assertRaises(TaskError):
            set_task_archived(self.store, task["task_id"], archived=True)
        from icle.task import set_task_status
        set_task_status(self.store, task["task_id"], "running")
        with self.assertRaises(TaskError):
            end_task(self.store, task["task_id"])

    # ---- fixed standards (UI-22)

    def test_fixed_standards_exist(self) -> None:
        self.assertEqual(len(DIFFICULTY_LEVELS), 5)
        self.assertIn("D4", DIFFICULTY_LEVELS)
        self.assertEqual(RISK_LEVELS, ("R0", "R1", "R2", "R3"))
        self.assertIn("DECOMPOSE", STRATEGIES)
        self.assertIn("CODING", TASK_TYPES)
        self.assertIn("approved", TASK_STATUSES)

    def test_profile_validation_rejects_unknown_enums(self) -> None:
        good = {
            "schema_version": "icle-task-profile/v0.1",
            "primary_type": "CODING", "subtype": "refactor", "difficulty": "D4",
            "risk": "R1", "context_requirement": "HIGH",
            "tool_requirement": ["filesystem", "git"], "estimated_duration": "medium",
            "decomposition": "recommended", "review": "recommended",
            "reason": "test", "source": "manual",
        }
        validate_task_profile(good)
        for bad in ({"difficulty": "medium"}, {"risk": "R9"}, {"primary_type": "MAGIC"}):
            with self.assertRaises(TaskError):
                validate_task_profile({**good, **bad})
        # subtype must belong to the primary type's fixed list
        with self.assertRaises(TaskError):
            validate_task_profile({**good, "subtype": "search"})  # search is RESEARCH-only
        # llm profile requires provenance
        with self.assertRaises(TaskError):
            validate_task_profile({**good, "source": "llm"})

    def test_plan_validation_requires_resolvable_dependencies(self) -> None:
        with self.assertRaises(TaskError):
            validate_task_plan(
                {
                    "schema_version": "icle-task-plan/v0.1",
                    "plan_id": "p", "task_id": "t", "planner": "manual",
                    "strategy": "DECOMPOSE", "steps": [
                        {"schema_version": "icle-task-step/v0.1", "step_id": "S1",
                         "title": "a", "description": "b", "type": "analysis",
                         "context_policy": "CLEAN", "depends_on": ["S99"]},
                    ],
                }
            )
        with self.assertRaises(TaskError):
            validate_task_plan(
                {
                    "schema_version": "icle-task-plan/v0.1",
                    "plan_id": "p", "task_id": "t", "planner": "manual",
                    "strategy": "MAGIC", "steps": [
                        {"schema_version": "icle-task-step/v0.1", "step_id": "S1",
                         "title": "a", "description": "b", "type": "analysis",
                         "context_policy": "CLEAN"},
                    ],
                }
            )

    # ---- task lifecycle (UI-20/26)

    def test_create_and_update(self) -> None:
        task = self._task()
        self.assertEqual(task["status"], "draft")
        self.assertTrue(task["task_id"].startswith("t-"))
        updated = update_task(self.store, task["task_id"], title="New title")
        self.assertEqual(updated["title"], "New title")
        self.assertEqual(show_task(self.store, task["task_id"])["title"], "New title")
        self.assertEqual(len(list_tasks(self.store)), 1)

    def test_profile_transitions_draft_to_profiled(self) -> None:
        task = self._task()
        profile = profile_for_task("refactor the confidence algorithm in router.py")
        task = set_task_profile(self.store, task["task_id"], profile)
        self.assertEqual(task["status"], "profiled")
        self.assertEqual(task["profile"]["difficulty"], "D3")

    def test_plan_transitions_to_planned_then_approved(self) -> None:
        task = self._task()
        self._plan(task)
        task = show_task(self.store, task["task_id"])
        self.assertEqual(task["status"], "planned")
        self.assertEqual(task["plan"]["status"], "proposed")
        self.assertEqual([s["step_id"] for s in task["plan"]["steps"]], ["S1", "S2"])
        task = approve_plan(self.store, task["task_id"])
        self.assertEqual(task["status"], "approved")
        self.assertEqual(task["plan"]["status"], "approved")

    def test_revised_plan_can_be_approved_again(self) -> None:
        task = self._task()
        self._plan(task)
        approve_plan(self.store, task["task_id"])
        revised = update_plan_steps(
            self.store,
            task["task_id"],
            steps=[
                {"title": "Changed", "description": "x", "recommended_agent": "kimi",
                 "type": "analysis", "context_policy": "CLEAN"},
            ],
        )
        self.assertEqual(revised["status"], "planned")
        self.assertEqual(revised["plan"]["status"], "revised")
        approved = approve_plan(self.store, task["task_id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["plan"]["status"], "approved")

    def test_agent_assignment_application_updates_plan_before_approval(self) -> None:
        task = self._task()
        self._plan(task)
        result = apply_agent_assignments(
            self.store, task["task_id"], {task["task_id"]: "hermes"}
        )
        self.assertEqual(result["task"]["plan"]["status"], "revised")
        self.assertEqual(result["task"]["status"], "planned")
        self.assertEqual(
            result["task"]["plan"]["steps"][0]["recommended_agent"], "hermes"
        )

    def test_plan_editor_update_preserves_identity(self) -> None:
        task = self._task()
        self._plan(task)
        edited = [
            {"step_id": "S1", "title": "Renamed inspect", "description": "x", "recommended_agent": "hermes",
             "type": "analysis", "context_policy": "CLEAN"},
            {"step_id": "S2", "title": "Implement", "description": "y", "recommended_agent": "kimi",
             "type": "implementation", "context_policy": "ARTIFACT_ONLY"},
        ]
        task = update_plan_steps(self.store, task["task_id"], steps=edited)
        self.assertEqual([s["step_id"] for s in task["plan"]["steps"]], ["S1", "S2"])
        self.assertEqual(task["plan"]["steps"][0]["recommended_agent"], "hermes")
        self.assertEqual(task["plan"]["status"], "revised")

    # ---- runner (UI-29)

    def test_run_requires_approved_plan(self) -> None:
        task = self._task()
        with self.assertRaises(TaskError):
            run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)
        self._plan(task)
        with self.assertRaises(TaskError):
            run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)

    def test_run_steps_recorded_and_budget_consumed(self) -> None:
        task = self._task()
        self._plan(task)
        approve_plan(self.store, task["task_id"])
        run = run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(run["steps"]), 2)
        self.assertTrue(all(s["status"] == "completed" for s in run["steps"]))
        # budget consumed per executed step
        self.assertEqual(get_budget(self.store)["used"].values().__iter__().__next__(), 2)
        # per-step records on disk
        steps_dir = self.store / "tasks" / task["task_id"] / "runs" / run["run_id"] / "steps"
        self.assertEqual(sorted(p.name for p in steps_dir.glob("S*.json")), ["S1.json", "S2.json"])
        task = show_task(self.store, task["task_id"])
        self.assertEqual(task["status"], "review")
        self.assertEqual(task["runs"], [run["run_id"]])

    def test_same_task_cannot_execute_concurrently(self) -> None:
        task = self._task()
        self._plan(task)
        approve_plan(self.store, task["task_id"])
        entered = threading.Event()
        release = threading.Event()
        calls: list[str] = []
        results: list[dict] = []
        errors: list[Exception] = []

        def slow_factory(agent: str):
            def executor(workspace: Path, prompt: str, timeout: float):
                calls.append(agent)
                entered.set()
                release.wait(3)
                return ("completed", "ok", "", 0)
            return executor

        def invoke() -> None:
            try:
                results.append(run_task(self.store, task["task_id"], executor_factory=slow_factory))
            except Exception as exc:  # noqa: BLE001 — assertion records contender result
                errors.append(exc)

        first = threading.Thread(target=invoke)
        second = threading.Thread(target=invoke)
        first.start()
        self.assertTrue(entered.wait(2))
        second.start()
        time.sleep(0.15)
        release.set()
        first.join(5)
        second.join(5)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TaskError)
        persisted = show_task(self.store, task["task_id"])
        self.assertEqual(persisted["runs"], [results[0]["run_id"]])
        self.assertEqual(len(list((self.store / "tasks" / task["task_id"] / "runs").glob("run-*"))), 1)
        self.assertEqual(get_budget(self.store)["used"].get(next(iter(get_budget(self.store)["used"]))), 2)

    def test_run_step_failure_marks_task_failed(self) -> None:
        task = self._task()
        self._plan(task)
        approve_plan(self.store, task["task_id"])
        run = run_task(self.store, task["task_id"], executor_factory=failing_executor_factory)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(show_task(self.store, task["task_id"])["status"], "failed")

    def test_update_task_cannot_bypass_lifecycle(self) -> None:
        """Generic metadata edits cannot fabricate running/cancelled states."""
        task = self._task()
        with self.assertRaises(TypeError):
            update_task(self.store, task["task_id"], status="running")
        self.assertEqual(show_task(self.store, task["task_id"])["status"], "draft")

    def test_accept_task_creates_episode_and_mark(self) -> None:
        task = self._task()
        self._plan(task)
        approve_plan(self.store, task["task_id"])
        run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)
        task = accept_task(self.store, task["task_id"], agent="kimi")
        self.assertEqual(task["status"], "accepted")
        self.assertTrue(task["episode_id"])
        episode_path = self.store / "episodes" / f"{task['episode_id']}.json"
        self.assertTrue(episode_path.is_file())
        marks_dir = self.store / "marks"
        self.assertEqual(len(list(marks_dir.glob("m-*.json"))), 1)

    def test_accept_requires_review_status(self) -> None:
        task = self._task()
        with self.assertRaises(TaskError):
            accept_task(self.store, task["task_id"], agent="kimi")

    def test_run_with_provider_model_agent(self) -> None:
        """agent = 'provider_id/model_id' executes via OpenAI-compatible API.

        Uses a local fake chat endpoint + a configured provider so the runner
        picks the provider executor instead of a CLI agent.
        """
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from icle.provider import save_provider

        class ChatHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                model = payload["model"]
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                reply = {"choices": [{"message": {"content": f"reply from {model}"}}]}
                self.wfile.write(json.dumps(reply).encode("utf-8"))

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), ChatHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base_url = f"http://127.0.0.1:{server.server_port}"
            provider = save_provider(
                self.store,
                {
                    "schema_version": "icle-provider-config/v0.1",
                    "provider_id": "p-fake",
                    "display_name": "Fake",
                    "type": "openai-compatible",
                    "base_url": base_url,
                    "secret_ref": "store",
                    "models": [{"id": "v4-pro"}],
                    "roles": ["general"],
                    "status": "connected",
                    "last_checked_at": None,
                },
                api_key="sk-test",
            )
            from icle.provider import get_provider

            raw = get_provider(self.store, provider["provider_id"])
            save_provider(self.store, {**raw, "status": "connected"})
            task = self._task()
            save_plan(
                self.store,
                task["task_id"],
                strategy="DIRECT",
                planner="manual",
                steps=[
                    {"title": "Ask model", "description": "one step",
                     "recommended_agent": f"{provider['provider_id']}/v4-pro",
                     "type": "analysis", "context_policy": "CLEAN", "risk": "R0"},
                ],
            )
            approve_plan(self.store, task["task_id"])
            run = run_task(self.store, task["task_id"])
            self.assertEqual(run["status"], "completed", run)
            self.assertEqual(run["steps"][0]["agent"], f"{provider['provider_id']}/v4-pro")
            self.assertIn("reply from v4-pro", run["steps"][0]["stdout_tail"])
        finally:
            server.shutdown()
            server.server_close()

    def test_provider_model_agent_unknown_model_falls_back(self) -> None:
        """Unmatched 'provider/model' falls back to CLI executor (unknown agent → fails)."""
        from icle.provider import save_provider

        save_provider(
            self.store,
            {
                "schema_version": "icle-provider-config/v0.1",
                "provider_id": "p-fake",
                "display_name": "Fake",
                "type": "openai-compatible",
                "base_url": "http://127.0.0.1:1",
                "secret_ref": "store",
                "models": [{"id": "v4-pro"}],
                "roles": ["general"],
                "status": "connected",
                "last_checked_at": None,
            },
            api_key="sk-test",
        )
        task = self._task()
        save_plan(
            self.store,
            task["task_id"],
            strategy="DIRECT",
            planner="manual",
            steps=[
                {"title": "x", "description": "y",
                 "recommended_agent": "p-fake/not-a-real-model",
                 "type": "analysis", "context_policy": "CLEAN", "risk": "R0"},
            ],
        )
        approve_plan(self.store, task["task_id"])
        # Unknown targets fail before budget consumption or CLI invocation.
        run = run_task(self.store, task["task_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIn("没有可用执行适配器", run["steps"][0]["stderr_tail"])


if __name__ == "__main__":
    unittest.main()


class EmptyAgentGuardTests(unittest.TestCase):
    """执行链闭环:skill 计划步骤无 agent 时明确失败,不静默崩溃。"""

    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"

    def test_run_without_agent_fails_explicitly(self) -> None:
        from icle.task import TaskError, approve_plan, create_task, run_task, save_plan, show_task

        task = create_task(self.store, title="t", description="d", project_id="p")
        save_plan(self.store, task["task_id"], strategy="DIRECT",
                  steps=[{"step_id": "S1", "title": "s", "description": "d",
                          "recommended_agent": ""}])  # skill 计划:无 agent
        approve_plan(self.store, task["task_id"])
        run = run_task(self.store, task["task_id"], executor_factory=lambda agent: None)
        # M3: 缺 agent → 明确 failed run(统一返回 run 结构),且不扣预算
        self.assertEqual(run["status"], "failed")
        self.assertIn("未指定执行 agent", run["steps"][0]["stderr_tail"])
        self.assertEqual(run["steps"][0]["cost"], None)  # M9: cost 类型统一
        self.assertEqual(show_task(self.store, task["task_id"])["status"], "failed")
        self.assertEqual(get_budget(self.store)["used"], {})  # 预算零消耗


def _read_run(store, task_id, run_id):
    import json
    run_path = store / "tasks" / task_id / "runs" / run_id / "run.json"
    if run_path.is_file():
        return json.loads(run_path.read_text(encoding="utf-8"))
    # 空 agent 提前 return,run.json 未写;从 steps 文件读
    steps_dir = store / "tasks" / task_id / "runs" / run_id / "steps"
    first = sorted(steps_dir.glob("S*.json"))[0]
    return {"steps": [json.loads(first.read_text(encoding="utf-8"))]}
