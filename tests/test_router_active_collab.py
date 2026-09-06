"""P6/P7/P8: experience router, active replay budget, collaboration tests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.active import (  # noqa: E402
    BudgetError,
    consume_budget,
    remaining_budget,
    set_budget,
    suggest_replays,
)
from icle.collab import author_reviewer, handoff, parallel_integrate  # noqa: E402
from icle.episode import create_episode  # noqa: E402
from icle.judge import record_judgment, record_result_mark  # noqa: E402
from icle.replay import replay  # noqa: E402
from icle.router import RouterError, executable_candidates, recommend_agent  # noqa: E402


def revision(agent: str) -> dict:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent,
        "revision_id": "rev-1",
        "model": "m", "cli": "c", "provider": "p",
        "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": "2026-08-14T00:00:00Z",
    }


def make_git_project(path: Path) -> None:
    (path / "app.py").write_text("x = 1\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, env=env, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, env=env, check=True)


def executor_with(text: str):
    def run(workspace: Path, prompt: str, timeout: float):
        (workspace / "out.txt").write_text(text, encoding="utf-8")
        return ("completed", text, "", 0)

    return run


class RouterTests(unittest.TestCase):
    def test_recommend_uses_similar_and_global_evidence(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        ep_csv = create_episode(
            store, session_id="s1", agent_revision=revision("kimi"),
            project_id="demo", project_path=project,
            user_request="fix the csv parsing bug",
        )
        ep_blog = create_episode(
            store, session_id="s2", agent_revision=revision("kimi"),
            project_id="demo", project_path=project,
            user_request="write a blog post",
        )
        record_result_mark(store, episode_id=ep_csv["episode_id"], mark="accept", agent_id="kimi")
        record_result_mark(store, episode_id=ep_csv["episode_id"], mark="reject", agent_id="hermes")
        record_result_mark(store, episode_id=ep_blog["episode_id"], mark="accept", agent_id="hermes")

        result = recommend_agent(store, task="another csv parsing issue", project_id="demo")
        self.assertIsNone(result["recommended"])
        self.assertEqual(result["policy"], "experience-router-v0.4")
        ranking = {r["agent"]: r for r in result["ranking"]}
        self.assertEqual(ranking["kimi"]["similar_outcomes"], 1)
        self.assertIsNone(ranking["kimi"]["quality_score"])
        self.assertTrue(ranking["kimi"]["exploration"])
        self.assertEqual(ranking["kimi"]["agent_shell_evidence_count"], 1)
        self.assertTrue(any("agent_shell=" in item for item in ranking["kimi"]["uncertainty"]))
        self.assertEqual(ranking["kimi"]["score_source"], "none")

    def test_unevaluated_similar_episode_falls_back_to_domain_evidence(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        evaluated = create_episode(
            store, session_id="s1", agent_revision=revision("kimi"),
            project_id="demo", project_path=project, user_request="fix a parser bug",
        )
        create_episode(
            store, session_id="s2", agent_revision=revision("hermes"),
            project_id="demo", project_path=project, user_request="fix the router confidence bug",
        )
        record_result_mark(store, episode_id=evaluated["episode_id"], mark="accept", agent_id="kimi")
        result = recommend_agent(
            store,
            task="fix the router confidence bug",
            project_id="demo",
            candidates=["kimi", "hermes"],
        )
        self.assertIsNone(result["recommended"])
        self.assertTrue(all(item["exploration"] for item in result["ranking"]))
        kimi = next(item for item in result["ranking"] if item["agent"] == "kimi")
        self.assertEqual(kimi["agent_shell_evidence_count"], 1)

    def test_saved_task_profile_overrides_ambiguous_short_title(self) -> None:
        result = recommend_agent(
            Path(tempfile.mkdtemp()),
            task="设计",
            task_profile={"primary_type": "PLANNING"},
            candidates=["provider/model-x"],
        )
        self.assertEqual(result["domain"], "planning")
        self.assertIsNone(result["recommended"])

    def test_domain_isolation_keeps_unmeasured_quality_unknown(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        episode = create_episode(
            store, session_id="s1", agent_revision=revision("kimi"),
            project_id="demo", project_path=project, user_request="fix a parser bug",
        )
        record_result_mark(store, episode_id=episode["episode_id"], mark="accept", agent_id="kimi")
        result = recommend_agent(
            store,
            task="调研公开基准并比较文献",
            candidates=["kimi", "provider/model-x"],
        )
        self.assertEqual(result["domain"], "research")
        self.assertIsNone(result["recommended"])
        self.assertTrue(all(item["score"] is None for item in result["ranking"]))
        self.assertTrue(all(item["exploration"] for item in result["ranking"]))

    def test_executable_candidates_include_provider_models_not_capture_only(self) -> None:
        detected = [
            {"agent_type": "kimi", "status": "linked", "execution_supported": True},
            {"agent_type": "codex", "status": "linked", "execution_supported": False},
        ]
        providers = [{
            "provider_id": "p1", "status": "connected", "type": "openai-compatible",
            "configured": True, "models": [{"id": "model-a"}],
        }]
        from unittest.mock import patch

        with patch("icle.discovery.scan_agents", return_value=detected), patch(
            "icle.provider.list_providers", return_value=providers
        ):
            candidates = executable_candidates(Path(tempfile.mkdtemp()))
        self.assertEqual(candidates, ["kimi", "p1/model-a"])

    def test_observed_j_drives_policy_order(self) -> None:
        store = Path(tempfile.mkdtemp()) / "store"
        store.mkdir()
        from icle.evolution import _safe_subject, subject_id_for

        def write_j(agent: str, value: float) -> None:
            route = {"agent": agent, "provider_id": "", "model": ""}
            path = store / "evolution" / "layers" / f"{_safe_subject(subject_id_for(route))}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "j": {"cells": [
                    {"dimension_id": "coding", "value": value},
                    {"dimension_id": "agentic_coding", "value": value},
                ]}
            }), encoding="utf-8")

        write_j("kimi", 1.2)
        write_j("hermes", 0.1)
        result = recommend_agent(
            store,
            task="fix the csv parser",
            task_profile={"primary_type": "CODING"},
            candidates=["kimi", "hermes"],
        )
        self.assertEqual(result["recommended"], "kimi")
        self.assertEqual(result["ranking"][0]["decision"]["quality_source"], "j_observed")

    def test_no_evidence_raises(self) -> None:
        with self.assertRaises(RouterError):
            recommend_agent(Path(tempfile.mkdtemp()), task="anything")


class ActiveReplayTests(unittest.TestCase):
    def test_budget_hard_cap(self) -> None:
        store = Path(tempfile.mkdtemp())
        set_budget(store, 2)
        self.assertEqual(remaining_budget(store), 2)
        consume_budget(store, reason="r1")
        consume_budget(store, reason="r2")
        self.assertEqual(remaining_budget(store), 0)
        with self.assertRaisesRegex(BudgetError, "exhausted"):
            consume_budget(store, reason="r3")

    def test_suggest_targets_evidence_gaps(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        episode = create_episode(
            store, session_id="s1", agent_revision=revision("hermes"),
            project_id="demo", project_path=project, user_request="fix bug",
        )
        replay(store, episode["episode_id"], target_agent_revision=revision("hermes"),
               executor=executor_with("A"))
        record_result_mark(store, episode_id=episode["episode_id"], mark="accept", agent_id="hermes")
        suggestions = suggest_replays(store, candidates=["hermes", "kimi"])
        self.assertTrue(suggestions["suggestions"])
        top = suggestions["suggestions"][0]
        self.assertNotEqual(top["agent"], "hermes")  # already replayed
        self.assertGreater(top["value"], 0.5)

    def test_budget_caps_suggestions(self) -> None:
        root = Path(tempfile.mkdtemp())
        project = root / "proj"
        project.mkdir()
        make_git_project(project)
        store = root / "store"
        for index in range(3):
            episode = create_episode(
                store, session_id=f"s{index}", agent_revision=revision("hermes"),
                project_id="demo", project_path=project, user_request=f"task {index}",
            )
            replay(store, episode["episode_id"], target_agent_revision=revision("hermes"),
                   executor=executor_with("x"))
        set_budget(store, 1)
        suggestions = suggest_replays(store, limit=5, candidates=["hermes", "kimi"])
        self.assertEqual(len(suggestions["suggestions"]), 1)


class CollabTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.project = self.root / "proj"
        self.project.mkdir()
        make_git_project(self.project)
        self.store = self.root / "store"
        self.episode = create_episode(
            self.store, session_id="s1", agent_revision=revision("hermes"),
            project_id="demo", project_path=self.project, user_request="improve the app",
        )

    def test_handoff_passes_artifact_only(self) -> None:
        seen_prompts: list[str] = []

        def executor_b(workspace: Path, prompt: str, timeout: float):
            seen_prompts.append(prompt)
            (workspace / "b.txt").write_text("B continued", encoding="utf-8")
            return ("completed", "B done", "", 0)

        result = handoff(
            self.store, self.episode["episode_id"],
            agent_a="hermes", agent_b="kimi",
            executor_a=executor_with("A artifact"),
            executor_b=executor_b,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["steps"]), 2)
        self.assertTrue(result["team_revision_id"].startswith("team-"))
        self.assertIn("A artifact", seen_prompts[0])
        self.assertIn("ARTIFACT_ONLY", result["context_note"])

    def test_parallel_integrate_merges(self) -> None:
        result = parallel_integrate(
            self.store, self.episode["episode_id"],
            agents=[("hermes", executor_with("A")), ("kimi", executor_with("B"))],
            integrator=lambda request, outputs: f"merged: {sorted(outputs)}",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["steps"]), 2)
        comparison = Path(result["run_dir"]) / "comparison.txt"
        self.assertIn("hermes", comparison.read_text())
        self.assertEqual(result["pattern"], "parallel-compare")

    def test_author_reviewer_records_verdict(self) -> None:
        result = author_reviewer(
            self.store, self.episode["episode_id"],
            author="hermes", reviewer="kimi",
            executor_author=executor_with("draft"),
            executor_reviewer=executor_with('{"verdict":"revise","notes":"tighten"}'),
        )
        self.assertEqual(result["review"]["verdict"], "revise")
        self.assertEqual([step["role"] for step in result["steps"]], ["author", "reviewer"])
        review_path = Path(result["run_dir"]) / "review.json"
        self.assertTrue(review_path.is_file())


if __name__ == "__main__":
    unittest.main()
