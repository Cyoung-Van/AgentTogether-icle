"""v0.4 Skill 自我评估(§30):accept/edit/reject 记录与接受率统计。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.skill_feedback import (  # noqa: E402
    SkillFeedbackError,
    feedback_stats,
    record_feedback,
)


class SkillFeedbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp())

    def test_record_and_stats(self) -> None:
        record_feedback(self.store, skill="icle-task-intelligence", mode="analyze-session",
                        action="accept", session_id="s1", candidate_id="c1")
        record_feedback(self.store, skill="icle-task-intelligence", mode="analyze-session",
                        action="edit", session_id="s1", candidate_id="c2")
        record_feedback(self.store, skill="icle-task-intelligence", mode="analyze-session",
                        action="reject", session_id="s1", candidate_id="c3")
        record_feedback(self.store, skill="icle-task-intelligence", mode="plan-task",
                        action="accept", session_id="s2")
        stats = feedback_stats(self.store)
        self.assertEqual(len(stats["stats"]), 2)
        analyze = next(s for s in stats["stats"] if s["mode"] == "analyze-session")
        self.assertEqual(analyze["accept"], 1)
        self.assertEqual(analyze["edit"], 1)
        self.assertEqual(analyze["reject"], 1)
        self.assertEqual(analyze["total"], 3)
        self.assertAlmostEqual(analyze["accept_rate"], round(1 / 3, 3), places=3)
        self.assertAlmostEqual(analyze["edited_rate"], round(2 / 3, 3), places=3)

    def test_append_only_durable(self) -> None:
        record_feedback(self.store, skill="s", mode="m", action="accept", detail="x")
        lines = (self.store / "skill-feedback.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        # 重复记录追加,不覆盖
        record_feedback(self.store, skill="s", mode="m", action="reject")
        lines = (self.store / "skill-feedback.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

    def test_unknown_action_rejected(self) -> None:
        with self.assertRaises(SkillFeedbackError):
            record_feedback(self.store, skill="s", mode="m", action="nonsense")


if __name__ == "__main__":
    unittest.main()
