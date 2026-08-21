from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from experience_evaluation.public_dataset import DatasetError
from experience_evaluation.run_bundle import (
    RUN_BUNDLE_SCHEMA,
    RunBundleStore,
    compute_content_hash,
    to_execution_result,
    validate_run_bundle,
)


def _bundle(**overrides):
    payload = {
        "schema_version": RUN_BUNDLE_SCHEMA,
        "run_id": "run-1",
        "task_spec_id": "implement-feature",
        "task_revision": "1.0.0",
        "subject_id": "agent-alpha",
        "agent_revision_id": "agent-alpha@1",
        "started_at": "2026-08-20T12:00:00+00:00",
        "ended_at": "2026-08-20T12:05:00+00:00",
        "status": "completed",
        "outcome": {
            "verifier_id": "objective-metrics/v0.1",
            "metrics": {"tests_passed": 9, "regressions": 1},
        },
        "runner": {"runner_id": "external", "revision": "demo"},
        "events": [],
    }
    payload.update(overrides)
    payload["content_hash"] = compute_content_hash(payload)
    return payload


class RunBundleTests(unittest.TestCase):
    def test_valid_bundle_maps_to_execution_result(self) -> None:
        result = to_execution_result(_bundle())
        self.assertEqual(result["result_id"], "run-1")
        self.assertEqual(result["task_id"], "implement-feature")
        self.assertEqual(result["metrics"]["tests_passed"], 9)
        self.assertEqual(result["provenance"]["content_hash"], _bundle()["content_hash"])

    def test_hash_mismatch_is_rejected(self) -> None:
        bundle = _bundle()
        bundle["content_hash"] = "0" * 64
        with self.assertRaises(DatasetError):
            validate_run_bundle(bundle)

    def test_same_run_is_idempotent_and_conflict_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunBundleStore(Path(tmp))
            first = store.ingest([_bundle()])
            second = store.ingest([_bundle()])
            self.assertEqual(first["accepted"], 1)
            self.assertEqual(second["duplicates"], 1)
            conflict = _bundle()
            conflict["outcome"] = {"verifier_id": "objective-metrics/v0.1", "metrics": {"tests_passed": 1}}
            conflict["content_hash"] = compute_content_hash(conflict)
            with self.assertRaises(DatasetError):
                store.ingest([conflict])
            results = store.execution_results()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["metrics"]["tests_passed"], 9)

    def test_missing_required_field_fails(self) -> None:
        with self.assertRaises(DatasetError):
            validate_run_bundle({"schema_version": RUN_BUNDLE_SCHEMA, "run_id": "x"})


if __name__ == "__main__":
    unittest.main()
