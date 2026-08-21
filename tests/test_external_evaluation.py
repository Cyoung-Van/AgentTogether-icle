"""External benchmark priors: provenance, freshness, model matching and blending."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from icle.external_evaluation import (
    SCHEMA,
    ExternalEvaluationError,
    _model_aliases,
    _parse_aider,
    _parse_swebench,
    _parse_terminal_bench,
    _model_family,
    dataset_quality,
    external_baseline,
    infer_domain,
    rank_agent,
    refresh_snapshots,
    save_snapshot,
)

class ExternalEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Path(tempfile.mkdtemp()) / "store"
        self.store.mkdir()
        self.now = datetime.now(timezone.utc)

    def _snapshot(self, *, model: str = "model-x", score: float = 0.7, stale: bool = False) -> dict:
        retrieved = self.now.isoformat()
        return {
            "schema_version": SCHEMA,
            "source_id": "swebench_verified",
            "source_url": "https://www.swebench.com/",
            "authority": "benchmark_maintainer",
            "benchmark": "SWE-bench Verified",
            "benchmark_version": "Verified",
            "domain": "coding",
            "metric": "resolved_rate",
            "license": "MIT",
            "evaluation_scope": "agent_model",
            "provenance_tier": "official_leaderboard",
            "retrieved_at": retrieved,
            "fresh_until": (self.now + timedelta(days=-1 if stale else 10)).isoformat(),
            "content_hash": "sha256:test",
            "parser_version": "test",
            "records": [{
                "source_id": "swebench_verified",
                "benchmark": "SWE-bench Verified",
                "domain": "coding",
                "model": model,
                "model_aliases": [model.replace("-", "")],
                "score": score,
                "result_date": "2026-08-01",
                "retrieved_at": retrieved,
                "metadata": {},
            }],
        }

    def test_infer_domain_supports_chinese_task_intent(self) -> None:
        cases = {
            "修复 Python Router 的边界 bug": "coding",
            "重构 Router 降低技术债": "refactoring",
            "排查 macOS 终端服务启动失败并检查日志": "terminal",
            "调研 BFCL 并比较公开基准": "research",
            "规划多 Agent 工作流和里程碑": "planning",
            "撰写项目架构说明文档": "writing",
            "设计": "planning",
            "分析": "research",
        }
        for task, expected in cases.items():
            with self.subTest(task=task):
                self.assertEqual(infer_domain(task), expected)

    def test_parses_swebench_machine_readable_verified_board(self) -> None:
        payload = [{"name": "Verified", "results": [{
            "agent": "mini-agent", "model_display": "Model X", "name": "model-x-high",
            "resolved": 72.5, "date": "2026-08-01", "warning": None,
        }]}]
        page = '<script type="application/json" id="leaderboard-data">' + json.dumps(payload) + "</script>"
        records = _parse_swebench("swebench_verified", page, self.now.isoformat())
        self.assertEqual(records[0]["score"], 0.725)
        self.assertEqual(records[0]["model"], "Model X")
        self.assertEqual(records[0]["metadata"]["agent"], "mini-agent")

    def test_parses_aider_table(self) -> None:
        page = """<table><tr><th>Model</th><th>Percent completed correctly</th></tr>
        <tr><td>model-x</td><td>84.2%</td><td>aider --model model-x</td></tr></table>"""
        records = _parse_aider("aider_edit", page, self.now.isoformat())
        self.assertEqual(records[0]["score"], 0.842)
        self.assertEqual(records[0]["model"], "model-x")
        self.assertEqual(records[0]["evaluated_agent"], "Aider")

    def test_parses_terminal_bench_verified_rows(self) -> None:
        rows = [{
            "rank": 1,
            "metadata": {
                "date": "2026-08-01",
                "agent_display": {"label": "Codex", "url": "https://example/agent"},
                "model_display": {"label": "Model X", "url": "https://example/model"},
                "reasoning_effort": "high",
                "pr_url": {"url": "https://example/verification"},
            },
            "metrics": {"accuracy": 81.5, "n_trials": 445, "accuracy_stderr": 1.2},
        }]
        decoded = 'prefix "rows":' + json.dumps(rows) + ',"footer":{}'
        encoded = json.dumps(decoded)[1:-1]
        page = f'<script>self.__next_f.push([1,"{encoded}"])</script>'
        records = _parse_terminal_bench("terminal_bench_2_1", page, self.now.isoformat())
        self.assertEqual(records[0]["score"], 0.815)
        self.assertEqual(records[0]["evaluated_agent"], "Codex")
        self.assertEqual(records[0]["metadata"]["n_trials"], 445)
        self.assertEqual(records[0]["reasoning_effort"], "high")
        self.assertEqual(records[0]["sample_count"], 445)

    def test_model_aliases_do_not_create_random_word_identities(self) -> None:
        aliases = _model_aliases("gemini-exp-1206 (whole)")
        self.assertIn("geminiexp1206whole", aliases)
        self.assertIn("geminiexp1206", aliases)
        self.assertNotIn("whole", aliases)

    def test_fresh_concrete_model_matches_but_brand_does_not(self) -> None:
        save_snapshot(self.store, self._snapshot())
        matched = external_baseline(
            self.store,
            model_identity={"model": "model-x", "source": "user_binding", "aliases": ["modelx"]},
        )
        self.assertEqual(matched["status"], "fresh")
        self.assertEqual(matched["match_strength"], "model-only")
        self.assertEqual(matched["score"], 0.63)  # weak model-only prior shrinks toward 0.5
        unmatched = external_baseline(
            self.store,
            model_identity={"model": "Agent Brand", "source": "brand", "aliases": ["agentbrand"]},
        )
        self.assertIsNone(unmatched["score"])
        self.assertIn("no result matches", unmatched["reason"])

    def test_documented_model_family_is_weakly_matchable(self) -> None:
        self.assertEqual(_model_family("gpt-5.6-sol"), "gpt56")
        self.assertEqual(_model_family("GPT-5.6 Luna"), "gpt56")
        self.assertIsNone(_model_family("gpt-5.5"))
        snapshot = self._snapshot(model="GPT-5.6 Luna", score=0.8)
        snapshot["records"][0]["evaluated_agent"] = "Codex"
        snapshot["records"][0]["agent_aliases"] = ["codex"]
        save_snapshot(self.store, snapshot)
        baseline = external_baseline(
            self.store,
            model_identity={"model": "gpt-5.6-sol", "aliases": ["gpt56sol"]},
            agent_id="codex",
            allow_model_family=True,
        )
        self.assertEqual(baseline["match_strength"], "agent+model-family")
        self.assertEqual(baseline["records"][0]["match_kind"], "agent+model-family")
        self.assertLess(baseline["score"], 0.8)
        strict = external_baseline(
            self.store,
            model_identity={"model": "gpt-5.6-sol", "aliases": ["gpt56sol"]},
            agent_id="codex",
        )
        self.assertEqual(strict["status"], "unavailable")
        self.assertIsNone(strict["score"])

    def test_legacy_snapshot_is_read_compatibly(self) -> None:
        snapshot = self._snapshot()
        snapshot["schema_version"] = "icle-external-evaluation-snapshot/v0.1"
        save_snapshot(self.store, snapshot)
        baseline = external_baseline(
            self.store,
            model_identity={"model": "model-x", "source": "binding", "aliases": ["modelx"]},
        )
        self.assertEqual(baseline["status"], "fresh")
        self.assertEqual(baseline["records"][0]["license"], "MIT")

    def test_duplicate_evaluation_records_are_rejected(self) -> None:
        snapshot = self._snapshot()
        snapshot["records"].append(dict(snapshot["records"][0]))
        with self.assertRaises(ExternalEvaluationError):
            save_snapshot(self.store, snapshot)

    def test_dataset_quality_reports_date_and_license_coverage(self) -> None:
        save_snapshot(self.store, self._snapshot())
        report = dataset_quality(self.store, now=self.now)
        self.assertEqual(report["invalid_snapshot_files"], 0)
        source = report["sources"][0]
        self.assertEqual(source["current_records"], 1)
        self.assertEqual(source["result_date_coverage"], 1.0)
        self.assertTrue(source["license_complete"])
        self.assertEqual(source["quality_status"], "healthy")

    def test_stale_baseline_is_not_scored(self) -> None:
        save_snapshot(self.store, self._snapshot(stale=True))
        baseline = external_baseline(
            self.store,
            model_identity={"model": "model-x", "source": "binding", "aliases": ["modelx"]},
        )
        self.assertEqual(baseline["status"], "stale")
        self.assertIsNone(baseline["score"])

    def test_local_evidence_gradually_overrides_external_prior(self) -> None:
        save_snapshot(self.store, self._snapshot(score=0.6))
        (self.store / "agent-models.json").write_text(
            json.dumps({"agent-a": {"model": "model-x", "provider": "p"}}), encoding="utf-8"
        )
        combined = rank_agent(
            self.store,
            agent_id="agent-a",
            local={"score": 1.0, "outcomes": 5, "pairwise": 0},
        )
        self.assertEqual(combined["score_source"], "external+local")
        self.assertGreater(combined["score"], 0.6)
        self.assertLess(combined["score"], 1.0)

    def test_refresh_failure_retains_existing_cache(self) -> None:
        save_snapshot(self.store, self._snapshot())

        def fail(_url: str) -> str:
            raise OSError("offline")

        result = refresh_snapshots(
            self.store, source_ids=["swebench_verified"], fetcher=fail
        )
        self.assertFalse(result["sources"][0]["ok"])
        self.assertEqual(result["snapshots"], 1)


if __name__ == "__main__":
    unittest.main()
