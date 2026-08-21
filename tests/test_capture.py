"""P1: session capture tests (synthetic fixtures, never real homes)."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.capture import CaptureError, capture_hermes, capture_kimi, redact  # noqa: E402


def make_hermes_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sessions (id TEXT, model TEXT, cwd TEXT, title TEXT, started_at REAL)"
    )
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
        "content TEXT, tool_calls TEXT, tool_name TEXT, timestamp REAL, finish_reason TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('s1', 'gpt-5.6', '/proj/a', 'fix bug', 1.0)"
    )
    conn.execute("INSERT INTO messages VALUES (1, 's1', 'user', 'fix the flaky test', NULL, NULL, 1.1, NULL)")
    conn.execute("INSERT INTO messages VALUES (2, 's1', 'assistant', 'looking at it', NULL, NULL, 1.2, 'stop')")
    conn.execute(
        "INSERT INTO messages VALUES (3, 's1', 'assistant', 'running tests', "
        "'[{\"name\": \"bash\"}]', 'bash', 1.3, NULL)"
    )
    conn.execute(
        "INSERT INTO messages VALUES (4, 's1', 'assistant', 'key is sk-live123456789', NULL, NULL, 1.4, 'stop')"
    )
    conn.commit()
    conn.close()


def make_kimi_session(root: Path) -> None:
    session_dir = root / "wd_proj_abc" / "session_111"
    (session_dir / "agents" / "main").mkdir(parents=True)
    (session_dir / "state.json").write_text(json.dumps({"title": "demo", "createdAt": "2026-08-01"}))
    (session_dir / "agents" / "main" / "wire.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "metadata", "created_at": 1}),
                json.dumps({"type": "turn.prompt", "time": 2, "prompt": "do the thing"}),
                json.dumps({"type": "context.append_loop_event", "time": 3, "text": "working"}),
                json.dumps({"type": "usage.record", "time": 4, "tokens": 100}),
                json.dumps({"type": "turn.cancel", "time": 5, "reason": "user"}),
            ]
        )
        + "\n"
    )
    (root.parent / "session_index.jsonl").write_text(
        json.dumps({"sessionId": "session_111", "workDir": "/proj/demo"}) + "\n"
    )


class RedactTests(unittest.TestCase):
    def test_authorization_header_redacted(self) -> None:
        text = "'headers': {'Authorization': 'Bearer sk-c4212abcdef12345', 'x': 1}"
        out = redact(text)
        self.assertNotIn("sk-c4212abcdef12345", out)
        self.assertIn("[REDACTED]", out)

    def test_sk_and_api_key_redacted(self) -> None:
        self.assertNotIn("sk-live123456789", redact("key is sk-live123456789"))
        out = redact('api_key = "abcdef1234567890"')
        self.assertNotIn("abcdef1234567890", out)


class HermesCaptureTests(unittest.TestCase):
    def test_capture_normalizes_and_redacts(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "state.db"
        make_hermes_db(db)
        index = capture_hermes(db, root / "store")
        self.assertEqual(index["sessions"][0]["new_events"], 4)
        events = [
            json.loads(line)
            for line in (root / "store" / "hermes" / "s1" / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual([e["kind"] for e in events], ["user", "assistant", "tool_call", "assistant"])
        self.assertEqual(events[0]["project"], "/proj/a")
        self.assertNotIn("sk-live123456789", events[3]["content"])  # redacted

    def test_recapture_is_idempotent(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = root / "state.db"
        make_hermes_db(db)
        capture_hermes(db, root / "store")
        second = capture_hermes(db, root / "store")
        self.assertEqual(second["sessions"][0]["new_events"], 0)
        self.assertEqual(second["sessions"][0]["total_events"], 4)

    def test_missing_db_rejected(self) -> None:
        with self.assertRaises(CaptureError):
            capture_hermes("/nonexistent/state.db", Path(tempfile.mkdtemp()))


class KimiCaptureTests(unittest.TestCase):
    def test_capture_maps_event_types(self) -> None:
        root = Path(tempfile.mkdtemp())
        sessions = root / "sessions"
        sessions.mkdir()
        make_kimi_session(sessions)
        index = capture_kimi(sessions, root / "store")
        self.assertEqual(index["sessions"][0]["new_events"], 5)
        events = [
            json.loads(line)
            for line in (root / "store" / "kimi" / "session_111" / "events.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            [e["kind"] for e in events],
            ["meta", "user", "assistant", "usage", "failure"],
        )
        self.assertEqual(events[1]["project"], "/proj/demo")

    def test_recapture_only_appends_new_lines(self) -> None:
        root = Path(tempfile.mkdtemp())
        sessions = root / "sessions"
        sessions.mkdir()
        make_kimi_session(sessions)
        capture_kimi(sessions, root / "store")
        wire = sessions / "wd_proj_abc" / "session_111" / "agents" / "main" / "wire.jsonl"
        with wire.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "turn.prompt", "time": 6, "prompt": "more"}) + "\n")
        second = capture_kimi(sessions, root / "store")
        self.assertEqual(second["sessions"][0]["new_events"], 1)
        self.assertEqual(second["sessions"][0]["total_events"], 6)

    def test_cli_end_to_end(self) -> None:
        import subprocess

        root = Path(tempfile.mkdtemp())
        db = root / "state.db"
        make_hermes_db(db)
        completed = subprocess.run(
            [
                sys.executable, "-m", "icle", "capture",
                "--agent", "hermes", "--source", str(db), "--store", str(root / "store"),
            ],
            capture_output=True, text=True, check=False,
            env={"PYTHONPATH": str(PROJECT_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("4 new event(s)", completed.stdout)


if __name__ == "__main__":
    unittest.main()


class ProviderBridgeTests(unittest.TestCase):
    def test_resolve_falls_back_to_providers_store(self) -> None:
        from icle.settings import resolve_provider
        from icle.provider import save_provider

        store = Path(tempfile.mkdtemp())
        save_provider(
            store,
            {
                "schema_version": "icle-provider-config/v0.1",
                "provider_id": "p-test",
                "display_name": "DeepSeek",
                "type": "openai-compatible",
                "base_url": "https://api.deepseek.com/v1",
                "secret_ref": "store",
                "models": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}],
                "roles": ["general"],
                "status": "connected",
                "selected_model": "deepseek-v4-flash",
            },
            api_key="sk-test-key-1234567890",
        )
        provider = resolve_provider(store)
        self.assertEqual(provider.name, "openai")
        self.assertEqual(provider.model, "deepseek-v4-flash")
        self.assertEqual(provider.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(provider.api_key, "sk-test-key-1234567890")

    def test_resolve_none_when_nothing_configured(self) -> None:
        from icle.settings import resolve_provider

        provider = resolve_provider(Path(tempfile.mkdtemp()))
        self.assertEqual(provider.name, "none")
