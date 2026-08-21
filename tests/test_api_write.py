"""UI-06..14: write-path API tests (replay, judgment, recommend, activity, settings)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from icle.active import get_budget, set_budget  # noqa: E402
from icle.api.app import create_app  # noqa: E402
from icle.episode import create_episode  # noqa: E402


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


def make_git_project(path: Path) -> None:
    (path / "app.py").write_text("x = 1\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, env=env, check=True)


def fake_executor_factory(agent: str):
    def executor(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(f"by {agent}", encoding="utf-8")
        return ("completed", f"output of {agent}", "", 0)

    return executor


class WriteApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(tempfile.mkdtemp())
        project = cls.root / "proj"
        project.mkdir()
        make_git_project(project)
        cls.store = cls.root / "store"
        cls.episode = create_episode(
            cls.store, session_id="s1", agent_revision=revision("hermes"),
            project_id="demo", project_path=project, user_request="fix the bug",
        )
        cls.app = create_app(store=cls.store)
        cls.app.state.executor_factory = fake_executor_factory
        cls.client = TestClient(cls.app)

    def setUp(self) -> None:
        # Tests share evidence fixtures but never depend on test order/budget mutations.
        set_budget(self.store, 100)

    def _start_replay(self, agent: str) -> str:
        response = self.client.post(
            f"/api/episodes/{self.episode['episode_id']}/replays",
            json={"agent_id": agent, "context_mode": "CLEAN"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        replay_id = response.json()["replay_id"]
        for _ in range(50):
            if (self.store / "replays" / replay_id / "replay.json").is_file():
                return replay_id
            time.sleep(0.1)
        self.fail("replay did not complete")

    def test_replay_full_flow(self) -> None:
        replay_id = self._start_replay("kimi")
        detail = self.client.get(f"/api/replays/{replay_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["replay"]["status"], "completed")
        self.assertIn("output of kimi", detail.json()["stdout_tail"])

    def test_replay_failure_is_persisted(self) -> None:
        def raising_factory(agent: str):
            def executor(workspace: Path, prompt: str, timeout: float):
                raise RuntimeError("injected replay failure")
            return executor

        original = self.app.state.executor_factory
        self.app.state.executor_factory = raising_factory
        try:
            replay_id = self.client.post(
                f"/api/episodes/{self.episode['episode_id']}/replays",
                json={"agent_id": "kimi", "context_mode": "CLEAN"},
            ).json()["replay_id"]
            record = self.store / "replays" / replay_id / "replay.json"
            for _ in range(50):
                if record.is_file():
                    break
                time.sleep(0.1)
            self.assertTrue(record.is_file())
            self.assertEqual(json.loads(record.read_text())["status"], "failed")
            self.assertIn(
                "injected replay failure",
                (self.store / "replays" / replay_id / "stderr.log").read_text(),
            )
        finally:
            self.app.state.executor_factory = original

    def test_replay_rejects_unknown_agent_and_episode(self) -> None:
        bad_agent = self.client.post(
            f"/api/episodes/{self.episode['episode_id']}/replays",
            json={"agent_id": "hal"},
        )
        self.assertEqual(bad_agent.status_code, 400)
        missing = self.client.post(
            "/api/episodes/ep-9999/replays", json={"agent_id": "kimi"}
        )
        self.assertEqual(missing.status_code, 404)

    def test_compare_and_judgment_writes_ledger(self) -> None:
        first = self._start_replay("kimi")
        second = self._start_replay("hermes")
        compare = self.client.get("/api/compare", params={"a": first, "b": second})
        self.assertEqual(compare.status_code, 200)
        self.assertEqual(compare.json()["episode_id"], self.episode["episode_id"])

        judgment = self.client.post(
            "/api/judgments",
            json={"pair": [first, second], "kind": "prefer_a", "reason_tags": ["speed"]},
        )
        self.assertEqual(judgment.status_code, 200, judgment.text)
        ledger_path = self.store / "ledger" / "ledger.jsonl"
        self.assertTrue(ledger_path.is_file())
        kinds = [json.loads(line)["kind"] for line in ledger_path.read_text().splitlines()]
        self.assertIn("judgment", kinds)

        different_episode = self.client.get("/api/compare", params={"a": first, "b": "rp-9999"})
        self.assertEqual(different_episode.status_code, 404)

    def test_recommend_and_agent_profile_and_activity_and_settings(self) -> None:
        self._start_replay("kimi")
        record = self.client.post(
            "/api/marks",
            json={
                "episode_id": self.episode["episode_id"],
                "agent_id": "kimi",
                "mark": "accept",
            },
        )
        self.assertEqual(record.status_code, 200)
        profile = self.client.get("/api/agents/kimi")
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.json()["accepted"], 1)

        with patch("icle.api.misc.executable_candidates", return_value=["kimi"]):
            recommendation = self.client.post(
                "/api/recommend", json={"task": "fix another bug", "project_id": "demo"}
            )
        self.assertEqual(recommendation.status_code, 200, recommendation.text)
        body = recommendation.json()
        self.assertIsNone(body["recommended"])
        self.assertEqual(body["policy"], "experience-router-v0.4")
        self.assertTrue(body["ranking"][0]["exploration"])

        activity = self.client.get("/api/activity")
        self.assertEqual(activity.status_code, 200)
        self.assertTrue(any(i["type"] == "mark" for i in activity.json()["activity"]))

        updated = self.client.post("/api/settings", json={"replay_per_day": 3})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["replay_per_day"], 3)

    def test_review_collab_runs_both_agents_and_hides_path(self) -> None:
        response = self.client.post(
            "/api/collabs",
            json={
                "mode": "review",
                "episode_id": self.episode["episode_id"],
                "agents": ["hermes", "kimi"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        collab = response.json()["collab"]
        self.assertEqual([step["role"] for step in collab["steps"]], ["author", "reviewer"])
        self.assertNotIn("run_dir", collab)
        listed = self.client.get("/api/collabs").json()["collabs"]
        self.assertTrue(listed)
        self.assertTrue(all("run_dir" not in item for item in listed))

    def test_template_collab_uses_fixed_phases_and_multiple_agents(self) -> None:
        recommendation = self.client.get(
            "/api/collab-template-recommendation",
            params={"episode_id": self.episode["episode_id"]},
        )
        self.assertEqual(recommendation.status_code, 200, recommendation.text)
        self.assertTrue(recommendation.json()["recommendations"])

        assignments = [
            {"key": "reproduce", "agent": "hermes"},
            {"key": "root_cause", "agent": "kimi"},
            {"key": "implement", "agent": "hermes"},
            {"key": "regression", "agent": "kimi"},
        ]
        response = self.client.post(
            "/api/collabs",
            json={
                "mode": "template",
                "episode_id": self.episode["episode_id"],
                "template_id": "bugfix",
                "assignments": assignments,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        collab = response.json()["collab"]
        self.assertEqual(collab["pattern"], "template-workflow")
        self.assertEqual(collab["template"]["template_id"], "bugfix")
        self.assertEqual([step["role"] for step in collab["steps"]], [item["key"] for item in assignments])
        self.assertEqual({step["agent"] for step in collab["steps"]}, {"hermes", "kimi"})

    def test_invalid_template_collab_does_not_consume_budget(self) -> None:
        before = sum(get_budget(self.store)["used"].values())
        response = self.client.post(
            "/api/collabs",
            json={
                "mode": "template",
                "episode_id": self.episode["episode_id"],
                "template_id": "bugfix",
                "assignments": [
                    {"key": "reproduce", "agent": "hermes"},
                    {"key": "root_cause", "agent": "kimi"},
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("required template phases", response.json()["detail"])
        self.assertEqual(sum(get_budget(self.store)["used"].values()), before)

    def test_activity_limit_and_invalid_references(self) -> None:
        self.assertEqual(self.client.get("/api/activity?limit=0").status_code, 400)
        self.assertEqual(self.client.get("/api/activity?limit=201").status_code, 400)
        invalid_compare = self.client.get("/api/compare", params={"a": "../x", "b": "rp-0001"})
        self.assertEqual(invalid_compare.status_code, 400)
        missing_mark = self.client.post(
            "/api/marks", json={"episode_id": "ep-nope", "agent_id": "kimi", "mark": "accept"}
        )
        self.assertEqual(missing_mark.status_code, 404)
        unrelated = self.client.post(
            "/api/marks",
            json={"episode_id": self.episode["episode_id"], "agent_id": "codex", "mark": "accept"},
        )
        self.assertEqual(unrelated.status_code, 400)

    def test_http_token_authentication(self) -> None:
        previous = os.environ.get("ICLE_TOKEN")
        os.environ["ICLE_TOKEN"] = "test-token"
        try:
            app = create_app(store=self.store)
            client = TestClient(app)
            self.assertEqual(client.get("/api/agents").status_code, 401)
            self.assertEqual(client.get("/api/agents", headers={"X-ICLE-Token": "test-token"}).status_code, 200)
            self.assertEqual(client.get("/api/health").status_code, 200)
        finally:
            if previous is None:
                os.environ.pop("ICLE_TOKEN", None)
            else:
                os.environ["ICLE_TOKEN"] = previous

    def test_websocket_connects(self) -> None:
        # starlette TestClient + httpx 0.28 breaks WS; verify against a live server.
        import asyncio
        import socket
        import threading

        import uvicorn
        import websockets

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            async def probe() -> dict:
                for _ in range(50):
                    if server.started:
                        break
                    await asyncio.sleep(0.1)
                async with websockets.connect(f"ws://127.0.0.1:{port}/api/events") as ws:
                    return json.loads(await asyncio.wait_for(ws.recv(), timeout=5))

            message = asyncio.run(probe())
            self.assertEqual(message["type"], "connected")
        finally:
            server.should_exit = True
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
