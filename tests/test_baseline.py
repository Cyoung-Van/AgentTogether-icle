"""Baseline-model probe: local A calibration and the gates that guard it."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.baseline import BaselineError, baseline_target, probe_status, run_baseline_probe  # noqa: E402
from icle.evolution import (  # noqa: E402
    baseline_identity,
    baseline_subject_id_for,
    load_measurement,
    subject_identity,
)
from icle.provider import save_provider  # noqa: E402
from icle.task import (  # noqa: E402
    accept_task,
    approve_plan,
    create_task,
    profile_for_task,
    run_task,
    save_plan,
    set_task_profile,
)

MODEL = "kimi-for-coding"
AGENT_ROUTE = {
    "agent": "kimi", "provider_id": "", "model": MODEL,
    "provider": "kimi-code", "execution_mode": "direct_cli",
}

AGENT_FORM = {
    "requirements_total": 4, "requirements_met": 4,
    "verification_ran": True, "verification_passed": True,
    "tests_total": 4, "tests_passed": 4,
    "input_tokens": 900, "output_tokens": 300, "duration_s": 6.0,
    "blocked": False, "summary": "agent finished everything",
}
BASELINE_FORM = {
    "requirements_total": 4, "requirements_met": 2,
    "verification_ran": True, "verification_passed": False,
    "tests_total": 4, "tests_passed": 2,
    "input_tokens": 700, "output_tokens": 200, "duration_s": 4.0,
    "blocked": False, "summary": "bare model got halfway",
}


def _form_executor(form: dict):
    def factory(agent: str):
        def executor(workspace: Path, prompt: str, timeout: float):
            return ("completed", f"ICLE_TASK_REPORT\n{json.dumps(form)}", "", 0)

        return executor

    return factory


def _split_executor(agent: str):
    """Bare model does worse than the harness — the point of a probe."""
    form = BASELINE_FORM if "/" in agent else AGENT_FORM

    def executor(workspace: Path, prompt: str, timeout: float):
        return ("completed", f"ICLE_TASK_REPORT\n{json.dumps(form)}", "", 0)

    return executor


class _TempStore(unittest.TestCase):
    """Every test gets a throwaway store that is actually thrown away.

    Probe runs clone a workspace per step, so a leaked store is not cheap.
    """

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.store = root / "store"
        self.store.mkdir()


class BaselineTargetTests(_TempStore):
    pass_through = None

    def test_unknown_model_cannot_be_probed(self):
        target = baseline_target(self.store, "aider")
        self.assertFalse(target["available"])
        self.assertEqual(target["reason"], "agent_model_unknown")

    def test_bare_model_agent_is_not_probed_against_itself(self):
        target = baseline_target(self.store, "p-123/some-model")
        self.assertFalse(target["available"])
        self.assertEqual(target["reason"], "agent_is_already_a_bare_model")

    def test_model_without_a_connected_provider_cannot_be_probed(self):
        target = baseline_target(self.store, "kimi")
        self.assertFalse(target["available"])
        self.assertEqual(target["reason"], "no_connected_provider_serves_this_model")

    def test_baseline_matches_the_agent_model_key(self):
        sys.path.insert(0, str(PROJECT_ROOT / "vendor" / "evaluation-engine-with-evolution" / "src"))
        from experience_evaluation.a_local_probe import model_match_key

        agent = subject_identity(AGENT_ROUTE)
        baseline = baseline_identity(AGENT_ROUTE)
        # Same model key (or the engine refuses to overlay A), distinct subject.
        self.assertEqual(model_match_key(agent, {}), model_match_key(baseline, {}))
        self.assertNotEqual(agent["subject_id"], baseline["subject_id"])
        self.assertEqual(baseline["role"], "baseline_model")
        self.assertEqual(baseline["harness"], "generic-agent-shell")

    def test_baseline_subject_id_is_prefixed(self):
        self.assertEqual(baseline_subject_id_for(AGENT_ROUTE), f"baseline:kimi||{MODEL}")


class BaselineProbeTests(_TempStore):
    def setUp(self) -> None:
        super().setUp()
        save_provider(
            self.store,
            {
                "schema_version": "icle-provider-config/v0.1",
                "provider_id": "p-kimi", "display_name": "Kimi Code",
                "type": "openai-compatible", "base_url": "https://api.kimi.com/coding/v1",
                "status": "connected", "models": [{"id": MODEL}],
            },
            api_key="test-key",
        )

    def _accepted_task(self, title: str) -> str:
        task = create_task(
            self.store, title=title,
            description="debug and implement the router fix", project_id="icle",
        )
        task_id = task["task_id"]
        set_task_profile(self.store, task_id, profile_for_task(task["description"], title))
        save_plan(
            self.store, task_id, strategy="DIRECT", planner="manual",
            steps=[{
                "title": "Fix", "description": "patch", "recommended_agent": "kimi",
                "type": "implementation", "context_policy": "PROJECT_STATE", "risk": "R0",
                "depends_on": [],
            }],
        )
        approve_plan(self.store, task_id)
        run_task(self.store, task_id, executor_factory=_split_executor)
        accept_task(self.store, task_id, agent="kimi")
        return task_id

    def test_target_resolves_to_the_configured_provider_model(self):
        target = baseline_target(self.store, "kimi")
        self.assertTrue(target["available"])
        self.assertEqual(target["execution_target"], f"p-kimi/{MODEL}")

    def test_probe_requires_an_approved_plan(self):
        task = create_task(self.store, title="draft", description="x", project_id="icle")
        with self.assertRaises(BaselineError):
            run_baseline_probe(
                self.store, task["task_id"], agent="kimi", executor_factory=_split_executor
            )

    def test_probe_skips_a_task_the_agent_never_froze(self):
        """A is only comparable on a TaskSpec J was scored on."""
        task = create_task(self.store, title="unfrozen", description="x", project_id="icle")
        task_id = task["task_id"]
        set_task_profile(self.store, task_id, profile_for_task("implement a fix", "unfrozen"))
        save_plan(
            self.store, task_id, strategy="DIRECT", planner="manual",
            steps=[{
                "title": "Fix", "description": "patch", "recommended_agent": "kimi",
                "type": "implementation", "context_policy": "CLEAN", "risk": "R0",
                "depends_on": [],
            }],
        )
        approve_plan(self.store, task_id)
        result = run_baseline_probe(
            self.store, task_id, agent="kimi", executor_factory=_split_executor
        )
        self.assertEqual(result["measurement"]["status"], "skipped")
        self.assertEqual(result["measurement"]["reason"], "task_spec_not_frozen")

    def test_probe_refuses_a_spec_it_cannot_match(self):
        """A spec frozen before the form-only contract requires user_accept."""
        task_id = self._accepted_task("Fix one")
        spec_path = self.store / "evolution" / "taskspecs" / f"{task_id}.json"
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec["task_revision"] = "1.1.0"
        spec["metrics"] = [
            {"metric_id": "user_accept", "min": 0, "max": 1,
             "direction": "maximize", "weight": 3, "required": True},
            *spec["metrics"],
        ]
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        result = run_baseline_probe(
            self.store, task_id, agent="kimi", executor_factory=_split_executor
        )
        self.assertEqual(result["measurement"]["reason"], "task_spec_not_probe_comparable")
        self.assertEqual(result["measurement"]["unsatisfiable_metrics"], ["user_accept"])

    def test_a_transport_failure_is_not_recorded_as_a_model_failure(self):
        """Our own bad request is not evidence about the baseline model."""
        task_id = self._accepted_task("Fix one")

        def broken(agent: str):
            def executor(workspace: Path, prompt: str, timeout: float):
                return ("failed", "", "openai provider failed: HTTP Error 400", 1)

            return executor

        result = run_baseline_probe(self.store, task_id, agent="kimi", executor_factory=broken)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["measurement"]["reason"], "probe_execution_failed")
        self.assertIn("400", result["measurement"]["detail"])
        bundles = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in (self.store / "evolution" / "runs" / "bundles").glob("*.json")
        ]
        self.assertEqual(
            [b for b in bundles if str(b["subject_id"]).startswith("baseline:")], []
        )
        self.assertEqual(probe_status(self.store, "kimi")["probed_task_count"], 0)

    def test_probe_records_the_bare_model_form(self):
        task_id = self._accepted_task("Fix one")
        result = run_baseline_probe(
            self.store, task_id, agent="kimi", executor_factory=_split_executor
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["report"]["metrics"]["requirement_coverage"], 0.5)
        self.assertEqual(result["measurement"]["status"], "recorded")
        self.assertEqual(result["measurement"]["subject_id"], f"baseline:kimi||{MODEL}")

    def test_one_probe_is_not_enough_for_a(self):
        task_id = self._accepted_task("Fix one")
        run_baseline_probe(self.store, task_id, agent="kimi", executor_factory=_split_executor)
        status = probe_status(self.store, "kimi")
        self.assertEqual(status["probed_task_count"], 1)
        self.assertEqual(status["required_task_count"], 2)
        self.assertFalse(status["ready"])
        measurement = load_measurement(self.store, AGENT_ROUTE)
        self.assertEqual(measurement["layers"]["a"], "unavailable")

    def test_two_probes_calibrate_a_and_c(self):
        for title in ("Fix one", "Fix two"):
            task_id = self._accepted_task(title)
            run_baseline_probe(
                self.store, task_id, agent="kimi", executor_factory=_split_executor
            )
        status = probe_status(self.store, "kimi")
        self.assertEqual(status["probed_task_count"], 2)
        self.assertTrue(status["ready"])

        measurement = load_measurement(self.store, AGENT_ROUTE)
        # `partial`: only the axes these tasks load get a probe cell. The five
        # axes no task exercised stay honestly unavailable.
        self.assertEqual(measurement["layers"]["a"], "partial")
        for axis in ("mathematics", "language"):
            self.assertIsNone(measurement["axes"][axis]["a"])
        for axis in ("coding", "agentic_coding"):
            row = measurement["axes"][axis]
            self.assertIsNotNone(row["a"], f"{axis} has no A")
            self.assertIsNotNone(row["j"], f"{axis} has no J")
            self.assertIsNotNone(row["c"], f"{axis} has no C")
        # The harness beat its own bare model, so C is a positive residual.
        self.assertGreater(measurement["axes"]["coding"]["c"], 0)

    def test_probe_evidence_never_enters_the_agent_subject(self):
        for title in ("Fix one", "Fix two"):
            task_id = self._accepted_task(title)
            run_baseline_probe(
                self.store, task_id, agent="kimi", executor_factory=_split_executor
            )
        layers = sorted(p.name for p in (self.store / "evolution" / "layers").glob("*.json"))
        self.assertEqual(len(layers), 2, layers)
        agent_layer = json.loads(
            (self.store / "evolution" / "layers" / "kimi____kimi-for-coding.json")
            .read_text(encoding="utf-8")
        )
        j_by_axis = {c["dimension_id"]: c["value"] for c in agent_layer["j"]["cells"]}
        # The agent's J stays the agent's: 4/4 requirements, not the baseline's 2/4.
        self.assertIsNotNone(j_by_axis["coding"])
        self.assertGreater(j_by_axis["coding"], 0)


class RebuildCStateTests(BaselineProbeTests):
    """Recovery path for a polluted belief ledger."""

    def test_a_rebuild_needs_a_reason_and_confirmation(self):
        from icle.evolution import EvolutionError, rebuild_c_state

        with self.assertRaises(EvolutionError):
            rebuild_c_state(self.store, reason="", confirm=True)
        with self.assertRaises(EvolutionError):
            rebuild_c_state(self.store, reason="incident", confirm=False)

    def test_rebuild_retires_the_chain_and_replays_current_evidence(self):
        from icle.evolution import rebuild_c_state

        # Three probed tasks: C becomes available at the second, so the ledger
        # has accumulated more than one observation per axis by the third.
        for title in ("Fix one", "Fix two", "Fix three"):
            task_id = self._accepted_task(title)
            run_baseline_probe(
                self.store, task_id, agent="kimi", executor_factory=_split_executor
            )
        before = json.loads(
            (self.store / "evolution" / "c-state" / "current.json").read_text(encoding="utf-8")
        )
        revisions_before = (
            self.store / "evolution" / "c-state" / "revisions.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        self.assertGreater(len(revisions_before), 1)

        result = rebuild_c_state(self.store, reason="probe incident", confirm=True)
        self.assertEqual(result["status"], "rebuilt")

        # The old chain is preserved for audit, not deleted.
        archive = Path(result["archived"]["path"])
        self.assertTrue((archive / "revisions.jsonl").is_file())
        retirement = json.loads((archive / "retirement.json").read_text(encoding="utf-8"))
        self.assertEqual(retirement["reason"], "probe incident")

        # The retired chain keeps every revision it had.
        self.assertEqual(
            (archive / "revisions.jsonl").read_text(encoding="utf-8").splitlines(),
            revisions_before,
        )
        # The live chain restarts from the current evidence only.
        after = json.loads(
            (self.store / "evolution" / "c-state" / "current.json").read_text(encoding="utf-8")
        )
        counts = {
            (row["subject_id"], row["dimension_id"]): row["evidence_count"]
            for row in after["states"]
        }
        self.assertTrue(counts)
        self.assertEqual(set(counts.values()), {1}, counts)
        self.assertGreaterEqual(
            max(row["evidence_count"] for row in before["states"]), 2
        )

    def test_rebuild_keeps_the_matrix_readable(self):
        from icle.evolution import rebuild_c_state

        for title in ("Fix one", "Fix two"):
            task_id = self._accepted_task(title)
            run_baseline_probe(
                self.store, task_id, agent="kimi", executor_factory=_split_executor
            )
        rebuild_c_state(self.store, reason="probe incident", confirm=True)
        measurement = load_measurement(self.store, AGENT_ROUTE)
        self.assertEqual(measurement["layers"]["c"], "available")
        self.assertIsNotNone(measurement["axes"]["coding"]["c"])


if __name__ == "__main__":
    unittest.main()
