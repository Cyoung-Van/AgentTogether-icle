"""Contract tests for the A+B+C evaluation adapter."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.evolution import (  # noqa: E402
    CANONICAL_AXES,
    FORBIDDEN_TOTAL_KEYS,
    build_run_bundle,
    capability_loadings_for,
    freeze_task_spec,
    load_measurement,
    record_accept_measurement,
    subject_id_for,
    subject_identity,
)
from icle.task import (  # noqa: E402
    accept_task,
    approve_plan,
    create_task,
    profile_for_task,
    run_task,
    save_plan,
    set_task_profile,
)


def fake_executor_factory(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(f"done by {agent}", encoding="utf-8")
        return ("completed", f"output of {agent}", "", 0, {"prompt_tokens": 12, "completion_tokens": 8})

    return executor


class EvolutionAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()

    def test_q_mapping_is_conservative(self) -> None:
        self.assertEqual(
            capability_loadings_for({"primary_type": "CODING"}),
            {"coding": 1.0, "agentic_coding": 1.0},
        )
        self.assertEqual(
            capability_loadings_for({"primary_type": "CODING", "subtype": "refactor"}),
            {"coding": 1.0},
        )
        self.assertEqual(
            capability_loadings_for({"primary_type": "SYSTEM_OPERATION"}),
            {"agentic_coding": 1.0},
        )
        self.assertEqual(
            capability_loadings_for({"primary_type": "RESEARCH"}),
            {"data_analysis": 1.0},
        )
        self.assertEqual(
            capability_loadings_for({"primary_type": "PLANNING"}),
            {"reasoning": 1.0, "instruction_following": 1.0},
        )
        self.assertEqual(
            capability_loadings_for({"primary_type": "WRITING"}),
            {"language": 1.0},
        )
        self.assertEqual(capability_loadings_for({"primary_type": "OTHER"}), {})
        self.assertEqual(capability_loadings_for({}), {})

    def test_unmapped_task_does_not_freeze_a_fake_q(self) -> None:
        spec = freeze_task_spec(self.store, {
            "task_id": "t-other",
            "title": "misc",
            "profile": {"primary_type": "OTHER"},
        })
        self.assertIsNone(spec)
        self.assertFalse((self.store / "evolution" / "taskspecs").exists())

    def test_task_spec_freeze_is_idempotent(self) -> None:
        task = {
            "task_id": "t-code",
            "title": "fix bug",
            "profile": {"primary_type": "CODING", "subtype": "debugging"},
        }
        first = freeze_task_spec(self.store, task)
        task["profile"] = {"primary_type": "PLANNING"}
        second = freeze_task_spec(self.store, task)
        self.assertEqual(first["capability_loadings"], {"coding": 1.0, "agentic_coding": 1.0})
        self.assertEqual(second["capability_loadings"], first["capability_loadings"])
        self.assertEqual(first["q_contract_id"], "icle-taskprofile-to-livebench-q/v0.1")

    def test_run_bundle_hash_is_idempotent(self) -> None:
        task = {"task_id": "t-code", "profile": {"primary_type": "CODING"}}
        run = {
            "run_id": "run-1",
            "started_at": "2026-08-21T00:00:00+00:00",
            "ended_at": "2026-08-21T00:01:00+00:00",
            "steps": [{"duration_ms": 1200, "exit_code": 0}],
        }
        route = {"agent": "kimi", "provider_id": "", "model": "kimi-for-coding"}
        first = build_run_bundle(task=task, run=run, route=route, mark="accept")
        second = build_run_bundle(task=task, run=run, route=route, mark="accept")
        self.assertEqual(first["content_hash"], second["content_hash"])
        # The accept is a diagnostic, not a scored metric (see report.py).
        self.assertEqual(first["outcome"]["diagnostics"]["user_accept"], 1.0)
        self.assertNotIn("prompt", json.dumps(first))
        self.assertNotIn("stdout", json.dumps(first))

    def test_subject_id_keeps_provider_isolation(self) -> None:
        left = subject_id_for({
            "agent": "p-aaa", "provider_id": "p-aaa", "model": "deepseek-v4-flash",
        })
        right = subject_id_for({
            "agent": "p-bbb", "provider_id": "p-bbb", "model": "deepseek-v4-flash",
        })
        self.assertNotEqual(left, right)
        identity = subject_identity({
            "agent": "hermes", "provider_id": "", "model": "gpt-5.6-luna",
            "execution_mode": "direct_cli",
        })
        self.assertEqual(identity["harness"], "hermes-agent")

    def test_kimi_measurement_has_seven_axes_and_no_total(self) -> None:
        measurement = load_measurement(self.store, {
            "agent": "kimi", "provider_id": "", "model": "kimi-for-coding",
        })
        self.assertEqual(set(measurement["axes"]), set(CANONICAL_AXES))
        self.assertFalse(measurement["ranking"])
        self.assertEqual(measurement["observed_axis_count"], 0)
        self.assertTrue(FORBIDDEN_TOTAL_KEYS.isdisjoint(measurement))
        for axis, row in measurement["axes"].items():
            self.assertEqual(row["status"], "unavailable")
            self.assertIsNone(row["c"])
            self.assertIsNotNone(axis)

    def test_accept_records_local_controlled_bundle(self) -> None:
        task = create_task(self.store, title="Fix bug", description="debug and implement the router fix", project_id="icle")
        set_task_profile(self.store, task["task_id"], profile_for_task(task["description"], task["title"]))
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
        run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)
        accepted = accept_task(self.store, task["task_id"], agent="kimi")
        self.assertEqual(accepted["status"], "accepted")
        specs = list((self.store / "evolution" / "taskspecs").glob("*.json"))
        self.assertEqual(len(specs), 1)
        bundles = list((self.store / "evolution" / "runs" / "bundles").glob("*.json"))
        self.assertEqual(len(bundles), 1)
        bundle = json.loads(bundles[0].read_text(encoding="utf-8"))
        self.assertEqual(bundle["outcome"]["verifier_id"], "icle-user-accept/v0.1")
        self.assertEqual(bundle["outcome"]["publication_scope"], "local_controlled")

    def test_accept_survives_measurement_failure(self) -> None:
        task = create_task(self.store, title="Fix bug", description="debug and implement the router fix", project_id="icle")
        set_task_profile(self.store, task["task_id"], profile_for_task(task["description"], task["title"]))
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
        run_task(self.store, task["task_id"], executor_factory=fake_executor_factory)
        with mock.patch("icle.evolution.record_accept_measurement", side_effect=RuntimeError("boom")):
            accepted = accept_task(self.store, task["task_id"], agent="kimi")
        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(accepted["episode_id"])

    def test_record_skips_when_q_unmapped(self) -> None:
        result = record_accept_measurement(
            self.store,
            task={"task_id": "t-1", "profile": {"primary_type": "OTHER"}},
            run={"run_id": "r-1"},
            agent="kimi",
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "q_unmapped")


if __name__ == "__main__":
    unittest.main()
