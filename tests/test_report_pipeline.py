"""End-to-end: the mandatory form must turn J from unavailable into observed."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.decision import score_measurement  # noqa: E402
from icle.evolution import load_measurement  # noqa: E402
from icle.report import REPORT_MARKER  # noqa: E402
from icle.task import (  # noqa: E402
    accept_task,
    approve_plan,
    collect_report_entries,
    consolidate_task_report,
    create_children,
    create_task,
    latest_run,
    profile_for_task,
    run_task,
    save_plan,
    set_manual_report,
    set_task_profile,
)

FORM = {
    "requirements_total": 4,
    "requirements_met": 4,
    "verification_ran": True,
    "verification_passed": True,
    "tests_total": 6,
    "tests_passed": 6,
    "input_tokens": 1200,
    "output_tokens": 400,
    "cache_read_tokens": 0,
    "reasoning_tokens": 0,
    "duration_s": 8.5,
    "files_changed": ["src/router.py"],
    "commands_run": ["pytest -q"],
    "blocked": False,
    "blocked_reason": "",
    "summary": "Fixed the router and verified it.",
}
# The explicit test binding below makes accept and lookup use the same identity.
ROUTE = {
    "agent": "kimi",
    "provider_id": "",
    "model": "kimi-for-coding",
    "execution_mode": "direct_cli",
}


def reporting_executor(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(f"done by {agent}", encoding="utf-8")
        return ("completed", f"work log\n{REPORT_MARKER}\n{json.dumps(FORM)}\n", "", 0)

    return executor


def silent_executor(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        return ("completed", "done, no form here", "", 0)

    return executor


def metered_silent_executor(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        return ("completed", "done, no form here", "", 0, {"prompt_tokens": 20, "completion_tokens": 10})

    return executor


def partial_executor(agent: str):
    """Each agent finishes a different amount of work."""
    done = {"kimi": 1, "hermes": 3}.get(agent, 2)
    form = {**FORM, "requirements_total": 3, "requirements_met": done, "summary": f"{agent} pass"}

    def executor(workspace: Path, prompt: str, timeout: float):
        return ("completed", f"{REPORT_MARKER}\n{json.dumps(form)}", "", 0)

    return executor


class FakeProvider:
    """Intelligence layer stand-in for the consolidation summary."""

    name = "fake"
    model = "fake-model"
    revision = "fake-rev"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps({
            "summary": "The chain delivered the router fix across two subtasks.",
            "blocked_reason": "",
        })


class ReportPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()
        configured_identity = patch("icle.external_evaluation._configured_model_identity", return_value=None)
        configured_identity.start()
        self.addCleanup(configured_identity.stop)
        (self.store / "agent-models.json").write_text(
            json.dumps({"kimi": {"model": ROUTE["model"], "provider": "kimi-code"}}),
            encoding="utf-8",
        )

    def _coding_task(self, title: str = "Fix bug") -> str:
        task = create_task(
            self.store,
            title=title,
            description="debug and implement the router fix",
            project_id="icle",
        )
        set_task_profile(
            self.store, task["task_id"], profile_for_task(task["description"], task["title"])
        )
        save_plan(
            self.store,
            task["task_id"],
            strategy="DIRECT",
            planner="manual",
            steps=[{
                "title": "Fix", "description": "patch", "recommended_agent": "kimi",
                "type": "implementation", "context_policy": "PROJECT_STATE", "risk": "R0",
                "depends_on": [],
            }],
        )
        approve_plan(self.store, task["task_id"])
        return task["task_id"]

    # ------------------------------------------------------------ capture

    def test_form_is_demanded_in_the_prompt(self):
        seen: list[str] = []

        def factory(agent: str):
            def executor(workspace: Path, prompt: str, timeout: float):
                seen.append(prompt)
                return ("completed", "no form", "", 0)

            return executor

        run_task(self.store, self._coding_task(), executor_factory=factory)
        self.assertIn(REPORT_MARKER, seen[0])
        self.assertIn("requirements_met", seen[0])

    def test_step_record_captures_the_form(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=reporting_executor)
        step = latest_run(self.store, task_id)["steps"][0]
        self.assertEqual(step["report_status"], "observed")
        self.assertEqual(step["report"]["requirements_met"], 4)

    def test_missing_form_is_flagged_not_invented(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=silent_executor)
        step = latest_run(self.store, task_id)["steps"][0]
        self.assertEqual(step["report_status"], "missing")
        self.assertIsNone(step["report"])
        consolidated = consolidate_task_report(self.store, task_id)
        self.assertEqual(consolidated["metrics"], {})

    # ------------------------------------------------------------ J becomes observed

    def test_accept_with_form_publishes_j(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=reporting_executor)
        accept_task(self.store, task_id, agent="kimi")

        bundles = list((self.store / "evolution" / "runs" / "bundles").glob("*.json"))
        bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
        metrics = bundle["outcome"]["metrics"]
        self.assertEqual(metrics["requirement_coverage"], 1.0)
        self.assertEqual(metrics["verification_passed"], 1.0)
        self.assertEqual(metrics["test_pass_rate"], 1.0)
        # Diagnostics are recorded, but never as scorable metrics.
        self.assertNotIn("duration_s", metrics)
        self.assertNotIn("exit_code", metrics)
        self.assertEqual(bundle["outcome"]["diagnostics"]["exit_code"], 0)

        layers = list((self.store / "evolution" / "layers").glob("*.json"))
        self.assertEqual(len(layers), 1)
        j_layer = json.loads(layers[0].read_text(encoding="utf-8"))["j"]
        self.assertEqual(j_layer["status"], "observed")
        scored = {
            cell["dimension_id"] for cell in j_layer["cells"] if cell["value"] is not None
        }
        self.assertEqual(scored, {"coding", "agentic_coding"})

    def test_j_reaches_the_decision_layer(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=reporting_executor)
        accept_task(self.store, task_id, agent="kimi")

        measurement = load_measurement(self.store, ROUTE)
        for axis in ("coding", "agentic_coding"):
            self.assertIsNotNone(measurement["axes"][axis]["j"])
        signal = score_measurement(measurement, {"primary_type": "CODING"})
        self.assertEqual(signal["quality_source"], "j_observed")
        self.assertIsNotNone(signal["quality"])

    def test_without_a_form_there_is_no_scorable_metric(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=metered_silent_executor)
        accept_task(self.store, task_id, agent="kimi")
        bundle = json.loads(
            next(iter((self.store / "evolution" / "runs" / "bundles").glob("*.json")))
            .read_text(encoding="utf-8")
        )
        # No form means no metrics — nothing is invented to fill the gap. The
        # accept survives as a diagnostic, it just does not grade the work.
        self.assertEqual(bundle["outcome"]["metrics"], {})
        self.assertEqual(bundle["outcome"]["diagnostics"]["report_status"], "missing")
        self.assertEqual(bundle["outcome"]["diagnostics"]["user_accept"], 1.0)

    def test_spec_declares_exactly_the_form_metrics(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=reporting_executor)
        accept_task(self.store, task_id, agent="kimi")
        spec = json.loads(
            next(iter((self.store / "evolution" / "taskspecs").glob("*.json"))).read_text(encoding="utf-8")
        )
        self.assertEqual(
            [metric["metric_id"] for metric in spec["metrics"]],
            ["requirement_coverage", "verification_passed", "test_pass_rate"],
        )
        self.assertEqual(spec["task_revision"], "2.0.0")

    # ------------------------------------------------------------ manual fill

    def test_accept_without_tokens_is_blocked(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=silent_executor)
        from icle.task import TaskError
        with self.assertRaises(TaskError):
            accept_task(self.store, task_id, agent="kimi")

    def test_user_can_fill_a_missing_form(self):
        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=silent_executor)
        outcome = set_manual_report(self.store, task_id, FORM)
        self.assertEqual(outcome["metrics"]["requirement_coverage"], 1.0)
        entries = collect_report_entries(self.store, task_id)
        self.assertEqual(entries[0]["report_origin"], "user")
        self.assertEqual(entries[0]["report_status"], "observed")
        from icle.cost import list_actuals

        actuals = list_actuals(self.store, limit=20)
        corrections = [item for item in actuals if item.get("operation") == "report_consumption"]
        self.assertTrue(corrections)
        self.assertIsNone(corrections[-1].get("duration_ms"))

    def test_manual_form_without_evidence_is_rejected(self):
        from icle.report import ReportError

        task_id = self._coding_task()
        run_task(self.store, task_id, executor_factory=silent_executor)
        with self.assertRaises(ReportError):
            set_manual_report(self.store, task_id, {"summary": "looks fine"})

    # ------------------------------------------------------------ multi-agent chain

    def _chain(self) -> str:
        """Parent + one child, each executed by a different agent."""
        parent_id = self._coding_task("Parent")
        child = create_children(
            self.store, parent_id, [{"title": "Child", "description": "second half"}]
        )[0]
        set_task_profile(self.store, child["task_id"], profile_for_task("implement", "Child"))
        save_plan(
            self.store,
            child["task_id"],
            strategy="DIRECT",
            planner="manual",
            steps=[{
                "title": "Finish", "description": "wrap up", "recommended_agent": "hermes",
                "type": "implementation", "context_policy": "PROJECT_STATE", "risk": "R0",
                "depends_on": [],
            }],
        )
        approve_plan(self.store, child["task_id"])
        run_task(self.store, parent_id, executor_factory=partial_executor)
        run_task(self.store, child["task_id"], executor_factory=partial_executor)
        return parent_id

    def test_chain_collects_every_agent_form_in_order(self):
        entries = collect_report_entries(self.store, self._chain())
        self.assertEqual([e["agent"] for e in entries], ["kimi", "hermes"])
        self.assertEqual([e["order"] for e in entries], [1, 2])
        self.assertTrue(all(e["report_status"] == "observed" for e in entries))

    def test_without_intelligence_the_finishing_agent_form_wins(self):
        parent_id = self._chain()
        consolidated = consolidate_task_report(self.store, parent_id)
        self.assertEqual(consolidated["source"], "finishing_agent")
        # hermes finished 3/3; kimi's 1/3 does not decide the task.
        self.assertEqual(consolidated["metrics"]["requirement_coverage"], 1.0)
        self.assertIn("hermes pass", consolidated["report"]["summary"])

    def test_with_intelligence_the_subtask_forms_are_summarized(self):
        parent_id = self._chain()
        provider = FakeProvider()
        consolidated = consolidate_task_report(self.store, parent_id, provider=provider)
        self.assertEqual(consolidated["source"], "intelligence_summary")
        self.assertEqual(consolidated["reason"], "llm_summary")
        # Counts come from the deterministic merge (1+3 of 3+3), not from the LLM.
        self.assertEqual(consolidated["report"]["requirements_total"], 6)
        self.assertEqual(consolidated["report"]["requirements_met"], 4)
        self.assertAlmostEqual(consolidated["metrics"]["requirement_coverage"], 4 / 6, places=5)
        self.assertIn("two subtasks", consolidated["report"]["summary"])
        self.assertEqual([row["task_id"] for row in consolidated["inputs"]].count(parent_id), 1)

    def test_summary_falls_back_to_the_merge_when_the_llm_fails(self):
        class Broken(FakeProvider):
            def complete(self, prompt: str) -> str:
                raise RuntimeError("provider down")

        parent_id = self._chain()
        consolidated = consolidate_task_report(self.store, parent_id, provider=Broken())
        self.assertEqual(consolidated["source"], "intelligence_summary")
        self.assertIn("llm_summary_failed", consolidated["reason"])
        self.assertEqual(consolidated["report"]["requirements_met"], 4)

    def test_disabled_provider_uses_the_finishing_agent(self):
        class Off(FakeProvider):
            name = "none"

        parent_id = self._chain()
        consolidated = consolidate_task_report(self.store, parent_id, provider=Off())
        self.assertEqual(consolidated["source"], "finishing_agent")


if __name__ == "__main__":
    unittest.main()
