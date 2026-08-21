"""v0.4 skill 机制测试:无内置 adapter 的小众 agent 用 LLM 按 skill 解析会话。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.capture import CaptureError  # noqa: E402
from icle.skills import (  # noqa: E402
    build_prompt,
    capture_llm,
    find_skill,
    parse_llm_result,
    parse_skill_sources,
    scan_skill_files,
)

FAKE_SKILL = """# FakeAgent 会话捕获 skill(v0.1)

## SOURCE
{fake_root}

## GLOB
**/*.jsonl

## 已知信息
- 每行 {{"role": "...", "content": "..."}}
- 噪音: stats 行
"""


class FakeProvider:
    name = "fake"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str) -> str:
        return self.reply


class SkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "capture-store"
        self.store.mkdir()
        self.sessions = self.root / "fake-sessions"
        self.sessions.mkdir()
        (self.sessions / "ses-a.jsonl").write_text(
            "\n".join([
                json.dumps({"role": "user", "content": "你好"}),
                json.dumps({"role": "assistant", "content": "你好"}),
                json.dumps({"role": "stats", "content": "n/a"}),
            ]),
            encoding="utf-8",
        )
        self.skills_dir = self.root / "skills"
        self.skills_dir.mkdir()
        (self.skills_dir / "fakeagent.md").write_text(
            FAKE_SKILL.format(fake_root=self.sessions), encoding="utf-8"
        )

    def test_find_skill_and_parse_sources(self) -> None:
        skill = find_skill("fakeagent", self.skills_dir)
        self.assertIsNotNone(skill)
        sources = parse_skill_sources(skill.read_text(encoding="utf-8"))
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["glob"], "**/*.jsonl")
        files = scan_skill_files(sources)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "ses-a.jsonl")

    def test_parse_llm_result_validates(self) -> None:
        raw = json.dumps({
            "sessions": [{
                "session_id": "ses-a",
                "title": "t",
                "events": [
                    {"seq": 1, "kind": "user", "content": "你好", "ts": "t1"},
                    {"seq": 2, "kind": "assistant", "content": "你好", "ts": "t2"},
                ],
            }]
        })
        parsed = parse_llm_result(raw, "fakeagent")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["events"][0]["kind"], "user")
        self.assertIsNotNone(parsed[0]["events"][0]["content_sha256"])
        # 白名单外的 kind → 报错
        bad = raw.replace('"kind": "user"', '"kind": "alien"')
        with self.assertRaises(CaptureError):
            parse_llm_result(bad, "fakeagent")

    def test_capture_llm_end_to_end(self) -> None:
        reply = json.dumps({
            "sessions": [{
                "session_id": "ses-a",
                "title": "fake",
                "events": [
                    {"seq": 1, "kind": "user", "content": "你好", "ts": "t1"},
                    {"seq": 2, "kind": "assistant", "content": "你好", "ts": "t2"},
                ],
            }]
        })
        result = capture_llm(
            "fakeagent", self.store, FakeProvider(reply), skills_dir=self.skills_dir
        )
        self.assertEqual(result["sessions"][0]["agent_id"], "fakeagent")
        self.assertEqual(result["sessions"][0]["session_id"], "ses-a")
        events_path = self.store / "fakeagent" / "ses-a" / "events.jsonl"
        rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["kind"] for r in rows], ["user", "assistant"])
        # 幂等
        again = capture_llm(
            "fakeagent", self.store, FakeProvider(reply), skills_dir=self.skills_dir
        )
        self.assertEqual(again["sessions"][0]["new_events"], 0)

    def test_missing_skill_raises(self) -> None:
        with self.assertRaises(CaptureError):
            capture_llm("nobody", self.store, FakeProvider("{}"), skills_dir=self.skills_dir)

    def test_llm_garbage_raises(self) -> None:
        with self.assertRaises(CaptureError):
            capture_llm(
                "fakeagent", self.store, FakeProvider("not json at all"),
                skills_dir=self.skills_dir,
            )

    def test_build_prompt_contains_samples(self) -> None:
        prompt = build_prompt("fakeagent", "## SOURCE\nx\n## GLOB\n**/*.jsonl\n", [{"file": "a", "lines": ["line1"]}])
        self.assertIn("fakeagent", prompt)
        self.assertIn("line1", prompt)
        self.assertIn('"kind"', prompt)


if __name__ == "__main__":
    unittest.main()
