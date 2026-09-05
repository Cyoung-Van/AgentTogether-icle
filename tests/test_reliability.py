"""Regression coverage for the 8f686dc reliability audit; no paid models."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from icle import task, provider
from icle.intelligence import OpenAICompatibleProvider, IntelligenceError
from icle.ledger import ExperienceLedger, LedgerError
from test_ledger import revision


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_external_http_and_credentials_rejected_at_both_entrypoints(self):
        for url in ('http://localhost.example.invalid/v1', 'http://127.0.0.1.example.invalid',
                    'http://localhost@outside.invalid', 'https://user:pass@example.invalid',
                    'https://', 'https://example.invalid:bad', 'https://example.invalid/\nfoo'):
            with self.subTest(url=url):
                config = provider.default_provider_config(display_name='test', provider_type='openai-compatible', base_url=url)
                with self.assertRaises(provider.ProviderError):
                    provider.validate_provider_config(config)
                with self.assertRaises(IntelligenceError):
                    OpenAICompatibleProvider(url, 'synthetic', 'fake')

    def test_ipv6_loopback_allowed(self):
        config = provider.default_provider_config(display_name='test', provider_type='local', base_url='http://[::1]:1234/v1')
        provider.validate_provider_config(config)
        OpenAICompatibleProvider(config['base_url'], '', 'fake')

    def test_ledger_ownership_and_extra_metadata_are_protected(self):
        for field, value in [('agent_revision', revision('other')), ('episode_id', 'other'),
                             ('extra_metadata', {'changed': True}), ('hash_version', 1)]:
            with self.subTest(field=field):
                ledger = ExperienceLedger(self.root / field)
                saved = ledger.append(ledger.new_record(record_id='r1', kind='episode', agent_revision=revision(), episode_id='original', payload='data'))
                saved[field] = value
                ledger.path.write_text(json.dumps(saved) + '\n')
                with self.assertRaises(LedgerError):
                    ExperienceLedger(ledger.root)

    def test_insert_and_reorder_preserve_dependency_identity(self):
        created = task.create_task(self.root, title='test')
        saved = task.save_plan(self.root, created['task_id'], strategy='DECOMPOSE', steps=[
            {'title': 'build', 'description': 'build'},
            {'title': 'test', 'description': 'test', 'depends_on': ['S1']},
        ])
        build, test = saved['plan']['steps']
        edited = task.update_plan_steps(self.root, created['task_id'], steps=[
            {'title': 'prepare', 'description': 'prepare'}, test, build])
        by_title = {s['title']: s for s in edited['plan']['steps']}
        self.assertEqual(by_title['build']['step_id'], build['step_id'])
        self.assertEqual(by_title['test']['depends_on'], [by_title['build']['step_id']])
        self.assertNotEqual(by_title['prepare']['step_id'], build['step_id'])

    def test_handoff_contains_changed_artifact_content(self):
        for policy in ('ARTIFACT_ONLY', 'PROJECT_STATE'):
            step = {'context_policy': policy}
            (self.root / 'output.md').write_text('author result alpha')
            first = task._step_prompt({'description': 'review', 'title': 'review'}, step, self.root)
            (self.root / 'output.md').write_text('author result beta')
            second = task._step_prompt({'description': 'review', 'title': 'review'}, step, self.root)
            self.assertIn('author result alpha', first)
            self.assertIn('author result beta', second)
            self.assertNotEqual(first, second)

    def test_run_readback_uses_execution_order_and_archives_full_output(self):
        created = task.create_task(self.root, title='test')
        # An inverse dependency forces S2 before S1, irrespective of numbering.
        task.save_plan(self.root, created['task_id'], strategy='DECOMPOSE', steps=[
            {'title': 'last', 'description': 'last', 'recommended_agent': 'fake', 'depends_on': ['S2']},
            {'title': 'first', 'description': 'first', 'recommended_agent': 'fake'},
        ])
        task.approve_plan(self.root, created['task_id'])
        output = 'COMPLETE RESULT\n' + 'x' * 5000
        with patch('icle.task._settle_step_cost', return_value=None):
            run = task.run_task(self.root, created['task_id'], executor_factory=lambda _: lambda *args: ('completed', output, '', 0))
        reread = task.latest_run(self.root, created['task_id'])
        self.assertEqual([s['step_id'] for s in reread['steps']], ['S2', 'S1'])
        self.assertEqual(reread['final_step_id'], 'S1')
        for step in reread['steps']:
            self.assertEqual((Path(run['run_dir']) / step['stdout_ref']).read_text(), output)
        self.assertEqual(reread['workspace_init'], run['workspace_init'])

    def test_workspace_excludes_sensitive_files_and_reports_exclusions(self):
        project = self.root / 'project'
        project.mkdir()
        (project / '.env').write_text('SYNTHETIC_SECRET=example')
        (project / '.env.example').write_text('KEY=')
        (project / 'source.py').write_text('pass')
        workspace = self.root / 'workspace'
        result = task._init_workspace(str(project), workspace)
        self.assertFalse((workspace / '.env').exists())
        self.assertTrue((workspace / 'source.py').exists())
        self.assertIn('.env', result['excluded_files'])

    def test_stale_task_save_is_rejected(self):
        created = task.create_task(self.root, title="original")
        stale = task.show_task(self.root, created["task_id"])
        task.update_task(self.root, created["task_id"], title="new title")
        stale["description"] = "stale update"
        with self.assertRaises(task.TaskError):
            task._save(self.root, stale)
        self.assertEqual(task.show_task(self.root, created["task_id"])["title"], "new title")

    def test_concurrent_provider_saves_keep_both_configs_and_secrets(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import time
        gate = threading.Barrier(2)
        original_load = provider._load_configs
        def delayed_load(store):
            result = original_load(store)
            time.sleep(0.03)
            return result
        def save(index):
            config = provider.default_provider_config(display_name=str(index), provider_type="local", base_url="http://localhost:1234")
            gate.wait(timeout=5)
            return provider.save_provider(self.root, config, api_key=f"synthetic-{index}")
        with patch("icle.provider._load_configs", side_effect=delayed_load), ThreadPoolExecutor(2) as pool:
            saved = list(pool.map(save, range(2)))
        self.assertEqual(len(provider.list_providers(self.root)), 2)
        for index, config in enumerate(saved):
            self.assertEqual(provider.resolve_secret(self.root, config["provider_id"]), f"synthetic-{index}")

    def test_running_task_metadata_cannot_replace_executor_state(self):
        created = task.create_task(self.root, title="original")
        task.save_plan(self.root, created["task_id"], strategy="DIRECT", steps=[
            {"title": "run", "description": "run", "recommended_agent": "fake"}])
        task.approve_plan(self.root, created["task_id"])
        def executor(*args):
            with self.assertRaises(task.TaskError):
                task.update_task(self.root, created["task_id"], title="concurrent edit")
            return "completed", "done", "", 0
        with patch("icle.task._settle_step_cost", return_value=None):
            run = task.run_task(self.root, created["task_id"], executor_factory=lambda _: executor)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(task.show_task(self.root, created["task_id"])["title"], "original")

    def test_git_snapshot_contains_uncommitted_and_untracked_content(self):
        import subprocess
        project = self.root / "repo"
        project.mkdir()
        def git(*args):
            return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=True).stdout.strip()
        git("init", "-q")
        (project / "code.txt").write_text("committed")
        git("add", "code.txt")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        head = git("rev-parse", "HEAD")
        (project / "code.txt").write_text("uncommitted")
        (project / "new.txt").write_text("untracked")
        (project / ".env").write_text("SYNTHETIC=secret")
        workspace = self.root / "workspace"
        manifest = task._init_workspace(str(project), workspace)
        self.assertEqual((workspace / "code.txt").read_text(), "uncommitted")
        self.assertEqual((workspace / "new.txt").read_text(), "untracked")
        self.assertEqual(manifest["source_commit"], head)
        self.assertTrue(manifest["source_dirty"])
        self.assertFalse((workspace / ".env").exists())
        self.assertFalse((workspace / ".git").exists())
        self.assertEqual(manifest["file_hashes"]["code.txt"], task._file_sha(workspace / "code.txt"))

    def test_api_author_reviewer_receives_and_preserves_full_author_reply(self):
        config = provider.default_provider_config(display_name="fake", provider_type="local", base_url="http://localhost:1234", models=[{"id": "fake"}])
        config["status"] = "connected"
        provider.save_provider(self.root, config)
        created = task.create_task(self.root, title="review a draft")
        agent = config["provider_id"] + "/fake"
        task.save_plan(self.root, created["task_id"], strategy="AUTHOR_REVIEWER", steps=[
            {"title": "author", "description": "write", "recommended_agent": agent},
            {"title": "review", "description": "review", "recommended_agent": agent, "context_policy": "ARTIFACT_ONLY", "depends_on": ["S1"]}])
        task.approve_plan(self.root, created["task_id"])
        draft = "UNIQUE AUTHOR EVIDENCE\n" + "a" * 6000
        prompts = []
        def complete(prompt):
            prompts.append(prompt)
            return (draft if len(prompts) == 1 else "reviewed"), {}
        with patch.object(OpenAICompatibleProvider, "complete_with_usage", side_effect=complete):
            run = task.run_task(self.root, created["task_id"])
        self.assertEqual(run["status"], "completed")
        self.assertIn(draft, prompts[1])
        self.assertEqual((Path(run["run_dir"]) / run["steps"][0]["stdout_ref"]).read_text(), draft)
        self.assertEqual((Path(run["run_dir"]) / "workspace/output.md").read_text(), "reviewed")

    def test_ten_step_finishing_report_and_manual_report_target_s10(self):
        from icle.active import set_budget
        from icle.report import finishing_consolidation
        set_budget(self.root, 20)
        created = task.create_task(self.root, title="ten steps")
        task.save_plan(self.root, created["task_id"], strategy="DECOMPOSE", steps=[
            {"title": str(i), "description": str(i), "recommended_agent": str(i)} for i in range(1, 11)])
        task.approve_plan(self.root, created["task_id"])
        def factory(agent):
            report = {"requirements_total": 10, "requirements_met": int(agent), "input_tokens": 1,
                      "output_tokens": 1, "duration_s": 1, "summary": agent}
            return lambda *args: ("completed", "ICLE_TASK_REPORT\n" + json.dumps(report), "", 0)
        with patch("icle.task._settle_step_cost", return_value=None):
            task.run_task(self.root, created["task_id"], executor_factory=factory)
        run = task.latest_run(self.root, created["task_id"])
        self.assertEqual([s["step_id"] for s in run["steps"]], [f"S{i}" for i in range(1, 11)])
        self.assertEqual(finishing_consolidation(task.collect_report_entries(self.root, created["task_id"]))["report"]["requirements_met"], 10)
        task.set_manual_report(self.root, created["task_id"], {"requirements_total": 10, "requirements_met": 8, "input_tokens": 1, "output_tokens": 1, "duration_s": 1})
        reread = task.latest_run(self.root, created["task_id"])
        self.assertEqual(reread["steps"][-1]["step_id"], "S10")
        self.assertEqual(reread["steps"][-1]["report_origin"], "user")
        self.assertNotIn("report_origin", reread["steps"][-2])

    def test_legacy_ledger_remains_readable_and_new_records_use_v2(self):
        from icle.ledger import GENESIS, _chain
        root = self.root / "ledger"
        root.mkdir()
        old = ExperienceLedger.new_record(record_id="old", kind="episode", agent_revision=revision(), payload="data")
        old.update(seq=1, prev=GENESIS)
        old["chain"] = _chain(old, GENESIS)
        (root / "ledger.jsonl").write_text(json.dumps(old) + "\n")
        ledger = ExperienceLedger(root)
        new = ledger.append(ledger.new_record(record_id="new", kind="episode", agent_revision=revision(), payload="new"))
        self.assertEqual(new["hash_version"], 2)
        self.assertEqual(ledger.records()[0], old)
        new.pop("hash_version")
        ledger.path.write_text(json.dumps(old) + "\n" + json.dumps(new) + "\n")
        with self.assertRaises(LedgerError):
            ExperienceLedger(root)

    def test_redirect_never_forwards_authentication(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        import urllib.error
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/redirect-target")
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(urllib.error.HTTPError):
                provider._http_get(f"http://127.0.0.1:{server.server_port}", "/models", {"Authorization": "Bearer synthetic"}, 3)
            self.assertEqual(seen, ["/models"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_sensitive_symlink_and_custom_ignore_never_enter_context(self):
        project = self.root / "project"
        project.mkdir()
        (project / ".icleignore").write_text("private.txt\n")
        (project / "private.txt").write_text("private content")
        (project / ".env").write_text("hidden secret")
        (project / "leak.txt").symlink_to(project / ".env")
        workspace = self.root / "workspace"
        task._init_workspace(str(project), workspace)
        self.assertFalse((workspace / "private.txt").exists())
        self.assertFalse((workspace / "leak.txt").exists())
        prompt = task._step_prompt({"title": "test", "description": "test"}, {"context_policy": "PROJECT_STATE"}, project)
        self.assertNotIn("hidden secret", prompt)
        self.assertNotIn("private content", prompt)

    def test_replacement_cannot_reuse_a_deleted_dependency_id(self):
        created = task.create_task(self.root, title="dependency")
        saved = task.save_plan(self.root, created["task_id"], strategy="DECOMPOSE", steps=[
            {"title": "consumer", "description": "consumer", "depends_on": ["S2"]},
            {"title": "producer", "description": "producer"}])
        consumer = saved["plan"]["steps"][0]
        with self.assertRaises(task.TaskError):
            task.update_plan_steps(self.root, created["task_id"], steps=[consumer, {"title": "different", "description": "different"}])

    def test_missing_final_report_does_not_substitute_an_earlier_author(self):
        from icle.report import finishing_consolidation
        entries = [
            {"task_id": "t-1", "step_id": "S1", "agent": "author", "order": 1, "is_final": False,
             "report_status": "observed", "report": {"requirements_total": 1, "requirements_met": 1}},
            {"task_id": "t-1", "step_id": "S2", "agent": "reviewer", "order": 2, "is_final": True,
             "report_status": "missing", "report": None},
        ]
        self.assertIsNone(finishing_consolidation(entries)["report"])

    def test_provider_transactions_work_between_processes(self):
        import multiprocessing
        import time
        context = multiprocessing.get_context("fork")
        gate = context.Barrier(2)
        original_load = provider._load_configs
        def save(index):
            def delayed_load(store):
                result = original_load(store)
                time.sleep(0.03)
                return result
            config = provider.default_provider_config(display_name=str(index), provider_type="local", base_url="http://localhost:1234")
            gate.wait(timeout=5)
            with patch("icle.provider._load_configs", side_effect=delayed_load):
                provider.save_provider(self.root, config, api_key="synthetic")
        children = [context.Process(target=save, args=(i,)) for i in range(2)]
        try:
            for child in children:
                child.start()
            for child in children:
                child.join(10)
                self.assertEqual(child.exitcode, 0)
            self.assertEqual(len(provider.list_providers(self.root)), 2)
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join()

    def test_api_rejects_incomplete_context_before_model_call(self):
        config = provider.default_provider_config(display_name="fake", provider_type="local", base_url="http://localhost:1234", models=[{"id": "fake"}])
        config["status"] = "connected"
        provider.save_provider(self.root, config)
        executor = task._provider_executor_for(self.root, config["provider_id"] + "/fake")
        (self.root / "output.md").write_text("x" * 256001)
        prompt = task._step_prompt({"description": "review", "title": "review"}, {"context_policy": "ARTIFACT_ONLY"}, self.root)
        with patch.object(OpenAICompatibleProvider, "complete_with_usage") as complete:
            result = executor(self.root, prompt, 1)
        self.assertEqual(result[0], "failed")
        complete.assert_not_called()
        clean = task._step_prompt({"description": "review", "title": "review"}, {"context_policy": "CLEAN"}, self.root)
        self.assertNotIn("FILE", clean.split("REQUIRED FINAL REPORT")[0])

    def test_legacy_run_orders_timestamps_before_numeric_ids(self):
        created = task.create_task(self.root, title="legacy")
        created["runs"] = ["run-old"]
        task._save(self.root, created)
        steps_dir = self.root / "tasks" / created["task_id"] / "runs/run-old/steps"
        steps_dir.mkdir(parents=True)
        for index, sid in enumerate(["S2", "S10", "S1"]):
            (steps_dir / (sid + ".json")).write_text(json.dumps({"step_id": sid, "status": "completed", "created_at": f"2026-01-01T00:00:0{index}Z"}))
        run = task.latest_run(self.root, created["task_id"])
        self.assertEqual([step["step_id"] for step in run["steps"]], ["S2", "S10", "S1"])
        self.assertEqual(run["order_source"], "legacy_timestamp")

    def test_skill_conversion_preserves_step_identity_and_dependencies(self):
        from icle.api.tasks import _skill_step_to_task_step
        step = _skill_step_to_task_step({"step_id": "A-S7", "goal": "review", "dependencies": ["A-S3"]})
        self.assertEqual(step.get("step_id"), "S7")
        self.assertEqual(step["depends_on"], ["S3"])

    def test_workspace_inside_project_prunes_execution_storage(self):
        project = self.root / "project"
        project.mkdir()
        (project / "code.py").write_text("pass")
        workspace = project / "store/tasks/t-1/runs/run-1/workspace"
        manifest = task._init_workspace(str(project), workspace)
        self.assertEqual((workspace / "code.py").read_text(), "pass")
        self.assertFalse((workspace / "store").exists())
        self.assertIn("store", manifest["excluded_files"])

    def test_context_marker_in_source_is_data_not_execution_control(self):
        config = provider.default_provider_config(display_name="fake", provider_type="local", base_url="http://localhost:1234", models=[{"id": "fake"}])
        config["status"] = "connected"
        provider.save_provider(self.root, config)
        workspace = self.root / "workspace"
        workspace.mkdir()
        (workspace / "source.py").write_text('message = "[CONTEXT_INCOMPLETE: example]"')
        prompt = task._step_prompt({"title": "test", "description": "test"}, {"context_policy": "PROJECT_STATE"}, workspace)
        executor = task._provider_executor_for(self.root, config["provider_id"] + "/fake")
        with patch.object(OpenAICompatibleProvider, "complete_with_usage", return_value=("done", {})) as complete:
            result = executor(workspace, prompt, 1)
        self.assertEqual(result[0], "completed")
        complete.assert_called_once()


if __name__ == '__main__':
    unittest.main()
