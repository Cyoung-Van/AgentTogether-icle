"""v0.4 icle-task-intelligence skill: analyze-session(Skill v0.1)测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.intelligence import IntelligenceError, analyze_session  # noqa: E402
from icle.skills import load_skill, run_script  # noqa: E402


class FakeProvider:
    name = "fake"
    model = "m"
    revision = "r"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


EVENTS = [
    {"session_id": "s1", "seq": 1, "kind": "user", "content": "给 Router 加置信度", "ts": "2026-08-08T00:00:00Z"},
    {"session_id": "s1", "seq": 2, "kind": "assistant", "content": "好的", "ts": "2026-08-08T00:00:05Z"},
    {"session_id": "s1", "seq": 3, "kind": "meta", "content": "", "ts": "2026-08-08T00:00:06Z"},  # 噪音
    {"session_id": "s1", "seq": 4, "kind": "user", "content": "测试失败", "ts": "2026-08-08T00:10:00Z"},  # 时间间隔边界
    {"session_id": "s1", "seq": 5, "kind": "assistant", "content": "修好了", "ts": "2026-08-08T00:11:00Z"},
]


def valid_reply(tasks: list | None = None) -> str:
    tasks = tasks if tasks is not None else [{
        "candidate_id": "c1",
        "boundaries": {"start_event": "e1", "end_event": "e5"},
        "title": "加置信度", "goal": "g",
        "original_request": "加置信度", "final_request": "修测试",
        "task_type": "CODING", "subtype": "refactor", "difficulty": "D2", "risk": "R1",
        "constraints": [], "success_criteria": ["测试通过"],
        "known_facts": [], "unknowns": [], "decisions": [], "assumptions": [],
        "attempts": [], "failures": ["测试失败"], "corrections": [], "artifacts": [],
        "outcome": {"status": "completed", "summary": "ok"},
        "user_feedback": [], "evidence_refs": ["e1", "e2"],
        "confidence": {"boundary": 0.9, "intent": 0.8, "outcome": 0.9, "task_profile": 0.6},
    }]
    return json.dumps({
        "schema_version": "icle-session-analysis/v0.1",
        "status": "ok", "facts": [{"text": "用户要求加置信度", "evidence": ["e1"]}],
        "inferences": [], "proposals": [],
        "confidence": {"boundary": 0.9, "intent": 0.8, "outcome": 0.9, "task_profile": 0.6},
        "tasks": tasks,
    })


class CompactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = load_skill("task-intelligence")

    def test_compaction_drops_noise(self) -> None:
        bundle = run_script(self.skill, "compact_session", EVENTS)
        self.assertEqual(bundle["noise_dropped"], 1)  # meta 空内容
        self.assertEqual(len(bundle["events"]), 4)
        self.assertEqual(bundle["events"][0]["event_id"], "e1")

    def test_rule_boundaries_time_gap(self) -> None:
        bundle = run_script(self.skill, "compact_session", EVENTS)
        # 00:10:00 - 00:00:05 = 595s > 300s → time_gap 边界
        self.assertTrue(any("time_gap" in b["signals"] for b in bundle["boundaries"]))
        for b in bundle["boundaries"]:
            self.assertGreaterEqual(b["boundary_probability"], 0.4)

    def test_kimi_wire_json_preserved_as_text(self) -> None:
        """kimi 会话 content 是 wire JSON(嵌套 text):应提取为自然语言而非丢为噪音。"""
        wire = json.dumps({"type": "turn.prompt", "input": [{"type": "text", "text": "请审查这个方案"}]})
        events = [
            {"session_id": "s1", "seq": 1, "kind": "user", "content": wire, "ts": "2026-08-08T00:00:00Z"},
            {"session_id": "s1", "seq": 2, "kind": "assistant", "content": '{"x":1}', "ts": "2026-08-08T00:00:01Z"},
        ]
        bundle = run_script(self.skill, "compact_session", events)
        self.assertEqual(len(bundle["events"]), 1)  # wire 保留,纯 JSON 回显丢
        self.assertEqual(bundle["events"][0]["content"], "请审查这个方案")
        self.assertEqual(bundle["noise_dropped"], 1)


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.skill = load_skill("task-intelligence")

    def _bundle(self) -> dict:
        return run_script(self.skill, "compact_session", EVENTS)

    def test_valid_proposal_passes(self) -> None:
        proposal = json.loads(valid_reply())
        result = run_script(self.skill, "validate_analysis", proposal, self._bundle())
        self.assertEqual(result, proposal)

    def test_unknown_root_field_rejected(self) -> None:
        proposal = json.loads(valid_reply())
        proposal["hacked"] = True
        with self.assertRaises(ValueError):
            run_script(self.skill, "validate_analysis", proposal, self._bundle())

    def test_evidence_not_in_input_rejected(self) -> None:
        proposal = json.loads(valid_reply())
        proposal["tasks"][0]["evidence_refs"] = ["e99"]
        with self.assertRaises(ValueError):
            run_script(self.skill, "validate_analysis", proposal, self._bundle())

    def test_bad_enum_rejected(self) -> None:
        proposal = json.loads(valid_reply())
        proposal["tasks"][0]["task_type"] = "HACKING"
        with self.assertRaises(ValueError):
            run_script(self.skill, "validate_analysis", proposal, self._bundle())

    def test_confidence_out_of_range_rejected(self) -> None:
        proposal = json.loads(valid_reply())
        proposal["tasks"][0]["confidence"]["boundary"] = 1.4
        with self.assertRaises(ValueError):
            run_script(self.skill, "validate_analysis", proposal, self._bundle())

    def test_empty_tasks_rejected(self) -> None:
        proposal = json.loads(valid_reply(tasks=[]))
        with self.assertRaises(ValueError):
            run_script(self.skill, "validate_analysis", proposal, self._bundle())

    def test_insufficient_evidence_accepted(self) -> None:
        proposal = {"schema_version": "icle-session-analysis/v0.1", "session_id": "s1",
                    "status": "insufficient_evidence", "reason": "no user requests"}
        result = run_script(self.skill, "validate_analysis", proposal, self._bundle())
        self.assertEqual(result["status"], "insufficient_evidence")


class AnalyzeSessionTests(unittest.TestCase):
    def test_valid_analysis_with_provenance(self) -> None:
        fp = FakeProvider(valid_reply())
        result = analyze_session(fp, EVENTS, language="zh")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["tasks"]), 1)
        self.assertEqual(result["tasks"][0]["title"], "加置信度")
        prov = result["provenance"]
        self.assertEqual(prov["skill"]["name"], "icle-task-intelligence")
        self.assertEqual(prov["skill"]["version"], "0.2.0")
        self.assertTrue(prov["skill"]["sha256"].startswith("sha256:"))
        self.assertEqual(prov["language"], "zh")
        self.assertIn("Simplified Chinese", fp.prompts[0])  # 语言前置条件

    def test_garbage_output_rejected(self) -> None:
        with self.assertRaises(IntelligenceError):
            analyze_session(FakeProvider("not json"), EVENTS)

    def test_invalid_proposal_rejected(self) -> None:
        reply = valid_reply().replace('"evidence_refs": ["e1", "e2"]', '"evidence_refs": ["e99"]')
        with self.assertRaises(IntelligenceError):
            analyze_session(FakeProvider(reply), EVENTS)

    def test_insufficient_evidence_status(self) -> None:
        reply = json.dumps({"schema_version": "icle-session-analysis/v0.1", "session_id": "s1",
                            "status": "insufficient_evidence", "reason": "no tasks visible"})
        result = analyze_session(FakeProvider(reply), EVENTS)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_long_session_map_merge(self) -> None:
        """长会话分块:每块独立分析 → merge → candidate 重编号 c1..cn。"""
        import icle.intelligence as intel

        orig = intel.ANALYZE_CHUNK
        intel.ANALYZE_CHUNK = 2  # 强制分块
        try:
            many = []
            for i in range(1, 7):
                many.append({"session_id": "s1", "seq": i, "kind": "user" if i % 2 else "assistant",
                             "content": f"请求{i}", "ts": f"2026-08-08T00:{i:02d}:00Z"})
            fp = FakeProvider(valid_reply())
            result = analyze_session(fp, many)
            self.assertEqual(result["status"], "ok")
            ids = [t["candidate_id"] for t in result["tasks"]]
            self.assertEqual(len(ids), len(set(ids)), "candidate_id 必须唯一")
            self.assertTrue(all(cid.startswith("c") for cid in ids))
            self.assertGreaterEqual(len(fp.prompts), 3)  # 每 chunk 一次 + 至少分 3 块
        finally:
            intel.ANALYZE_CHUNK = orig


if __name__ == "__main__":
    unittest.main()


class PlanTaskTests(unittest.TestCase):
    """skill plan-task:Task Ledger → 1-3 PlanCandidate(不指定 agent)。"""

    def _plan_reply(self) -> str:
        return json.dumps({
            "schema_version": "icle-task-plan-proposal/v0.1",
            "ledger": {"goal": "重构 Router", "given_facts": [], "verified_facts": [],
                       "facts_to_lookup": [], "facts_to_derive": [],
                       "constraints": [], "unknowns": [], "assumptions": [],
                       "required_outputs": ["置信度字段"], "completion_contract": ["测试通过"]},
            "candidates": [{
                "candidate_id": "A", "execution_pattern": "DIRECT",
                "summary": "单步直接改", "expected_complexity": "low",
                "context_requirement": "MEDIUM", "estimated_calls": "1",
                "steps": [{
                    "step_id": "S1", "goal": "改置信度算法", "inputs": [], "expected_outputs": ["router.py 修改"],
                    "dependencies": [], "required_capabilities": ["coding"],
                    "context_policy": "CLEAN", "tools": [],
                    "verification": {"type": "test", "criteria": ["测试通过"]},
                    "risk": "R1", "estimated_complexity": "low", "parallel_group": None,
                }],
            }],
        })

    def test_plan_task_valid(self) -> None:
        from icle.intelligence import plan_task

        fp = FakeProvider(self._plan_reply())
        result = plan_task(fp, title="t", description="d", language="zh")
        self.assertEqual(result["schema_version"], "icle-task-plan-proposal/v0.1")
        self.assertEqual(result["candidates"][0]["candidate_id"], "A")
        self.assertEqual(result["provenance"]["skill"]["version"], "0.2.0")
        self.assertIn("Simplified Chinese", fp.prompts[0])
        # 校验已过:candidates 1-3、DAG、无 agent 字段

    def test_plan_task_rejects_agent_field(self) -> None:
        from icle.intelligence import IntelligenceError, plan_task

        reply = self._plan_reply().replace('"required_capabilities": ["coding"]',
                                           '"suggested_agent": "kimi", "required_capabilities": ["coding"]')
        with self.assertRaises(IntelligenceError):
            plan_task(FakeProvider(reply), title="t", description="d")

    def test_plan_task_rejects_cycle(self) -> None:
        from icle.intelligence import IntelligenceError, plan_task

        proposal = json.loads(self._plan_reply())
        proposal["candidates"][0]["steps"][0]["dependencies"] = ["S1"]  # 自环
        with self.assertRaises(IntelligenceError):
            plan_task(FakeProvider(json.dumps(proposal)), title="t", description="d")

    def test_plan_task_no_candidates_rejected(self) -> None:
        from icle.intelligence import IntelligenceError, plan_task

        with self.assertRaises(IntelligenceError):
            plan_task(FakeProvider('{"candidates":[]}'), title="t", description="d")


class ReplanTaskTests(unittest.TestCase):
    """skill replan-task:保留有效步骤,局部重规划。"""

    def _original_plan(self) -> dict:
        return {"strategy": "DIRECT", "steps": [{"step_id": "S1", "title": "a"}]}

    def _replan_reply(self) -> str:
        return json.dumps({
            "schema_version": "icle-replan-proposal/v0.1",
            "status": "replan_needed",
            "reasons": ["S1 测试失败,归一化边界问题"],
            "kept_step_ids": [],
            "replaced_steps": [{
                "step_id": "S1", "goal": "修复归一化", "inputs": [], "expected_outputs": ["测试过"],
                "dependencies": [], "required_capabilities": ["coding"],
                "context_policy": "CLEAN", "tools": [],
                "verification": {"type": "test", "criteria": ["test_confidence 通过"]},
                "risk": "R1", "estimated_complexity": "low", "parallel_group": None,
            }],
            "new_steps": [],
        })

    def test_replan_task_valid(self) -> None:
        from icle.intelligence import replan_task

        fp = FakeProvider(self._replan_reply())
        result = replan_task(fp, original_plan=self._original_plan(), failure="S1 failed")
        self.assertEqual(result["status"], "replan_needed")
        self.assertEqual(result["replaced_steps"][0]["step_id"], "S1")
        self.assertEqual(result["provenance"]["skill"]["version"], "0.2.0")

    def test_replan_kept_step_must_exist_in_original(self) -> None:
        from icle.intelligence import IntelligenceError, replan_task

        reply = self._replan_reply().replace('"kept_step_ids": []', '"kept_step_ids": ["S9"]')
        with self.assertRaises(IntelligenceError):
            replan_task(FakeProvider(reply), original_plan=self._original_plan(), failure="x")


if __name__ == "__main__":
    unittest.main()


class SplitTaskTests(unittest.TestCase):
    """skill split-task 两阶段:propose(2 套雏形)→ 用户选 → refine(精细化)。"""

    def _reply(self, candidates: list | None = None, needs_split: bool = True) -> str:
        if candidates is None:
            candidates = [
                {
                    "candidate_id": "c1", "approach": "按模块拆",
                    "rationale": "多个可独立交付能力", "children": [
                        {"title": "会话分析", "description": "d", "reason": "独立模块"},
                        {"title": "拆分能力", "description": "d", "reason": "独立模块"},
                    ],
                },
                {
                    "candidate_id": "c2", "approach": "按阶段拆",
                    "rationale": "顺序依赖强", "children": [
                        {"title": "调研", "description": "d", "reason": "阶段一"},
                        {"title": "实现", "description": "d", "reason": "阶段二"},
                        {"title": "测试", "description": "d", "reason": "阶段三"},
                    ],
                },
            ]
        return json.dumps({
            "schema_version": "icle-task-split-proposal/v0.2",
            "parent_task_id": "t-1",
            "needs_split": needs_split,
            "reason": "两个维度都合理" if needs_split else "单文件小改动，无需拆分",
            "candidates": candidates,
        })

    def test_propose_split_two_candidates(self) -> None:
        from icle.intelligence import propose_split_task

        fp = FakeProvider(self._reply())
        result = propose_split_task(fp, task={"task_id": "t-1", "title": "完善分析", "description": "d"}, language="zh")
        self.assertEqual(result["schema_version"], "icle-task-split-proposal/v0.2")
        self.assertEqual(len(result["candidates"]), 2)
        self.assertNotEqual(result["candidates"][0]["approach"], result["candidates"][1]["approach"])
        self.assertEqual(result["candidates"][0]["candidate_id"], "c1")
        self.assertEqual(len(result["candidates"][0]["children"]), 2)
        self.assertIn("Simplified Chinese", fp.prompts[0])

    def test_propose_split_refuses_when_not_needed(self) -> None:
        from icle.intelligence import propose_split_task

        result = propose_split_task(FakeProvider(self._reply(candidates=[], needs_split=False)),
                                    task={"task_id": "t-1", "title": "t"})
        self.assertFalse(result["needs_split"])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["reason"])

    def test_propose_split_rejects_wrong_candidate_count(self) -> None:
        from icle.intelligence import IntelligenceError, propose_split_task

        one = [{"candidate_id": "c1", "approach": "a", "rationale": "r", "children": []}]
        reply = json.dumps({"schema_version": "icle-task-split-proposal/v0.2", "candidates": one,
                            "needs_split": True, "reason": "x"})
        with self.assertRaises(IntelligenceError):
            propose_split_task(FakeProvider(reply), task={"task_id": "t-1", "title": "t"})

    def test_propose_split_rejects_missing_title(self) -> None:
        from icle.intelligence import IntelligenceError, propose_split_task

        candidates = [{
            "candidate_id": "c1", "approach": "a", "rationale": "r",
            "children": [{"title": "", "description": "x", "reason": "r"}]},
            {"candidate_id": "c2", "approach": "b", "rationale": "r",
             "children": [{"title": "ok", "description": "", "reason": "r"}]}]
        reply = json.dumps({"schema_version": "icle-task-split-proposal/v0.2", "candidates": candidates,
                            "needs_split": True, "reason": "x"})
        with self.assertRaises(IntelligenceError):
            propose_split_task(FakeProvider(reply), task={"task_id": "t-1", "title": "t"})

    def test_refine_split_elaborates_candidate(self) -> None:
        from icle.intelligence import refine_split_task

        candidate = {"candidate_id": "c2", "approach": "按阶段拆", "rationale": "顺序依赖强",
                     "children": [{"title": "调研", "description": "粗", "reason": "阶段一"}]}
        refined_reply = json.dumps({
            "schema_version": "icle-task-split-proposal/v0.2",
            "parent_task_id": "t-1", "candidate_id": "c2",
            "children": [
                {"title": "调研现状", "description": "分析现有会话捕获链路", "reason": "阶段一细化"},
                {"title": "设计", "description": "确定接口", "reason": "阶段二细化"},
            ],
        })
        fp = FakeProvider(refined_reply)
        result = refine_split_task(fp, task={"task_id": "t-1", "title": "t"}, candidate=candidate, language="zh")
        self.assertEqual(result["candidate_id"], "c2")
        self.assertEqual(result["approach"], "按阶段拆")
        self.assertEqual(len(result["children"]), 2)
        self.assertEqual(result["children"][0]["title"], "调研现状")
        self.assertIn("refine", fp.prompts[0].lower())

    def test_refine_split_rejects_missing_candidate(self) -> None:
        from icle.intelligence import IntelligenceError, refine_split_task

        with self.assertRaises(IntelligenceError):
            refine_split_task(FakeProvider('{}'), task={"task_id": "t-1", "title": "t"}, candidate={})


class NewSkillModeTests(unittest.TestCase):
    """v0.2 modes: analyze-task / judge-result / judge-pair / summarize-reports."""

    def test_analyze_task_validates_and_tags_skill(self) -> None:
        from icle.intelligence import propose_task_profile

        reply = json.dumps({
            "primary_type": "CODING", "subtype": "debugging",
            "difficulty": "D2", "risk": "R1", "context_requirement": "MEDIUM",
            "tool_requirement": ["filesystem", "shell"], "estimated_duration": "short",
            "decomposition": "not_recommended", "review": "recommended",
            "reason": "small reversible fix",
        })
        result = propose_task_profile(FakeProvider(reply), title="t", description="d")
        self.assertEqual(result["primary_type"], "CODING")
        self.assertEqual(result["provenance"]["skill"]["version"], "0.2.0")

    def test_analyze_task_rejects_invented_type(self) -> None:
        from icle.intelligence import IntelligenceError, propose_task_profile

        with self.assertRaises(IntelligenceError):
            propose_task_profile(
                FakeProvider('{"primary_type":"HACKING","reason":"x"}'),
                title="t", description="d",
            )

    def test_judge_result_and_pair(self) -> None:
        from icle.intelligence import judge_pair, propose_rating

        rating = propose_rating(
            FakeProvider(json.dumps({
                "dimensions": {
                    "requirement_fit": 5, "correctness": 4, "efficiency": 3,
                    "autonomy": 4, "maintainability": 4,
                },
                "overall_preference": 4, "would_use_again": "yes",
                "comment": "solid",
            })),
            task="fix", result="done",
        )
        self.assertEqual(rating["dimensions"]["correctness"], 4)
        self.assertEqual(rating["provenance"]["skill"]["name"], "icle-task-intelligence")
        pair = judge_pair(
            FakeProvider(json.dumps({
                "verdict": "prefer_b", "reason": "B handles edges",
                "confidence": 0.7, "reason_tags": ["correctness"],
            })),
            task="fix", result_a="A", result_b="B",
        )
        self.assertEqual(pair["output"]["verdict"], "prefer_b")

    def test_summarize_reports_rejects_count_tampering(self) -> None:
        from icle.intelligence import IntelligenceError, summarize_task_reports

        merged = {
            "requirements_total": 4, "requirements_met": 3,
            "verification_ran": True, "verification_passed": True,
            "tests_total": 2, "tests_passed": 2,
            "files_changed": [], "commands_run": [],
            "blocked": False, "blocked_reason": "", "summary": "merge",
        }
        entries = [{"task_id": "t1", "agent": "kimi", "report": merged, "report_status": "observed"}]
        with self.assertRaises(IntelligenceError):
            summarize_task_reports(
                FakeProvider(json.dumps({
                    "requirements_total": 999, "summary": "nope", "blocked_reason": "",
                })),
                entries=entries, merged=merged,
            )
        ok = summarize_task_reports(
            FakeProvider(json.dumps({"summary": "two agents finished", "blocked_reason": ""})),
            entries=entries, merged=merged,
        )
        self.assertEqual(ok["requirements_total"], 4)
        self.assertEqual(ok["summary"], "two agents finished")
        self.assertEqual(ok["provenance"]["skill"]["version"], "0.2.0")


if __name__ == "__main__":
    unittest.main()
