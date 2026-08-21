"""templates.py:固定拆分模板库 + 规则推荐 + template-customize 定制校验。"""

import json
import tempfile
import unittest
from pathlib import Path

from icle import templates as template_lib
from icle.intelligence import IntelligenceError, plan_from_template
from tests.test_task_intelligence import FakeProvider


class TemplatesTests(unittest.TestCase):
    def test_twelve_builtin_templates_validate(self) -> None:
        ids = template_lib.validate_all_templates()
        self.assertEqual(len(ids), 12)
        for required in ("feature", "bugfix", "refactor", "migration", "review",
                         "release", "incident", "research", "data_analysis",
                         "security_audit", "documentation", "production_readiness"):
            self.assertIn(required, ids)

    def test_template_structure(self) -> None:
        bugfix = template_lib.get_template("bugfix")
        self.assertIsNotNone(bugfix)
        self.assertEqual(bugfix["applicable_task_types"], ["CODING"])
        self.assertIn("D2", bugfix["recommended_difficulty"])
        keys = [c["key"] for c in bugfix["children"]]
        self.assertEqual(keys, ["reproduce", "root_cause", "fix_design", "implement", "regression", "boundary"])
        # 复现独立成步(用户核心要求)且不可省略
        self.assertFalse(bugfix["children"][0]["optional"])
        # optional 标记存在且是布尔
        for child in bugfix["children"]:
            self.assertIsInstance(child["optional"], bool)
        # 文档/边界等可选步骤标记为 optional
        self.assertTrue(bugfix["children"][-1]["optional"])

    def test_list_templates_filter_and_blank(self) -> None:
        all_tpl = template_lib.list_templates()
        self.assertEqual(len(all_tpl), 13)  # 12 + blank
        self.assertEqual(all_tpl[-1]["template_id"], "blank")
        coding = template_lib.list_templates(primary_type="CODING")
        ids = [t["template_id"] for t in coding]
        self.assertIn("bugfix", ids)
        self.assertNotIn("research", ids)  # research 只适用 RESEARCH
        research_only = template_lib.list_templates(primary_type="RESEARCH")
        self.assertIn("research", [t["template_id"] for t in research_only])

    def test_unknown_template(self) -> None:
        self.assertIsNone(template_lib.get_template("nope"))


class RecommendTemplateTests(unittest.TestCase):
    def _task(self, title: str, description: str, profile: dict | None = None) -> dict:
        return {"task_id": "t-1", "title": title, "description": description,
                "profile": profile or {"primary_type": "CODING", "difficulty": "D2"}}

    def test_bugfix_keywords_top(self) -> None:
        r = template_lib.recommend_template(
            self._task("修复 API 崩溃 bug", "登录接口报错, 500 异常"))
        top = r["recommendations"][0]
        self.assertEqual(top["template_id"], "bugfix")
        self.assertGreaterEqual(top["confidence"], 0.6)
        self.assertLessEqual(top["confidence"], 0.97)

    def test_research_keywords_top_for_research(self) -> None:
        r = template_lib.recommend_template(
            self._task("调研大模型路由方案", "对比主流路由器的资料",
                       {"primary_type": "RESEARCH", "difficulty": "D3"}))
        top = r["recommendations"][0]
        self.assertEqual(top["template_id"], "research")

    def test_profile_fit_boosts_without_keywords(self) -> None:
        # 无关键词但有 profile:difficulty 命中 + primary 命中 → 有非零置信度
        r = template_lib.recommend_template(
            self._task("整理需求", "把需求写清楚", {"primary_type": "CODING", "difficulty": "D4"}))
        self.assertEqual(r["recommendations"][-1]["template_id"], "blank")
        # blank 置信度与最高分互补(<= 0.97)
        self.assertLessEqual(r["recommendations"][-1]["confidence"], 0.97)

    def test_blank_fallback_present(self) -> None:
        r = template_lib.recommend_template(self._task("x", "y"))
        ids = [x["template_id"] for x in r["recommendations"]]
        self.assertIn("blank", ids)


