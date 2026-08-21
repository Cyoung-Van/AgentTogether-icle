"""v0.5 Task Hierarchy:子任务拆分(建议评审 14 项测试清单)。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.task import (  # noqa: E402
    MAX_TASK_DEPTH,
    TaskError,
    TaskTreeError,
    assert_deletable,
    create_children,
    create_task,
    end_task,
    list_children,
    parent_summary,
    show_task,
    subtask_projection,
    task_depth,
)


class TaskHierarchyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"

    def _root(self) -> dict:
        return create_task(self.store, title="父任务", description="d", project_id="icle")

    # 1. root task 创建 child 成功
    def test_01_create_children_ok(self) -> None:
        parent = self._root()
        created = create_children(
            self.store, parent["task_id"],
            [{"title": "调研"}, {"title": "设计", "description": "定义 schema"}],
        )
        self.assertEqual(len(created), 2)

    # 2. child parent_task_id 正确
    def test_02_parent_id_correct(self) -> None:
        parent = self._root()
        created = create_children(self.store, parent["task_id"], [{"title": "c"}])
        self.assertEqual(created[0]["parent_task_id"], parent["task_id"])

    # 3. children sibling_order 正确
    def test_03_sibling_order(self) -> None:
        parent = self._root()
        created = create_children(
            self.store, parent["task_id"],
            [{"title": "a"}, {"title": "b"}, {"title": "c"}],
        )
        self.assertEqual([c["sibling_order"] for c in created], [1, 2, 3])

    # 4. 不存在 parent → 404/400
    def test_04_missing_parent(self) -> None:
        with self.assertRaises(TaskTreeError):
            create_children(self.store, "t-none", [{"title": "c"}])

    # 5. 空 children → 400
    def test_05_empty_children(self) -> None:
        parent = self._root()
        with self.assertRaises(TaskTreeError):
            create_children(self.store, parent["task_id"], [])

    # 6. title 缺失 → 400
    def test_06_missing_title(self) -> None:
        parent = self._root()
        with self.assertRaises(TaskTreeError):
            create_children(self.store, parent["task_id"], [{"description": "no title"}])

    # 7. 一次多个 child 全部创建
    def test_07_multi_children_all_created(self) -> None:
        parent = self._root()
        created = create_children(
            self.store, parent["task_id"],
            [{"title": f"子{i}"} for i in range(1, 6)],
        )
        self.assertEqual(len(created), 5)
        self.assertEqual(len(list_children(self.store, parent["task_id"])), 5)

    # 8. 某 child invalid → 全部不创建(all-or-nothing)
    def test_08_invalid_child_aborts_all(self) -> None:
        parent = self._root()
        with self.assertRaises(TaskTreeError):
            create_children(self.store, parent["task_id"],
                            [{"title": "ok"}, {"title": ""}])
        self.assertEqual(list_children(self.store, parent["task_id"]), [])

    # 9. depth > max_depth → 400/409
    def test_09_depth_limit(self) -> None:
        parent = self._root()
        child = create_children(self.store, parent["task_id"], [{"title": "子"}])[0]
        with self.assertRaises(TaskTreeError):
            create_children(self.store, child["task_id"], [{"title": "孙"}])
        self.assertEqual(task_depth(self.store, child), 1)
        self.assertEqual(MAX_TASK_DEPTH, 1)

    # 10. GET children 只返回直属
    def test_10_children_direct_only(self) -> None:
        parent = self._root()
        child = create_children(self.store, parent["task_id"], [{"title": "子"}])[0]
        other = create_task(self.store, title="另一个父")
        create_children(self.store, other["task_id"], [{"title": "别的子"}])
        children = list_children(self.store, parent["task_id"])
        self.assertEqual([c["task_id"] for c in children], [child["task_id"]])

    # 11. 原有无 parent_task_id Task 正常读取
    def test_11_legacy_task_readable(self) -> None:
        legacy = create_task(self.store, title="旧任务")
        self.assertIsNone(legacy.get("parent_task_id"))
        shown = show_task(self.store, legacy["task_id"])
        self.assertEqual(shown["title"], "旧任务")

    # 12. 父 Task profile/status 不因 split 自动变化
    def test_12_parent_unchanged_by_split(self) -> None:
        parent = self._root()
        create_children(self.store, parent["task_id"], [{"title": "c"}])
        shown = show_task(self.store, parent["task_id"])
        self.assertEqual(shown["status"], "draft")
        self.assertIsNone(shown.get("profile"))

    # 13. child 有自己的独立 lifecycle
    def test_13_child_independent_lifecycle(self) -> None:
        parent = self._root()
        child = create_children(self.store, parent["task_id"], [{"title": "c"}])[0]
        from icle.task import save_plan, set_task_profile

        set_task_profile(self.store, child["task_id"], {
            "schema_version": "icle-task-profile/v0.1",
            "primary_type": "CODING", "subtype": "", "difficulty": "D2", "risk": "R1",
            "context_requirement": "MEDIUM", "tool_requirement": [],
            "estimated_duration": "unknown", "decomposition": "not_recommended",
            "review": "not_recommended", "reason": "r", "source": "manual",
        })
        save_plan(self.store, child["task_id"], strategy="DIRECT",
                  steps=[{"step_id": "S1", "title": "s", "description": "d",
                          "recommended_agent": "kimi"}])
        shown = show_task(self.store, child["task_id"])
        self.assertEqual(shown["status"], "planned")
        self.assertIsNotNone(shown["profile"])
        parent_again = show_task(self.store, parent["task_id"])
        self.assertEqual(parent_again["status"], "draft")  # 父不受影响

    # 14. 删除有 child 的 parent 被拒绝
    def test_14_delete_parent_with_children_rejected(self) -> None:
        parent = self._root()
        create_children(self.store, parent["task_id"], [{"title": "c"}])
        with self.assertRaises(TaskTreeError):
            assert_deletable(self.store, parent["task_id"])
        # 无 children 的可删
        leaf = self._root()
        assert_deletable(self.store, leaf["task_id"])

    def test_ending_parent_also_ends_unfinished_children(self) -> None:
        parent = self._root()
        children = create_children(
            self.store, parent["task_id"], [{"title": "one"}, {"title": "two"}]
        )
        ended = end_task(self.store, parent["task_id"])
        self.assertEqual(ended["status"], "cancelled")
        self.assertTrue(all(
            show_task(self.store, child["task_id"])["status"] == "cancelled"
            for child in children
        ))


class HierarchyHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"

    def test_parent_summary_and_projection(self) -> None:
        parent = create_task(self.store, title="父", description="goal x", project_id="p")
        children = create_children(
            self.store, parent["task_id"],
            [{"title": "a"}, {"title": "b"}, {"title": "c"}],
        )
        summary = parent_summary(self.store, children[0])
        self.assertEqual(summary["task_id"], parent["task_id"])
        self.assertEqual(summary["title"], "父")
        projection = subtask_projection(self.store, parent["task_id"])
        self.assertEqual(projection["total"], 3)
        self.assertEqual(projection["completed"], 0)
        self.assertEqual(projection["by_status"], {"draft": 3})
        # 根任务无父摘要
        self.assertIsNone(parent_summary(self.store, parent))


if __name__ == "__main__":
    unittest.main()
