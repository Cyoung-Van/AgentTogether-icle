"""UI-01..05: API tests (TestClient; same core functions as CLI)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from icle.api.app import create_app  # noqa: E402
from icle.episode import create_episode  # noqa: E402
from icle.judge import record_judgment, record_result_mark  # noqa: E402
from icle.replay import replay  # noqa: E402


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


def fake_executor(workspace: Path, prompt: str, timeout: float):
    (workspace / "out.txt").write_text("done", encoding="utf-8")
    return ("completed", "did it", "", 0)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        cls.episode = create_episode(
            store, session_id="s1", agent_revision=revision("hermes"),
            project_id="demo", project_path=project,
            user_request="fix the flaky csv parser",
        )
        cls.replay = replay(
            store, cls.episode["episode_id"],
            target_agent_revision=revision("kimi"), executor=fake_executor,
        )
        record_result_mark(store, episode_id=cls.episode["episode_id"], mark="accept", agent_id="kimi")
        cls.client = TestClient(create_app(store=store))

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["episodes"], 1)

    def test_agents(self) -> None:
        response = self.client.get("/api/agents")
        self.assertEqual(response.status_code, 200)
        agents = {a["agent_id"]: a for a in response.json()["agents"]}
        self.assertIn("kimi", agents)
        self.assertEqual(agents["kimi"]["replays"], 1)
        self.assertNotIn("rank", agents["kimi"])
        self.assertNotIn("score", agents["kimi"])
        self.assertEqual(agents["kimi"]["measurement"]["observed_axis_count"], 0)
        self.assertIn("no-pairwise-data", agents["kimi"]["flags"])

    def test_external_evaluation_quality_is_read_only(self) -> None:
        response = self.client.get("/api/agent-evaluations/quality")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["schema_version"], "icle-external-evaluation-quality/v0.1")
        self.assertIn("sources", body)
        self.assertIn("invalid_snapshot_files", body)

    def test_episode_list_and_filters(self) -> None:
        all_items = self.client.get("/api/episodes").json()
        self.assertEqual(all_items["total"], 1)
        self.assertEqual(all_items["episodes"][0]["replays"], 1)
        self.assertEqual(self.client.get("/api/episodes", params={"search": "csv"}).json()["total"], 1)
        self.assertEqual(self.client.get("/api/episodes", params={"search": "nomatch"}).json()["total"], 0)
        # Agent filtering includes replay targets, not only the source agent.
        self.assertEqual(self.client.get("/api/episodes", params={"agent": "kimi"}).json()["total"], 1)
        self.assertEqual(self.client.get("/api/episodes", params={"agent": "hermes"}).json()["total"], 1)

    def test_episode_detail_and_404(self) -> None:
        response = self.client.get(f"/api/episodes/{self.episode['episode_id']}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["episode"]["episode_id"], self.episode["episode_id"])
        self.assertEqual(len(body["replays"]), 1)
        self.assertEqual(body["replays"][0]["agent"], "kimi")
        missing = self.client.get("/api/episodes/ep-9999")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