class PlanFromTemplateTests(unittest.TestCase):
    """template-customize:LLM 只做语义定制,骨架 keys/optional 由模板锁定。"""

    def _reply(self, children: list, fits: bool = True, reason: str = "fits") -> str:
        return json.dumps({
            "schema_version": "icle-template-plan/v0.1",
            "template_id": "bugfix", "fits_template": fits, "reason": reason,
            "children": children,
        })

    def test_customize_keeps_skeleton_and_overrides_optional(self) -> None:
        template = template_lib.get_template("bugfix")
        customized = [
            {"key": "reproduce", "title": "复现:提交两次 children 请求",
             "description": "用 POST /api/tasks/{id}/children 重复提交两次, 确认出现重复", "optional": False},
            {"key": "root_cause", "title": "定位根因", "description": "检查 children 去重逻辑", "optional": False},
            {"key": "fix_design", "title": "修复设计", "description": "最小风险方案", "optional": True},
            {"key": "implement", "title": "实现修复", "description": "加唯一约束", "optional": False},
            {"key": "regression", "title": "回归测试", "description": "为 bug 加测试", "optional": False},
            {"key": "boundary", "title": "边界验证", "description": "相邻场景", "optional": True},
        ]
        fp = FakeProvider(self._reply(customized))
        result = plan_from_template(fp, task={"task_id": "t-1", "title": "修复 bug", "description": "d"},
                                    template=template, language="zh")
        self.assertEqual(result["template_id"], "bugfix")
        self.assertEqual(len(result["children"]), 6)
        self.assertEqual(result["children"][0]["key"], "reproduce")
        self.assertIn("children", result["children"][0]["description"])
        self.assertIn("Simplified Chinese", fp.prompts[0])
        self.assertIn("template-customize", fp.prompts[0])

    def test_customize_rejects_key_mismatch(self) -> None:
        template = template_lib.get_template("bugfix")
        dropped = [
            {"key": "reproduce", "title": "a", "description": "", "optional": False},
            {"key": "root_cause", "title": "b", "description": "", "optional": False},
            {"key": "fix_design", "title": "c", "description": "", "optional": False},
            {"key": "implement", "title": "d", "description": "", "optional": False},
            {"key": "regression", "title": "e", "description": "", "optional": False},
        ]  # 少 boundary
        with self.assertRaises(IntelligenceError):
            plan_from_template(FakeProvider(self._reply(dropped)),
                               task={"task_id": "t-1", "title": "t"}, template=template)

    def test_customize_rejects_extra_key(self) -> None:
        template = template_lib.get_template("bugfix")
        extra = [
            {"key": "reproduce", "title": "a", "description": "", "optional": False},
            {"key": "root_cause", "title": "b", "description": "", "optional": False},
            {"key": "fix_design", "title": "c", "description": "", "optional": False},
            {"key": "implement", "title": "d", "description": "", "optional": False},
            {"key": "regression", "title": "e", "description": "", "optional": False},
            {"key": "boundary", "title": "f", "description": "", "optional": False},
            {"key": "extra_step", "title": "g", "description": "", "optional": False},
        ]
        with self.assertRaises(IntelligenceError):
            plan_from_template(FakeProvider(self._reply(extra)),
                               task={"task_id": "t-1", "title": "t"}, template=template)

    def test_customize_fits_false_with_reason(self) -> None:
        template = template_lib.get_template("bugfix")
        result = plan_from_template(
            FakeProvider(self._reply([], fits=False, reason="这是文档任务不是 bug")),
            task={"task_id": "t-1", "title": "t"}, template=template)
        self.assertFalse(result["fits_template"])
        self.assertEqual(result["children"], [])
        self.assertIn("文档", result["reason"])

    def test_customize_rejects_fits_false_without_reason(self) -> None:
        template = template_lib.get_template("bugfix")
        with self.assertRaises(IntelligenceError):
            plan_from_template(FakeProvider(self._reply([], fits=False, reason="")),
                               task={"task_id": "t-1", "title": "t"}, template=template)


class TemplatesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="icle-tpl-"))
        self.store = self.root / "store"
        self.store.mkdir()
        (self.store / "episodes").mkdir()
        (self.store / "marks").mkdir()
        from fastapi.testclient import TestClient

        from icle.api.app import create_app

        self.client = TestClient(create_app(store=str(self.store), capture_store=str(self.root / "capture")))

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def test_get_templates(self) -> None:
        r = self.client.get("/api/templates")
        self.assertEqual(r.status_code, 200)
        templates = r.json()["templates"]
        self.assertEqual(len(templates), 13)
        bugfix = next(t for t in templates if t["template_id"] == "bugfix")
        self.assertTrue(all("key" in c and "optional" in c for c in bugfix["children"]))

    def test_get_templates_filter(self) -> None:
        r = self.client.get("/api/templates?primary_type=RESEARCH")
        ids = [t["template_id"] for t in r.json()["templates"]]
        self.assertIn("research", ids)
        self.assertNotIn("bugfix", ids)

    def test_recommend_template_api(self) -> None:
        from icle.task import create_task

        task = create_task(self.store, title="修复登录崩溃 bug", description="接口 500",
                           project_id="p", project_path="/tmp")
        r = self.client.post(f"/api/tasks/{task['task_id']}/recommend-template")
        self.assertEqual(r.status_code, 200)
        top = r.json()["recommendations"][0]
        self.assertEqual(top["template_id"], "bugfix")

    def test_plan_from_template_requires_llm(self) -> None:
        from icle.task import create_task

        task = create_task(self.store, title="t", description="d", project_id="p")
        r = self.client.post(f"/api/tasks/{task['task_id']}/plan-from-template",
                             json={"template_id": "bugfix"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("LLM provider", r.json()["detail"])

    def test_plan_from_template_unknown_template(self) -> None:
        from icle.task import create_task

        task = create_task(self.store, title="t", description="d", project_id="p")
        r = self.client.post(f"/api/tasks/{task['task_id']}/plan-from-template",
                             json={"template_id": "nope"})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
