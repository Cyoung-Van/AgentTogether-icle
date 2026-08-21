"""P5: IntelligenceProvider tests (fake providers; real kimi not required)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.intelligence import (  # noqa: E402
    IntelligenceError,
    NoneProvider,
    analyze_failure,
    judge_pair,
    propose_tasks,
    similar_episodes,
)


class FakeProvider:
    name = "fake"
    model = "fake-1"
    revision = "rev-fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


EVENTS = [
    {"seq": 1, "kind": "user", "content": "fix the bug"},
    {"seq": 2, "kind": "assistant", "content": "looking"},
    {"seq": 3, "kind": "user", "content": "now write docs"},
    {"seq": 4, "kind": "assistant", "content": "writing"},
]


def _analysis_reply(*, start="e1", end="e2", extra_tasks=None) -> str:
    task = {
        "candidate_id": "c1",
        "boundaries": {"start_event": start, "end_event": end},
        "title": "fix bug", "goal": "fix the bug",
        "original_request": "fix the bug", "final_request": "fix the bug",
        "task_type": "CODING", "subtype": "debugging", "difficulty": "D2", "risk": "R1",
        "constraints": [], "success_criteria": ["tests pass"],
        "known_facts": [], "unknowns": [], "decisions": [], "assumptions": [],
        "attempts": [], "failures": [], "corrections": [], "artifacts": [],
        "outcome": {"status": "unknown", "summary": "in progress"},
        "user_feedback": [], "evidence_refs": [start, end],
        "confidence": {"boundary": 0.8, "intent": 0.7, "outcome": 0.5, "task_profile": 0.6},
    }
    return json.dumps({
        "schema_version": "icle-session-analysis/v0.1",
        "status": "ok",
        "facts": [{"text": "user asked to fix a bug", "evidence": [start]}],
        "inferences": [], "proposals": [],
        "confidence": {"boundary": 0.8, "intent": 0.7, "outcome": 0.5, "task_profile": 0.6},
        "tasks": extra_tasks if extra_tasks is not None else [task],
    })


class TaskExtractorTests(unittest.TestCase):
    def test_valid_proposal_with_provenance(self) -> None:
        provider = FakeProvider(_analysis_reply())
        result = propose_tasks(provider, EVENTS)
        self.assertEqual(result["component"], "task_extractor")
        self.assertEqual(result["judge_model"], "fake-1")
        self.assertTrue(result["prompt_sha256"].startswith("sha256:"))
        proposal = result["output"]["tasks"][0]
        self.assertEqual(proposal["type"], "debugging")
        self.assertEqual(proposal["from_seq"], 1)
        self.assertEqual(proposal["to_seq"], 2)
        self.assertEqual(result["analysis"]["provenance"]["skill"]["name"], "icle-task-intelligence")

    def test_out_of_range_proposal_rejected(self) -> None:
        provider = FakeProvider(_analysis_reply(start="e1", end="e99"))
        with self.assertRaisesRegex(IntelligenceError, "validation failed"):
            propose_tasks(provider, EVENTS)

    def test_no_json_and_no_tasks_rejected(self) -> None:
        with self.assertRaisesRegex(IntelligenceError, "no JSON object"):
            propose_tasks(FakeProvider("no json here"), EVENTS)
        with self.assertRaisesRegex(IntelligenceError, "validation failed|no tasks"):
            propose_tasks(FakeProvider('{"tasks": []}'), EVENTS)

    def test_none_provider_fails_loudly(self) -> None:
        with self.assertRaisesRegex(IntelligenceError, "unavailable"):
            NoneProvider().complete("hi")


class PairwiseJudgeTests(unittest.TestCase):
    def test_verdict_with_full_provenance(self) -> None:
        provider = FakeProvider(
            '{"verdict": "prefer_b", "reason": "B handles edge cases", "confidence": 0.7, "reason_tags": ["correctness"]}'
        )
        result = judge_pair(provider, task="fix bug", result_a="A did x", result_b="B did y")
        self.assertEqual(result["component"], "pairwise_judge")
        self.assertEqual(result["output"]["verdict"], "prefer_b")
        self.assertEqual(result["output"]["reason_tags"], ["correctness"])
        self.assertTrue(result["prompt_sha256"].startswith("sha256:"))

    def test_invalid_verdict_and_tags_rejected(self) -> None:
        with self.assertRaisesRegex(IntelligenceError, "verdict"):
            judge_pair(FakeProvider('{"verdict": "love_both"}'), task="t", result_a="a", result_b="b")
        with self.assertRaisesRegex(IntelligenceError, "reason_tags"):
            judge_pair(
                FakeProvider('{"verdict": "tie", "reason": "same", "reason_tags": ["vibes"]}'),
                task="t", result_a="a", result_b="b",
            )


class TaskSimilarityTests(unittest.TestCase):
    def _episode(self, eid: str, project: str, request: str) -> dict:
        return {
            "episode_id": eid,
            "project_id": project,
            "task_start": {"original_user_request": request},
        }

    def test_token_overlap_and_project_bonus(self) -> None:
        episodes = [
            self._episode("ep-1", "demo", "fix the csv parsing bug"),
            self._episode("ep-2", "demo", "write a blog post"),
            self._episode("ep-3", "other", "fix the csv export issue"),
        ]
        results = similar_episodes(episodes, "csv parsing fix", project_id="demo")
        self.assertEqual(results[0]["episode_id"], "ep-1")
        self.assertGreater(results[0]["score"], results[1]["score"])

    def test_empty_query_is_safe(self) -> None:
        results = similar_episodes([self._episode("e", "p", "x")], "")
        self.assertEqual(results[0]["score"], 0.0)


class FailureAnalyzerTests(unittest.TestCase):
    def test_rule_categories(self) -> None:
        result = analyze_failure(status="failed", stderr="error: authorization grant is invalid")
        self.assertIn("auth", result["categories"])
        self.assertIn("re-login", result["likely_user_action"])
        result = analyze_failure(status="failed", stderr="FAILED (failures=2)")
        self.assertIn("test_failure", result["categories"])

    def test_non_failure(self) -> None:
        result = analyze_failure(status="completed", stderr="")
        self.assertEqual(result["categories"], ["not_a_failure"])


if __name__ == "__main__":
    unittest.main()


class LanguageInstructionTests(unittest.TestCase):
    """LLM 语言前置条件:zh 界面要求中文 prose,en 要求英文(枚举值不动)。"""

    def _provider(self, reply: str) -> FakeProvider:
        return FakeProvider(reply)

    def test_profilers_prompt_carries_language(self) -> None:
        from icle.intelligence import propose_task_profile

        profile = json.dumps({
            "primary_type": "CODING", "subtype": "implementation",
            "difficulty": "D2", "risk": "R1", "context_requirement": "LOW",
            "tool_requirement": ["filesystem"], "estimated_duration": "short",
            "decomposition": "not_recommended", "review": "not_recommended",
            "reason": "中文理由",
        })
        zh = self._provider(profile)
        propose_task_profile(zh, title="t", description="d", language="zh")
        self.assertIn("Chinese", zh.prompts[0])
        self.assertIn("Simplified Chinese", zh.prompts[0])
        self.assertIn("analyze-task", zh.prompts[0])
        en = self._provider(profile)
        propose_task_profile(en, title="t", description="d", language="en")
        self.assertIn("English", en.prompts[0])
        self.assertIn("untranslated", zh.prompts[0].lower())

    def test_extractor_and_rating_carry_language(self) -> None:
        from icle.intelligence import propose_rating, propose_tasks

        zh_tasks = self._provider(_analysis_reply())
        propose_tasks(zh_tasks, EVENTS, language="zh")
        self.assertIn("Simplified Chinese", zh_tasks.prompts[0])
        self.assertIn("analyze-session", zh_tasks.prompts[0])
        rating = json.dumps({
            "dimensions": {
                "requirement_fit": 4, "correctness": 4, "efficiency": 3,
                "autonomy": 4, "maintainability": 3,
            },
            "overall_preference": 4, "would_use_again": "yes", "comment": "可用",
        })
        zh_rate = self._provider(rating)
        propose_rating(zh_rate, task="t", result="r", language="zh")
        self.assertIn("Simplified Chinese", zh_rate.prompts[0])
        self.assertIn("judge-result", zh_rate.prompts[0])


if __name__ == "__main__":
    unittest.main()
