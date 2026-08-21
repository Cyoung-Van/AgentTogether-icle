"""v0.4 Session Capture adapters: claude/codex JSONL via the generic framework."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
import sqlite3

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.capture import (  # noqa: E402
    CaptureError,
    capture_claude,
    capture_codex,
    capture_opencode,
    capture_pi,
)


class CaptureAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "capture-store"
        self.store.mkdir()

    def test_capture_claude(self) -> None:
        projects = self.root / "claude-projects"
        session_file = projects / "-Users-funcyoung" / "36be685f-0133.jsonl"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(
            "\n".join([
                # 真实 claude 事件结构:内容在 message.content(字符串/数组)
                json.dumps({"type": "user", "timestamp": "2026-08-08T05:48:12Z",
                            "message": {"role": "user", "content": "你好"}}),
                json.dumps({"type": "assistant", "timestamp": "2026-08-08T05:48:13Z",
                            "message": {"role": "assistant", "content": [
                                {"type": "thinking", "thinking": "思考过程"},
                                {"type": "text", "text": "你好，有什么可以帮你"},
                            ]}}),
                json.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": "t", "content": "noise"}),
                json.dumps({"type": "thinking", "timestamp": "t", "content": "noise2"}),
            ]),
            encoding="utf-8",
        )
        result = capture_claude(projects, self.store)
        self.assertEqual(result["sessions"][0]["agent_id"], "claude")
        self.assertEqual(result["sessions"][0]["session_id"], "36be685f-0133")
        self.assertEqual(result["sessions"][0]["new_events"], 2)  # noise skipped
        events_path = self.store / "claude" / "36be685f-0133" / "events.jsonl"
        kinds = [json.loads(line)["kind"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        contents = [json.loads(line)["content"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds, ["user", "assistant"])
        self.assertEqual(contents, ["你好", "你好，有什么可以帮你"])  # 数组只取 text,跳过 thinking
        # 幂等:重复捕获 0 新增
        again = capture_claude(projects, self.store)
        self.assertEqual(again["sessions"][0]["new_events"], 0)

    def test_capture_codex(self) -> None:
        sessions = self.root / "codex-sessions"
        session_file = sessions / "2026" / "07" / "02" / "rollout-abc.jsonl"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(
            "\n".join([
                json.dumps({"id": "x", "thread_name": "检查数据迁移"}),
                json.dumps({"timestamp": "2026-07-02T05:16:42Z", "type": "response_item",
                            "payload": {"type": "message", "role": "user",
                                        "content": [{"type": "input_text", "text": "帮我检查迁移"}]}}),
                json.dumps({"timestamp": "2026-07-02T05:16:43Z", "type": "response_item",
                            "payload": {"type": "message", "role": "assistant",
                                        "content": [{"type": "input_text", "text": "好的"}]}}),
                json.dumps({"timestamp": "t", "type": "event_msg", "payload": {"type": "agent_message"}}),
                json.dumps({"timestamp": "t", "type": "token_count"}),
            ]),
            encoding="utf-8",
        )
        result = capture_codex(sessions, self.store)
        self.assertEqual(result["sessions"][0]["agent_id"], "codex")
        self.assertEqual(result["sessions"][0]["session_id"], "rollout-abc")
        self.assertEqual(result["sessions"][0]["title"], "检查数据迁移")
        self.assertEqual(result["sessions"][0]["new_events"], 2)  # noise skipped
        events_path = self.store / "codex" / "rollout-abc" / "events.jsonl"
        contents = [json.loads(line)["content"] for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertIn("帮我检查迁移", contents)

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(CaptureError):
            capture_claude(self.root / "nope", self.store)
        with self.assertRaises(CaptureError):
            capture_codex(self.root / "nope", self.store)


if __name__ == "__main__":
    unittest.main()


class OpenCodePiAdapterTests(unittest.TestCase):
    """opencode(SQLite)与 pi(JSONL)adapter:格式来自 GitHub 调研 + 本机真实数据验证。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "capture-store"
        self.store.mkdir()

    def test_capture_opencode_sqlite(self) -> None:
        db = self.root / "opencode.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT)")
        conn.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)")
        conn.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT)")
        conn.execute("INSERT INTO session VALUES ('ses_1','重构 Router','/proj')")
        conn.execute(
            "INSERT INTO message VALUES ('m1','ses_1',100,'{\"role\":\"user\",\"time\":{\"created\":100}}')")
        conn.execute(
            "INSERT INTO message VALUES ('m2','ses_1',101,'{\"role\":\"assistant\",\"time\":{\"created\":101}}')")
        conn.execute("INSERT INTO part VALUES ('p1','m1','{\"type\":\"text\",\"text\":\"帮我改\"}')")
        conn.execute("INSERT INTO part VALUES ('p2','m2','{\"type\":\"text\",\"text\":\"好的\"}')")
        conn.execute("INSERT INTO part VALUES ('p3','m2','{\"type\":\"step-start\"}')")
        conn.commit()
        conn.close()

        result = capture_opencode(db, self.store)
        self.assertEqual(result["sessions"][0]["agent_id"], "opencode")
        self.assertEqual(result["sessions"][0]["session_id"], "ses_1")
        self.assertEqual(result["sessions"][0]["title"], "重构 Router")
        self.assertEqual(result["sessions"][0]["new_events"], 2)  # step-start 无 role 跳过
        events_path = self.store / "opencode" / "ses_1" / "events.jsonl"
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["user", "assistant"])
        self.assertEqual(rows[0]["content"], "帮我改")
        self.assertEqual(rows[0]["project"], "/proj")
        # 幂等
        again = capture_opencode(db, self.store)
        self.assertEqual(again["sessions"][0]["new_events"], 0)

    def test_capture_claude_thinking_fallback(self) -> None:
        """纯 thinking 轮次(无 text)也应提取为自然语言,而不是整行 JSON。"""
        projects = self.root / "claude-thinking"
        session_file = projects / "-Users-funcyoung" / "sess-x.jsonl"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(
            json.dumps({"type": "assistant", "timestamp": "t",
                        "message": {"role": "assistant", "content": [
                            {"type": "thinking", "thinking": "我先分析一下需求"},
                        ]}}),
            encoding="utf-8",
        )
        result = capture_claude(projects, self.store)
        events_path = self.store / "claude" / "sess-x" / "events.jsonl"
        row = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["content"], "我先分析一下需求")

    def test_capture_pi_jsonl(self) -> None:
        sessions = self.root / "pi-sessions" / "--proj--"
        session_file = sessions / "2026-08-10T00-00-00Z_uuid123.jsonl"
        session_file.parent.mkdir(parents=True)
        session_file.write_text(
            "\n".join([
                json.dumps({"type": "session", "id": "uuid123", "cwd": "/proj"}),
                json.dumps({"type": "message", "id": "a", "timestamp": "2026-08-10T00:00:01Z",
                            "message": {"role": "user", "content": [{"type": "text", "text": "查看配置"}]}}),
                json.dumps({"type": "message", "id": "b", "timestamp": "2026-08-10T00:00:02Z",
                            "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "x"},
                                                                         {"type": "text", "text": "好的"}]}}),
                json.dumps({"type": "toolCall", "id": "c", "timestamp": "t"}),
            ]),
            encoding="utf-8",
        )
        result = capture_pi(sessions, self.store)
        self.assertEqual(result["sessions"][0]["agent_id"], "pi")
        self.assertEqual(result["sessions"][0]["session_id"], "uuid123")
        self.assertEqual(result["sessions"][0]["new_events"], 2)  # session/toolCall 跳过
        events_path = self.store / "pi" / "uuid123" / "events.jsonl"
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["content"], "查看配置")
        self.assertEqual(rows[1]["content"], "好的")  # 只取 text part,跳过 thinking


if __name__ == "__main__":
    unittest.main()
