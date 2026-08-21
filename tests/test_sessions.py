"""v0.4 Sessions:捕获会话浏览 + 会话→任务(手动 / LLM 批量)。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.capture import _write_index, _write_session  # noqa: E402
from icle.sessionlib import (  # noqa: E402
    SessionError,
    create_episode_from_session,
    create_task_from_sessions,
    extract_tasks_from_sessions,
    list_sessions,
    preview_task_from_sessions,
    read_session,
)


class FakeProvider:
    name = "fake"
    model = "fake-model"
    revision = "fake-rev"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str) -> str:
        return self.reply


def _event(seq, kind, content, ts=None):
    return {
        "schema_version": "icle-session-event/v0.1",
        "session_id": "ses-1", "agent_id": "kimi", "seq": seq,
        "kind": kind, "ts": ts, "project": None, "content": content,
        "content_sha256": "x", "detail": {},
    }


class SessionLibTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.capture = self.root / "capture-store"
        self.capture.mkdir()
        self.episode_events = [_event(1, "user", "重构 Router 置信度"), _event(2, "assistant", "好的")]
        _write_session(self.capture, "kimi", "ses-1", self.episode_events, title="重构 Router")
        _write_index(self.capture, [{"agent_id": "kimi", "session_id": "ses-1", "new_events": 2}])

    def test_list_and_read(self) -> None:
        result = list_sessions(self.capture)
        self.assertIn("kimi", result["agents"])
        self.assertEqual(result["agents"]["kimi"][0]["session_id"], "ses-1")
        self.assertEqual(result["total_events"], 2)
        detail = read_session(self.capture, "kimi", "ses-1")
        self.assertEqual(detail["total"], 2)
        self.assertEqual(detail["events"][0]["content"], "重构 Router 置信度")
        with self.assertRaises(SessionError):
            read_session(self.capture, "kimi", "nope")

    def test_manual_create_task(self) -> None:
        episode = create_episode_from_session(
            self.store, self.capture, agent_id="kimi", session_id="ses-1",
            user_request="把这个会话整理成任务", project_id="icle", project_path="/tmp",
        )
        self.assertTrue(episode["episode_id"].startswith("ep-"))
        self.assertEqual(episode["source_session"], "ses-1")
        self.assertEqual(episode["source_agent_revision"]["agent_id"], "kimi")
        # 说明是保底项:留空 → 用会话首个 user 消息兜底,不报错
        fallback = create_episode_from_session(
            self.store, self.capture, agent_id="kimi", session_id="ses-1",
            user_request="", project_id="icle", project_path="/tmp",
        )
        self.assertIn("重构 Router 置信度", fallback["task_start"]["original_user_request"])

    def test_multi_session_task_preview_then_explicit_create(self) -> None:
        _write_session(
            self.capture, "codex", "ses-2",
            [
                {**_event(1, "user", "补充回归测试"), "agent_id": "codex", "session_id": "ses-2"},
                {**_event(2, "assistant", "完成"), "agent_id": "codex", "session_id": "ses-2"},
            ],
            title="补测试",
        )
        picks = [
            {"agent_id": "kimi", "session_id": "ses-1"},
            {"agent_id": "codex", "session_id": "ses-2"},
        ]
        preview = preview_task_from_sessions(self.capture, picks)
        self.assertEqual(preview["count"], 2)
        self.assertEqual(len(list(self.store.glob("tasks/*.json"))), 0)
        task = create_task_from_sessions(
            self.store, self.capture,
            picks=picks,
            title="合并两个会话",
            description="统一任务说明",
            project_id="icle",
            project_path="/tmp",
        )
        self.assertEqual(task["title"], "合并两个会话")
        self.assertEqual(task["description"], "统一任务说明")
        self.assertEqual(task["creation_source"], "captured_sessions:2")
        self.assertEqual(task["source_sessions"], picks)
        self.assertEqual(len(list(self.store.glob("tasks/*.json"))), 1)

    def test_llm_extract_tasks(self) -> None:
        reply = json.dumps({
            "schema_version": "icle-session-analysis/v0.1",
            "session_id": "ses-1",
            "status": "ok",
            "facts": [{"text": "用户要求重构 Router", "evidence": ["e1"]}],
            "inferences": [], "proposals": [],
            "confidence": {"boundary": 0.9, "intent": 0.8, "outcome": 0.5, "task_profile": 0.6},
            "tasks": [{
                "candidate_id": "c1",
                "boundaries": {"start_event": "e1", "end_event": "e2"},
                "title": "重构 Router 置信度", "goal": "重构 Router 置信度",
                "original_request": "重构 Router 置信度", "final_request": "重构 Router 置信度",
                "task_type": "CODING", "subtype": "refactor", "difficulty": "D2", "risk": "R1",
                "constraints": [], "success_criteria": ["测试通过"],
                "known_facts": [], "unknowns": [], "decisions": [], "assumptions": [],
                "attempts": [], "failures": [], "corrections": [], "artifacts": [],
                "outcome": {"status": "unknown", "summary": "captured"},
                "user_feedback": [], "evidence_refs": ["e1", "e2"],
                "confidence": {"boundary": 0.9, "intent": 0.8, "outcome": 0.5, "task_profile": 0.6},
            }],
        })
        result = extract_tasks_from_sessions(
            self.store, self.capture, FakeProvider(reply),
            picks=[{"agent_id": "kimi", "session_id": "ses-1", "description": "用户补充"}],
            project_id="icle", project_path="/tmp",
        )
        self.assertEqual(result["proposal_count"], 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["session_id"], "ses-1")
        self.assertEqual(
            proposal["analysis"]["tasks"][0]["boundaries"],
            {"start_event": "e1", "end_event": "e2"},
        )
        self.assertIn("用户补充", proposal["analysis"]["tasks"][0]["goal"])
        self.assertEqual(
            proposal["analysis"]["provenance"]["skill"]["name"],
            "icle-task-intelligence",
        )
        self.assertEqual(len(list(self.store.glob("episodes/*.json"))), 0)
        result2 = extract_tasks_from_sessions(
            self.store, self.capture, FakeProvider("garbage"),
            picks=[{"agent_id": "kimi", "session_id": "ses-1"}],
            project_id="icle", project_path="/tmp",
        )
        self.assertEqual(result2["proposal_count"], 0)
        self.assertEqual(len(result2["errors"]), 1)


if __name__ == "__main__":
    unittest.main()


class AnalyzeApiTests(unittest.TestCase):
    """POST /api/sessions/{agent}/{sid}/analyze:skill-driven,不落库。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.capture = self.root / "capture-store"
        self.capture.mkdir()
        _write_session(self.capture, "kimi", "ses-1",
                       [_event(1, "user", "重构 Router 置信度"), _event(2, "assistant", "好的")],
                       title="重构")
        _write_index(self.capture, [{"agent_id": "kimi", "session_id": "ses-1", "new_events": 2}])

    def _app(self, reply: str) -> tuple:
        import icle.api.sessions as api_sessions
        from fastapi.testclient import TestClient

        from icle.api.app import create_app

        app = create_app(store=str(self.store), capture_store=str(self.capture))
        fp = FakeProvider(reply)
        api_sessions.resolve_provider = lambda store, **kwargs: fp  # 注入 fake LLM
        return TestClient(app), fp

    def test_analyze_returns_proposal_not_persisted(self) -> None:
        from fastapi.testclient import TestClient  # noqa: F401
        reply = json.dumps({
            "schema_version": "icle-session-analysis/v0.1",
            "session_id": "ses-1", "status": "ok",
            "facts": [{"text": "用户要求重构 Router", "evidence": ["e1"]}],
            "inferences": [], "proposals": [],
            "confidence": {"boundary": 0.8, "intent": 0.7, "outcome": 0.8, "task_profile": 0.5},
            "tasks": [{
                "candidate_id": "c1",
                "boundaries": {"start_event": "e1", "end_event": "e2"},
                "title": "重构 Router", "goal": "提升置信度",
                "original_request": "重构 Router", "final_request": "重构 Router",
                "task_type": "CODING", "subtype": "refactor", "difficulty": "D3", "risk": "R1",
                "constraints": [], "success_criteria": [],
                "known_facts": [], "unknowns": [], "decisions": [], "assumptions": [],
                "attempts": [], "failures": [], "corrections": [], "artifacts": [],
                "outcome": {"status": "completed", "summary": "s"},
                "user_feedback": [], "evidence_refs": ["e1"],
                "confidence": {"boundary": 0.8, "intent": 0.7, "outcome": 0.8, "task_profile": 0.5},
            }],
        })
        client, fp = self._app(reply)
        r = client.post("/api/sessions/kimi/ses-1/analyze", json={"lang": "zh"})
        self.assertEqual(r.status_code, 200)
        analysis = r.json()["analysis"]
        self.assertEqual(analysis["status"], "ok")
        self.assertEqual(analysis["tasks"][0]["title"], "重构 Router")
        self.assertEqual(analysis["provenance"]["skill"]["name"], "icle-task-intelligence")
        self.assertEqual(analysis["provenance"]["language"], "zh")
        # 不落库:experience-store 无 episode
        self.assertEqual(len(list(self.store.glob("episodes/*.json"))), 0)

    def test_multi_session_preview_is_no_write_and_confirm_creates_one_task(self) -> None:
        from fastapi.testclient import TestClient
        from icle.api.app import create_app

        _write_session(
            self.capture, "codex", "ses-2",
            [
                {**_event(1, "user", "补充验证"), "agent_id": "codex", "session_id": "ses-2"},
                {**_event(2, "assistant", "ok"), "agent_id": "codex", "session_id": "ses-2"},
            ],
            title="验证",
        )
        client = TestClient(create_app(store=str(self.store), capture_store=str(self.capture)))
        picks = [
            {"agent_id": "kimi", "session_id": "ses-1"},
            {"agent_id": "codex", "session_id": "ses-2"},
        ]
        preview = client.post("/api/sessions/task-preview", json={"sessions": picks})
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["preview"]["count"], 2)
        self.assertEqual(len(list(self.store.glob("tasks/*.json"))), 0)
        created = client.post(
            "/api/sessions/task",
            json={
                "sessions": picks,
                "title": "确认后的任务",
                "description": "合并来源",
                "project_id": "icle",
                "project_path": "/tmp",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["task"]["source_sessions"], picks)
        self.assertEqual(len(list(self.store.glob("tasks/*.json"))), 1)

    def test_manual_session_button_creates_task_not_episode(self) -> None:
        from fastapi.testclient import TestClient

        from icle.api.app import create_app

        client = TestClient(create_app(store=str(self.store), capture_store=str(self.capture)))
        response = client.post(
            "/api/sessions/kimi/ses-1/task",
            json={"request": "整理为工作任务", "project_id": "icle", "project_path": "/tmp"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("task", response.json())
        self.assertNotIn("episode", response.json())
        self.assertEqual(response.json()["task"]["status"], "draft")
        self.assertEqual(len(list(self.store.glob("episodes/*.json"))), 0)

    def test_analyze_without_llm_returns_400(self) -> None:
        """未配 LLM provider → 400 明确提示(不走到会话读取)。"""
        import icle.api.sessions as api_sessions
        from fastapi.testclient import TestClient

        from icle.api.app import create_app

        original = api_sessions.resolve_provider
        try:
            class NoLLM:
                name = "none"
            api_sessions.resolve_provider = lambda store, **kwargs: NoLLM()
            client = TestClient(create_app(store=str(self.store), capture_store=str(self.capture)))
            r = client.post("/api/sessions/kimi/ses-1/analyze", json={})
            self.assertEqual(r.status_code, 400)
            self.assertIn("LLM provider", r.json()["detail"])
        finally:
            api_sessions.resolve_provider = original


if __name__ == "__main__":
    unittest.main()


class CreateTaskFromProposalTests(unittest.TestCase):
    """接受 analyze Proposal → 创建 Task(带 TaskProfile)(§31)。"""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"

    def test_creates_task_with_profile(self) -> None:
        from icle.sessionlib import create_task_from_proposal

        proposal_task = {
            "candidate_id": "c1", "title": "重构 Router", "goal": "提升置信度",
            "original_request": "重构", "final_request": "重构 Router",
            "task_type": "CODING", "subtype": "refactor", "difficulty": "D3", "risk": "R1",
            "boundaries": {"start_event": "e1", "end_event": "e2"},
            "evidence_refs": ["e1"], "outcome": {"status": "completed", "summary": "s"},
        }
        task = create_task_from_proposal(
            self.store, proposal_task=proposal_task,
            provenance={"provider": "fake", "model": "m", "prompt_sha256": "abc"},
            project_id="icle", project_path="/tmp",
        )
        self.assertTrue(task["task_id"].startswith("t-"))
        self.assertEqual(task["status"], "profiled")
        profile = task["profile"]
        self.assertEqual(profile["primary_type"], "CODING")
        self.assertEqual(profile["subtype"], "refactor")
        self.assertEqual(profile["difficulty"], "D3")
        self.assertEqual(profile["risk"], "R1")
        self.assertEqual(profile["source"], "llm")
        self.assertEqual(profile["provenance"]["model"], "m")
        # 文件落盘
        self.assertTrue((self.store / "tasks" / f"{task['task_id']}.json").is_file())

    def test_subtype_mismatch_cleared(self) -> None:
        from icle.sessionlib import create_task_from_proposal

        proposal_task = {"title": "t", "goal": "g", "task_type": "CODING",
                         "subtype": "search", "difficulty": "D2", "risk": "R1"}  # search 不属于 CODING 子类
        task = create_task_from_proposal(
            self.store, proposal_task=proposal_task, provenance={}, project_id="p", project_path="/tmp")
        self.assertEqual(task["profile"]["subtype"], "")


if __name__ == "__main__":
    unittest.main()
