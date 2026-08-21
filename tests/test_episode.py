"""P2: TaskEpisode + snapshot tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.episode import (  # noqa: E402
    EpisodeError,
    create_episode,
    list_episodes,
    show_episode,
    task_start_snapshot,
)


def revision(agent: str = "hermes") -> dict:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent,
        "revision_id": "rev-1",
        "model": "m",
        "cli": "c",
        "provider": "p",
        "persona_sha256": None,
        "memory_sha256": None,
        "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": "2026-08-14T00:00:00Z",
    }


def make_git_project(path: Path) -> None:
    (path / "file.txt").write_text("hello\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(path)}
    for key, value in (
        ("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        env[key] = value
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, env=env, check=True)


def make_capture(store: Path, agent: str, session: str, seqs: int = 5) -> None:
    directory = store / agent / session
    directory.mkdir(parents=True)
    (directory / "events.jsonl").write_text(
        "".join(
            json.dumps({"seq": i, "kind": "user", "content": f"line {i}"}) + "\n"
            for i in range(1, seqs + 1)
        ),
        encoding="utf-8",
    )


class SnapshotTests(unittest.TestCase):
    def test_git_project_uses_revision(self) -> None:
        project = Path(tempfile.mkdtemp()) / "proj"
        project.mkdir()
        make_git_project(project)
        snapshot = task_start_snapshot(project)
        self.assertIn("git_revision", snapshot)
        self.assertFalse(snapshot["git_dirty"])
        (project / "file.txt").write_text("changed\n", encoding="utf-8")
        self.assertTrue(task_start_snapshot(project)["git_dirty"])

    def test_non_git_project_uses_workspace_hash(self) -> None:
        project = Path(tempfile.mkdtemp()) / "proj"
        project.mkdir()
        (project / "a.txt").write_text("a", encoding="utf-8")
        first = task_start_snapshot(project)
        self.assertIn("workspace_sha256", first)
        self.assertEqual(first["workspace_sha256"], task_start_snapshot(project)["workspace_sha256"])
        (project / "a.txt").write_text("b", encoding="utf-8")
        self.assertNotEqual(
            first["workspace_sha256"], task_start_snapshot(project)["workspace_sha256"]
        )

    def test_missing_project_rejected(self) -> None:
        with self.assertRaises(EpisodeError):
            task_start_snapshot("/nonexistent")


class EpisodeStoreTests(unittest.TestCase):
    def test_create_list_show_roundtrip(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        (project / "x").write_text("x", encoding="utf-8")
        capture = root / "capture"
        make_capture(capture, "hermes", "s-1")
        episode = create_episode(
            root / "store",
            session_id="s-1",
            agent_revision=revision(),
            project_id="demo",
            project_path=project,
            user_request="fix the flaky test",
            from_seq=2,
            to_seq=4,
            capture_store=capture,
        )
        self.assertEqual(episode["episode_id"], "ep-0001")
        self.assertEqual(episode["task_start"]["turn_range"], {"from_seq": 2, "to_seq": 4})
        listing = list_episodes(root / "store")
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["agent"], "hermes")
        shown = show_episode(root / "store", "ep-0001")
        self.assertEqual(shown["source_session"], "s-1")

    def test_turn_range_bounds_verified(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        capture = root / "capture"
        make_capture(capture, "hermes", "s-1")
        with self.assertRaisesRegex(EpisodeError, "outside captured session bounds"):
            create_episode(
                root / "store",
                session_id="s-1",
                agent_revision=revision(),
                project_id="demo",
                project_path=project,
                user_request="x",
                from_seq=2,
                to_seq=99,
                capture_store=capture,
            )
        with self.assertRaisesRegex(EpisodeError, "from_seq <= to_seq"):
            create_episode(
                root / "store",
                session_id="s-1",
                agent_revision=revision(),
                project_id="demo",
                project_path=project,
                user_request="x",
                from_seq=4,
                to_seq=2,
                capture_store=capture,
            )

    def test_unknown_episode_rejected(self) -> None:
        with self.assertRaises(EpisodeError):
            show_episode(Path(tempfile.mkdtemp()), "ep-9999")


if __name__ == "__main__":
    unittest.main()
