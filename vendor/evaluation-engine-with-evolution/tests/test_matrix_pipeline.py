from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.c_state import CStateStore
from experience_evaluation.matrix_pipeline import (
    BuiltinDatasetRegistry,
    run_matrix_pipeline,
    write_pipeline_bundle,
)
from experience_evaluation.profile import render_subject_profile
from experience_evaluation.public_dataset import DatasetError


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


class MatrixPipelineTests(unittest.TestCase):
    def _registry(self, root: Path) -> BuiltinDatasetRegistry:
        snapshot = root / "matrices" / "2026-01-01"
        _write_jsonl(snapshot / "A_models/model_catalog.jsonl", [
            {"model_id":"provider-model","display_name":"Model","provider":"provider"}
        ])
        _write_jsonl(snapshot / "A_models/source_model_configs.jsonl", [
            {"source_model_id":"model-rev","normalized_model_config_id":"provider-model-rev","identity_status":"source_exact"}
        ])
        _write_jsonl(snapshot / "A_models/model_benchmark_observations.jsonl", [])
        _write_jsonl(snapshot / "A_models/capability_baselines.jsonl", [
            {"baseline_id":"a-r","model_config_id":"provider-model-rev","dimension_id":"reasoning","value":0.4,"stderr":0.05,"scale_id":"normalized_0_1","calibration_status":"calibrated_for_c"}
        ])
        _write_jsonl(snapshot / "B_harnesses/harness_catalog.jsonl", [
            {"harness_id":"harness-one","display_name":"Harness One"}
        ])
        _write_jsonl(snapshot / "B_harnesses/capability_baselines.jsonl", [
            {"baseline_id":"b-r","harness_id":"harness-one","dimension_id":"reasoning","value":0.1,"stderr":0.02,"scale_id":"canonical_logit/v0.1","scale_role":"additive_effect","calibration_status":"calibrated_for_c","evidence_tier":"family_observation"}
        ])
        _write_json(snapshot / "B_harnesses/exact_anchor_report.json", {"exact_anchor_blocks":2,"anchor_status":"available"})
        _write_jsonl(snapshot / "A_models/current/source_model_configs.jsonl", [
            {"source_model_id":"model-rev","normalized_model_config_id":"provider-model-rev","identity_status":"source_exact","freshness_status":"active_current"}
        ])
        _write_jsonl(snapshot / "A_models/current/model_benchmark_observations.jsonl", [])
        _write_jsonl(snapshot / "A_models/current/capability_baselines.jsonl", [
            {"baseline_id":"a-r","model_config_id":"provider-model-rev","dimension_id":"reasoning","value":0.4,"stderr":0.05,"scale_id":"normalized_0_1","calibration_status":"calibrated_for_c","freshness_status":"active_current"}
        ])
        _write_jsonl(snapshot / "B_harnesses/current/capability_baselines.jsonl", [
            {"baseline_id":"b-r","harness_id":"harness-one","dimension_id":"reasoning","value":0.1,"stderr":0.02,"scale_id":"canonical_logit/v0.1","scale_role":"additive_effect","calibration_status":"calibrated_for_c","evidence_tier":"family_observation","freshness_status":"active_current"}
        ])
        _write_json(snapshot / "freshness_summary.json", {"policy_id":"test/v1","current_view_ready":True})
        _write_json(snapshot / "coverage.json", {"as_of":"2026-01-01","A":{},"B":{}})
        files = {}
        for item in sorted(snapshot.rglob("*")):
            if item.is_file() and item.name != "manifest.json":
                files[str(item.relative_to(snapshot))] = hashlib.sha256(item.read_bytes()).hexdigest()
        _write_json(snapshot / "manifest.json", {"schema_version":"test-manifest","as_of":"2026-01-01","files":files})
        return BuiltinDatasetRegistry(root / "matrices")

    def test_registry_discovers_latest_snapshot_and_verifies_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            snapshot = registry.latest()
            self.assertEqual(snapshot.as_of, "2026-01-01")
            self.assertTrue(snapshot.integrity_verified)
            (snapshot.root / "coverage.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(DatasetError):
                registry.open("2026-01-01")

    def test_identity_resolution_is_exact_or_explicitly_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = self._registry(Path(tmp)).latest()
            resolved = snapshot.resolve_identity({
                "subject_id":"agent","model_revision":"model-rev","provider":"provider",
                "harness":"Harness One","harness_revision":"1.0"
            })
            self.assertEqual(resolved["model"]["match_tier"], "exact_dataset_config")
            self.assertEqual(resolved["model"]["model_config_id"], "provider-model-rev")
            self.assertEqual(resolved["harness"]["match_tier"], "catalog_identity_only")
            self.assertEqual(resolved["harness"]["version_policy"], "ignored_for_matching")
            unknown = snapshot.resolve_identity({"subject_id":"x","model":"invented","harness":"invented"})
            self.assertEqual(unknown["model"]["match_tier"], "unresolved")
            self.assertEqual(unknown["harness"]["match_tier"], "generic_default")

    def test_registry_defaults_to_current_view_and_history_requires_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = self._registry(root)
            snapshot = registry.latest()
            current_unknown = snapshot.resolve_identity({
                "subject_id":"old","model_revision":"legacy-rev","provider":"provider"
            })
            self.assertEqual(current_unknown["model"]["match_tier"], "unresolved")
            historical = BuiltinDatasetRegistry(root / "matrices", include_history=True).latest()
            self.assertEqual(historical.data_view, "history_inclusive")

    def test_pipeline_connects_j_and_infers_c_only_on_calibrated_same_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            tasks = [
                {"task_id":"t1","task_revision":"r1","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]},
                {"task_id":"t2","task_revision":"r1","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]},
            ]
            results = [
                {"result_id":"r1","subject_id":"agent","task_id":"t1","task_revision":"r1","status":"completed","metrics":{"score":1}},
                {"result_id":"r2","subject_id":"agent","task_id":"t2","task_revision":"r1","status":"completed","metrics":{"score":0.8}},
            ]
            config = {
                "generator": {"dimensions":["reasoning"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":200,"bootstrap_seed":4,"min_coverage":1,"comparability_verified":True,"comparison_contract_id":"controlled-v1"},
                "calibration_contract": {"contract_id":"same-scale-v1","verified":True,"scale_id":"canonical_logit/v0.1","independence_assumption":True},
            }
            pipeline = run_matrix_pipeline(
                registry=registry,
                task_specs=tasks,
                execution_results=results,
                subject_identities=[{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}],
                pipeline_config=config,
            )
            c = pipeline["c_matrix"][0]
            self.assertEqual(c["status"], "available")
            b_cell = pipeline["b_lookups"]["agent"]["cells"][0]
            expected = math.log(0.9 / 0.1) - math.log(0.4 / 0.6) - b_cell["canonical_value"]
            self.assertAlmostEqual(c["value"], expected)
            j = pipeline["canonical_j_matrix"][0]
            a_se = 0.05 / (0.4 * 0.6)
            self.assertAlmostEqual(c["stderr"], (j["canonical_stderr"] ** 2 + a_se ** 2 + b_cell["canonical_stderr"] ** 2) ** 0.5)
            self.assertEqual(j["axis_contract_id"], "livebench-capability-seven/v0.1")
            self.assertEqual(j["canonical_scale_id"], "canonical_logit/v0.1")
            self.assertIn("b_specialized_with_family_data", c["warnings"])

            no_contract = dict(config)
            no_contract["calibration_contract"] = None
            unavailable = run_matrix_pipeline(
                registry=registry,
                task_specs=tasks,
                execution_results=results,
                subject_identities=[{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}],
                pipeline_config=no_contract,
            )["c_matrix"][0]
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertIn("calibration_contract_missing", unavailable["reasons"])

    def test_audit_artifacts_may_be_absent_but_not_tampered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "matrices" / "2026-01-01"
            self._registry(root)
            dump = snapshot_root / "B_harnesses/rejected_comparisons.jsonl"
            _write_jsonl(dump, [{"reason": "label_mismatch"}])
            manifest = json.loads((snapshot_root / "manifest.json").read_text(encoding="utf-8"))
            manifest["audit_artifacts"] = {
                "B_harnesses/rejected_comparisons.jsonl": {
                    "sha256": hashlib.sha256(dump.read_bytes()).hexdigest(),
                    "distribution": "rebuild_only_not_in_git",
                }
            }
            _write_json(snapshot_root / "manifest.json", manifest)
            snapshot = BuiltinDatasetRegistry(root / "matrices").latest()
            self.assertEqual(
                snapshot.audit_artifacts["B_harnesses/rejected_comparisons.jsonl"],
                "present_verified",
            )
            dump.write_text('{"reason":"edited"}\n', encoding="utf-8")
            with self.assertRaises(DatasetError):
                BuiltinDatasetRegistry(root / "matrices").latest()
            dump.unlink()
            absent = BuiltinDatasetRegistry(root / "matrices").latest()
            self.assertEqual(
                absent.audit_artifacts["B_harnesses/rejected_comparisons.jsonl"],
                "not_distributed",
            )

    def test_shipped_snapshot_records_undistributed_audit_artifacts(self) -> None:
        project = Path(__file__).resolve().parents[1]
        snapshot = BuiltinDatasetRegistry(project / "data/matrices").latest()
        self.assertTrue(snapshot.audit_artifacts)
        self.assertLessEqual(
            set(snapshot.audit_artifacts.values()), {"present_verified", "not_distributed"}
        )
        self.assertEqual(
            snapshot.audit_artifacts["B_harnesses/rejected_comparisons.jsonl"],
            "not_distributed",
        )

    def test_negative_residual_is_published_not_clipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            tasks = [
                {"task_id":"t1","task_revision":"r1","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]},
                {"task_id":"t2","task_revision":"r1","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]},
            ]
            results = [
                {"result_id":"r1","subject_id":"agent","task_id":"t1","task_revision":"r1","status":"completed","metrics":{"score":0.2}},
                {"result_id":"r2","subject_id":"agent","task_id":"t2","task_revision":"r1","status":"completed","metrics":{"score":0.1}},
            ]
            config = {
                "generator": {"dimensions":["reasoning"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":200,"bootstrap_seed":4,"min_coverage":1,"comparability_verified":True,"comparison_contract_id":"controlled-v1"},
                "calibration_contract": {"contract_id":"same-scale-v1","verified":True,"scale_id":"canonical_logit/v0.1","independence_assumption":True},
            }
            pipeline = run_matrix_pipeline(
                registry=registry,
                task_specs=tasks,
                execution_results=results,
                subject_identities=[{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}],
                pipeline_config=config,
            )
            c = pipeline["c_matrix"][0]
            b_cell = pipeline["b_lookups"]["agent"]["cells"][0]
            expected = math.log(0.15 / 0.85) - math.log(0.4 / 0.6) - b_cell["canonical_value"]
            self.assertEqual(c["status"], "available")
            self.assertLess(expected, 0)
            self.assertAlmostEqual(c["value"], expected)
            self.assertAlmostEqual(c["canonical_value"], expected)

    def test_reference_a_path_covers_every_livebench_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "matrices" / "2026-01-01"
            self._registry(root)
            _write_jsonl(snapshot_root / "A_models/current/capability_baselines.jsonl", [])
            categories = {
                "Reasoning": "reasoning",
                "Coding": "coding",
                "Agentic Coding": "agentic_coding",
                "Mathematics": "mathematics",
                "Data Analysis": "data_analysis",
                "Language": "language",
                "IF": "instruction_following",
            }
            observations = []
            for index, category in enumerate(categories):
                for item in range(2):
                    observations.append({
                        "record_id": f"obs-{index}-{item}",
                        "model_id": "provider-model-rev",
                        "category": category,
                        "score": 50 + item,
                        "score_status": "observed",
                    })
            _write_jsonl(snapshot_root / "A_models/current/model_benchmark_observations.jsonl", observations)
            files = {}
            for item in sorted(snapshot_root.rglob("*")):
                if item.is_file() and item.name != "manifest.json":
                    files[str(item.relative_to(snapshot_root))] = hashlib.sha256(item.read_bytes()).hexdigest()
            _write_json(snapshot_root / "manifest.json", {"schema_version":"test-manifest","as_of":"2026-01-01","files":files})
            snapshot = BuiltinDatasetRegistry(root / "matrices").latest()
            a = snapshot.lookup_a(
                {"match_tier": "exact_dataset_config", "model_config_id": "provider-model-rev"},
                list(categories.values()),
            )
            self.assertEqual(a["status"], "reference_only")
            self.assertEqual(
                {row["dimension_id"] for row in a["cells"]}, set(categories.values())
            )
            self.assertIn("not_calibrated_for_c", a["reasons"])

    def test_incomplete_stable_family_prior_falls_back_per_axis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "matrices" / "2026-01-01"
            self._registry(root)
            _write_jsonl(snapshot_root / "B_harnesses/stable/agent_families.jsonl", [
                {"agent_id":"harness-one","display_name":"Harness One","status":"stable"}
            ])
            _write_jsonl(snapshot_root / "B_harnesses/stable/capability_baselines.jsonl", [
                {
                    "baseline_id":"stable-reasoning","agent_id":"harness-one","dimension_id":"reasoning",
                    "value":0.3,"stderr":0.1,"scale_id":"canonical_logit/v0.1",
                    "scale_role":"additive_effect","calibration_status":"document_specialized_prior_for_c",
                }
            ])
            files = {}
            for item in sorted(snapshot_root.rglob("*")):
                if item.is_file() and item.name != "manifest.json":
                    files[str(item.relative_to(snapshot_root))] = hashlib.sha256(item.read_bytes()).hexdigest()
            _write_json(snapshot_root / "manifest.json", {"schema_version":"test-manifest","as_of":"2026-01-01","files":files})
            snapshot = BuiltinDatasetRegistry(root / "matrices").latest()
            b = snapshot.lookup_b(
                {"harness_id": "harness-one", "match_tier": "stable_family_id"},
                ["reasoning", "coding"],
            )
            self.assertEqual({row["dimension_id"] for row in b["cells"]}, {"reasoning", "coding"})
            self.assertEqual(b["incomplete_family_prior_dimensions"], ["coding"])
            self.assertIn("incomplete_family_prior_dimension:coding", b["reasons"])

    def test_builtin_current_snapshot_auto_resolves_but_refuses_unavailable_b(self) -> None:
        project = Path(__file__).resolve().parents[1]
        snapshot = BuiltinDatasetRegistry(project / "data/matrices").latest()
        identity = snapshot.resolve_identity({
            "subject_id":"demo",
            "model_revision":"gpt-5-6-terra-max",
            "provider":"openai",
            "harness":"Claude Code",
            "harness_revision":"2.0.31",
        })
        self.assertEqual(identity["model"]["match_tier"], "exact_dataset_config")
        self.assertEqual(identity["harness"]["match_tier"], "stable_family_id")
        a = snapshot.lookup_a(identity["model"], ["reasoning", "coding", "agentic_coding", "mathematics"])
        self.assertIn(a["status"], {"available", "partial"})
        a_status = {row["dimension_id"]: row["calibration_status"] for row in a["cells"]}
        self.assertEqual(a_status.get("reasoning"), "calibrated_for_c")
        self.assertEqual(a_status.get("coding"), "calibrated_for_c")
        self.assertEqual(a_status.get("agentic_coding"), "calibrated_for_c")
        self.assertEqual(a_status.get("mathematics"), "calibrated_for_c")
        b = snapshot.lookup_b(identity["harness"], ["reasoning"])
        self.assertEqual(b["status"], "agent_specialized_prior")
        self.assertGreater(b["cells"][0]["canonical_value"], 0.15)
        self.assertIn("no_family_observation_using_agent_specialized_prior", b["reasons"])

        openclaw = snapshot.resolve_identity({"subject_id":"claw","harness":"Clawdbot","harness_version":"any"})
        self.assertEqual(openclaw["harness"]["agent_id"], "openclaw")
        self.assertEqual(openclaw["harness"]["match_tier"], "stable_family_alias")

    def test_local_usage_emits_c_only_for_calibrated_a_axes(self) -> None:
        project = Path(__file__).resolve().parents[1]
        tasks = [
            json.loads(line)
            for line in (project / "examples/matrix_generator/task_specs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results = [
            json.loads(line)
            for line in (project / "examples/matrix_generator/execution_results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        identities = [
            json.loads(line)
            for line in (project / "examples/matrix_pipeline/subject_identities.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        config = json.loads(
            (project / "examples/matrix_pipeline/local_usage_config.json").read_text(encoding="utf-8")
        )
        config["generator"]["bootstrap_replicates"] = 0
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(project / "data/matrices"),
            task_specs=tasks,
            execution_results=results,
            subject_identities=identities,
            pipeline_config=config,
        )
        by_key = {
            (row["subject_id"], row["dimension_id"]): row for row in pipeline["c_matrix"]
        }
        available = [row for row in pipeline["c_matrix"] if row["status"] == "available"]
        self.assertGreaterEqual(len(available), 14)
        for subject in ("agent-alpha", "agent-beta"):
            for dimension in (
                "reasoning",
                "coding",
                "agentic_coding",
                "mathematics",
                "data_analysis",
                "language",
                "instruction_following",
            ):
                cell = by_key[(subject, dimension)]
                self.assertEqual(cell["status"], "available")
                self.assertIsNotNone(cell["canonical_value"])
                self.assertIn("b_agent_specialized_prior_used", cell["warnings"])

    def test_family_observation_specializes_without_version_and_provisional_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_root = root / "matrices" / "2026-01-01"
            registry = self._registry(root)
            current = snapshot_root / "B_harnesses/current/capability_baselines.jsonl"
            _write_jsonl(current, [
                {
                    "baseline_id":"family","harness_id":"harness-one","dimension_id":"reasoning",
                    "value":0.4,"stderr":0.12,"scale_id":"canonical_logit/v0.1",
                    "scale_role":"additive_effect","calibration_status":"calibrated_for_c",
                    "evidence_tier":"family_observation",
                },
                {
                    "baseline_id":"junk","harness_id":"harness-one","dimension_id":"reasoning",
                    "value":0.9,"stderr":0.01,"scale_id":"canonical_logit/v0.1",
                    "scale_role":"additive_effect","calibration_status":"calibrated_for_c",
                    "evidence_tier":"provisional_same_label_blocks",
                },
            ])
            files = {}
            for item in sorted(snapshot_root.rglob("*")):
                if item.is_file() and item.name != "manifest.json":
                    files[str(item.relative_to(snapshot_root))] = hashlib.sha256(item.read_bytes()).hexdigest()
            _write_json(snapshot_root / "manifest.json", {"schema_version":"test-manifest","as_of":"2026-01-01","files":files})
            snapshot = BuiltinDatasetRegistry(root / "matrices").latest()
            resolved = snapshot.resolve_identity({
                "subject_id":"agent","harness":"Harness One","harness_revision":"999.0",
            })
            self.assertEqual(resolved["harness"]["version_policy"], "ignored_for_matching")
            b = snapshot.lookup_b(resolved["harness"], ["reasoning"])
            self.assertEqual(b["status"], "specialized")
            self.assertEqual(b["cells"][0]["observed_baseline_id"], "family")
            self.assertNotEqual(b["cells"][0]["canonical_value"], 0.9)

    def test_pipeline_rejects_tampered_j_canonical_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            tasks = [{"task_id":"t","task_revision":"r","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]}]
            results = [{"result_id":"r","subject_id":"agent","task_id":"t","task_revision":"r","status":"completed","metrics":{"score":1}}]
            config = {"generator":{"dimensions":["reasoning"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":0,"comparability_verified":True,"comparison_contract_id":"c"}}
            original = run_matrix_pipeline(
                registry=registry, task_specs=tasks, execution_results=results,
                subject_identities=[{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}],
                pipeline_config=config,
            )
            original["j_bundle"]["evaluation_matrix"][0]["canonical_value"] = 123
            with self.assertRaises(DatasetError):
                from experience_evaluation.matrix_pipeline import _align_j_cell
                _align_j_cell(original["j_bundle"]["evaluation_matrix"][0])

    def test_c_observation_id_changes_when_j_observation_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            base_task = {"task_revision":"r","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]}
            tasks = [{"task_id":"t1",**base_task},{"task_id":"t2",**base_task}]
            identities = [{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}]
            config = {"generator":{"dimensions":["reasoning"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":0,"comparability_verified":True,"comparison_contract_id":"c"},"calibration_contract":{"contract_id":"cal","verified":True,"scale_id":"canonical_logit/v0.1","independence_assumption":True}}
            first = run_matrix_pipeline(registry=registry, task_specs=tasks, execution_results=[
                {"result_id":"r1","subject_id":"agent","task_id":"t1","task_revision":"r","status":"completed","metrics":{"score":1}},
                {"result_id":"r2","subject_id":"agent","task_id":"t2","task_revision":"r","status":"completed","metrics":{"score":0.8}},
            ], subject_identities=identities, pipeline_config=config)
            second = run_matrix_pipeline(registry=registry, task_specs=tasks, execution_results=[
                {"result_id":"r1","subject_id":"agent","task_id":"t1","task_revision":"r","status":"completed","metrics":{"score":0.2}},
                {"result_id":"r2","subject_id":"agent","task_id":"t2","task_revision":"r","status":"completed","metrics":{"score":0.4}},
            ], subject_identities=identities, pipeline_config=config)
            self.assertNotEqual(first["c_matrix"][0]["cell_id"], second["c_matrix"][0]["cell_id"])

    def test_local_probe_makes_seven_axis_c_and_publishable_portrait(self) -> None:
        project = Path(__file__).resolve().parents[1]
        tasks = [
            json.loads(line)
            for line in (project / "examples/local_usage/task_specs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        results = [
            json.loads(line)
            for line in (project / "examples/local_usage/execution_results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        identities = [
            {"subject_id":"model-baseline","role":"baseline_model","model_revision":"model-rev","provider":"provider"},
            {"subject_id":"agent-alpha","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"},
            {"subject_id":"agent-beta","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"},
        ]
        config = json.loads((project / "examples/local_usage/pipeline_config.json").read_text(encoding="utf-8"))
        config["generator"]["bootstrap_replicates"] = 20
        with tempfile.TemporaryDirectory() as tmp:
            registry = self._registry(Path(tmp))
            pipeline = run_matrix_pipeline(
                registry=registry,
                task_specs=tasks,
                execution_results=results,
                subject_identities=identities,
                pipeline_config=config,
            )
        self.assertEqual(pipeline["local_probe_a"]["cell_count"], 7)
        self.assertEqual(pipeline["summary"]["c_available_cells"], 14)
        self.assertEqual(pipeline["summary"]["c_unavailable_cells"], 0)
        by_key = {(row["subject_id"], row["dimension_id"]): row for row in pipeline["c_matrix"]}
        for subject in ("agent-alpha", "agent-beta"):
            for dimension in (
                "reasoning",
                "coding",
                "agentic_coding",
                "mathematics",
                "data_analysis",
                "language",
                "instruction_following",
            ):
                cell = by_key[(subject, dimension)]
                self.assertEqual(cell["status"], "available")
                self.assertIsNotNone(cell["canonical_value"])
                self.assertIsNotNone(cell["canonical_stderr"])
                self.assertEqual(
                    pipeline["a_lookups"][subject]["a_sources"][dimension],
                    "local-probe-same-taskspec/v0.1",
                )
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), json.loads((project / "config/c_state_config.json").read_text(encoding="utf-8")))
            update = store.update(
                pipeline["c_matrix"],
                observed_at="2026-08-20T12:00:00+00:00",
                source_bundle_id="local-usage-test",
            )
            self.assertEqual(update["updated"], 14)
            policy = json.loads((project / "config/profile_publication_policy.json").read_text(encoding="utf-8"))
            portrait = render_subject_profile(store, "agent-alpha", policy)
            self.assertEqual(portrait["publication_status"], "publishable_local_portrait")
            self.assertEqual(portrait["observed_axis_count"], 7)
            self.assertFalse(portrait["ranking"])
            self.assertNotIn("total_score", portrait)

    def test_pipeline_bundle_is_hash_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry = self._registry(root)
            tasks = [{"task_id":"t","task_revision":"r","weight":1,"capability_loadings":{"reasoning":1},"metrics":[{"metric_id":"score","min":0,"max":1,"direction":"maximize"}]}]
            results = [{"result_id":"r","subject_id":"agent","task_id":"t","task_revision":"r","status":"completed","metrics":{"score":1}}]
            config = {"generator":{"dimensions":["reasoning"],"axis_contract_id":"livebench-capability-seven/v0.1","source_scale_id":"normalized_0_1","canonical_scale_id":"canonical_logit/v0.1","bootstrap_replicates":0,"comparability_verified":True,"comparison_contract_id":"c"}}
            pipeline = run_matrix_pipeline(
                registry=registry,task_specs=tasks,execution_results=results,
                subject_identities=[{"subject_id":"agent","model_revision":"model-rev","provider":"provider","harness":"Harness One","harness_revision":"1.0"}],
                pipeline_config=config,
            )
            output = root / "bundle"
            manifest = write_pipeline_bundle(output, pipeline, pipeline_config=config)
            self.assertTrue((output / "c_matrix.jsonl").exists())
            self.assertTrue((output / "identity_resolution.jsonl").exists())
            for name, expected in manifest["files"].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), expected)


if __name__ == "__main__":
    unittest.main()
