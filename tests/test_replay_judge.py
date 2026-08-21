"""P3/P4: replay engine + human evaluation + light router tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.episode import create_episode  # noqa: E402
from icle.judge import record_judgment, record_result_mark, route_suggest  # noqa: E402
from icle.replay import ReplayError, build_context_bundle, list_replays, replay  # noqa: E402


def revision(agent: str) -> dict:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent,
        "revision_id": "rev-1",
        "model": "m", "cli": "c", "provider": "p",
        "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": "2026-08-14T00:00:00Z",
    }


def make_git_project(path: Path, *, with_future: bool = True) -> None:
    (path / "app.py").write_text("print('v1')\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, env=env, check=True)
    if with_future:
        # later change: replay must NOT see this
        (path / "app.py").write_text("print('v2-future')\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-q", "-am", "future"], cwd=path, env=env, check=True)


def fake_executor_factory(marker: str, code: int = 0):
    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "result.txt").write_text(marker, encoding="utf-8")
        return ("completed" if code == 0 else "failed", f"did {marker}", "", code)

    return executor


class ReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.project = self.root / "proj"
        self.project.mkdir()
        # episode is created at v1; the future commit lands AFTER marking
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.project),
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        make_git_project(self.project, with_future=False)
        self.store = self.root / "store"
        self.episode = create_episode(
            self.store,
            session_id="s-1",
            agent_revision=revision("hermes"),
            project_id="demo",
            project_path=self.project,
            user_request="improve the app",
        )
        (self.project / "app.py").write_text("print('v2-future')\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-q", "-am", "future"], cwd=self.project,
                       env=self.env, check=True)

    def test_replay_restores_t0_not_future(self) -> None:
        run = replay(
            self.store, self.episode["episode_id"],
            target_agent_revision=revision("kimi"),
            executor=fake_executor_factory("done"),
        )
        self.assertEqual(run["status"], "completed")
        workspace = self.store / "replays" / run["replay_id"] / "workspace"
        content = (workspace / "app.py").read_text(encoding="utf-8")
        self.assertIn("v1", content)
        self.assertNotIn("v2-future", content)  # T0 restored, future invisible
        self.assertEqual(run["context_bundle"]["mode"], "CLEAN")

    def test_selected_history_includes_only_user_events(self) -> None:
        capture = self.root / "capture"
        events_dir = capture / "hermes" / "s-1"
        events_dir.mkdir(parents=True)
        (events_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"seq": 1, "kind": "user", "content": "please improve it"}),
                    json.dumps({"seq": 2, "kind": "assistant", "content": "SOURCE ANSWER SECRET"}),
                    json.dumps({"seq": 3, "kind": "user", "content": "and keep style"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        episode = create_episode(
            self.store,
            session_id="s-1",
            agent_revision=revision("hermes"),
            project_id="demo",
            project_path=self.project,
            user_request="improve the app",
            from_seq=1,
            to_seq=3,
            capture_store=capture,
        )
        bundle = build_context_bundle(episode, "SELECTED_HISTORY", capture_store=capture)
        self.assertIn("please improve it", bundle["text"])
        self.assertIn("and keep style", bundle["text"])
        self.assertNotIn("SOURCE ANSWER SECRET", bundle["text"])

    def test_selected_history_requires_turn_range(self) -> None:
        with self.assertRaises(ReplayError):
            build_context_bundle(self.episode, "SELECTED_HISTORY", capture_store=self.root)

    def test_replay_runs_are_isolated_and_listed(self) -> None:
        first = replay(self.store, self.episode["episode_id"],
                       target_agent_revision=revision("kimi"),
                       executor=fake_executor_factory("A"))
        second = replay(self.store, self.episode["episode_id"],
                        target_agent_revision=revision("hermes"),
                        executor=fake_executor_factory("B"))
        self.assertNotEqual(first["replay_id"], second["replay_id"])
        listing = list_replays(self.store, self.episode["episode_id"])
        self.assertEqual(len(listing), 2)
        a = self.store / "replays" / first["replay_id"] / "workspace" / "result.txt"
        b = self.store / "replays" / second["replay_id"] / "workspace" / "result.txt"
        self.assertEqual(a.read_text(), "A")
        self.assertEqual(b.read_text(), "B")  # no cross-contamination


class JudgeAndRouteTests(unittest.TestCase):
    def test_full_loop_episode_replay_pairwise_mark_route(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        episode = create_episode(
            store, session_id="s-1", agent_revision=revision("hermes"),
            project_id="demo", project_path=project, user_request="improve the app",
        )
        rp_a = replay(store, episode["episode_id"], target_agent_revision=revision("hermes"),
                      executor=fake_executor_factory("A"))
        rp_b = replay(store, episode["episode_id"], target_agent_revision=revision("kimi"),
                      executor=fake_executor_factory("B"))
        # pairwise: prefer kimi's replay
        result = record_judgment(
            store,
            subject={"type": "replay_pair", "a": rp_a["replay_id"], "b": rp_b["replay_id"]},
            kind="prefer_b",
            reason_tags=["correctness"],
        )
        self.assertTrue(result["judgment"]["judgment_id"])
        record_result_mark(store, episode_id=episode["episode_id"], mark="accept", agent_id="hermes")
        record_result_mark(store, episode_id=episode["episode_id"], mark="reject", agent_id="hermes")
        record_result_mark(store, episode_id=episode["episode_id"], mark="accept", agent_id="kimi")

        suggestion = route_suggest(store)
        ranking = {entry["agent"]: entry for entry in suggestion["ranking"]}
        self.assertAlmostEqual(ranking["kimi"]["adopt_rate"], 1.0)
        self.assertAlmostEqual(ranking["kimi"]["win_rate"], 1.0)
        self.assertAlmostEqual(ranking["hermes"]["adopt_rate"], 0.5)
        self.assertAlmostEqual(ranking["hermes"]["win_rate"], 0.0)
        self.assertEqual(suggestion["ranking"][0]["agent"], "kimi")
        # ledger contains chained records for every judgment/mark
        from icle.ledger import ExperienceLedger

        ledger = ExperienceLedger(store / "ledger")
        self.assertEqual(len(ledger.records()), 4)  # 1 judgment + 3 marks

    def test_no_evidence_routes_last_with_flag(self) -> None:
        suggestion = route_suggest(Path(tempfile.mkdtemp()))
        self.assertEqual(suggestion["ranking"], [])


if __name__ == "__main__":
    unittest.main()
