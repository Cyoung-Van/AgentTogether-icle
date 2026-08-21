from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.c_state import CStateStore
from experience_evaluation.public_dataset import DatasetError


class CStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "contract_id": "c-state-test/v1",
            "initial_mean": 0.0,
            "initial_sd": 1.0,
            "mean_half_life_days": 10.0,
            "process_variance_per_day": 0.01,
            "min_observation_sd": 0.05,
        }

    @staticmethod
    def _cell(cell_id="c1", value=0.5, stderr=0.2, status="available"):
        return {
            "cell_id": cell_id,
            "c_generator_version": "experience-evaluation-c-generator/v0.2",
            "subject_id": "agent",
            "dimension_id": "coding",
            "status": status,
            "value": value if status == "available" else None,
            "stderr": stderr if status == "available" else None,
            "scale_id": "canonical_logit/v0.1",
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "calibration_contract_id": "cal-v1",
            "warnings": ["b_agent_specialized_prior_used"],
        }

    def test_first_observation_updates_prior_by_precision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            result = store.update(
                [self._cell()], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="bundle-1"
            )
            self.assertEqual(result["updated"], 1)
            state = store.get("agent", "coding")
            self.assertAlmostEqual(state["mean"], 0.4807692307692308)
            self.assertAlmostEqual(state["variance"], 1 / 26)
            self.assertEqual(state["evidence_count"], 1)
            self.assertEqual(state["scale_id"], "canonical_logit/v0.1")

    def test_decay_reverts_to_the_configured_prior_not_to_zero(self) -> None:
        config = dict(self.config, initial_mean=-0.4)
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), config)
            store.update([self._cell(value=-0.4)], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1")
            store.update(
                [self._cell("c2", value=-0.4)],
                observed_at="2027-08-20T00:00:00+00:00",
                source_bundle_id="b2",
            )
            state = store.get("agent", "coding")
            self.assertAlmostEqual(state["mean"], -0.4)

    def test_negative_residuals_are_stored_with_their_sign(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            store.update(
                [self._cell(value=-0.9)], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1"
            )
            self.assertLess(store.get("agent", "coding")["mean"], 0)

    def test_later_observation_applies_decay_and_process_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            store.update([self._cell()], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1")
            result = store.update(
                [self._cell("c2", value=0.8)],
                observed_at="2026-08-30T00:00:00+00:00",
                source_bundle_id="b2",
            )
            revision = result["revisions"][0]
            self.assertAlmostEqual(revision["decay_factor"], 0.5)
            self.assertAlmostEqual(revision["predicted_mean"], 0.4807692307692308 * 0.5)
            self.assertAlmostEqual(revision["predicted_variance"], 1 / 26 + 0.1)
            state = store.get("agent", "coding")
            self.assertEqual(state["evidence_count"], 2)
            self.assertGreater(state["mean"], revision["predicted_mean"])

    def test_same_bundle_conflicting_reuse_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            store.update([self._cell()], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1")
            conflict = store.update([self._cell(value=0.9)], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1")
            self.assertEqual(conflict["rejected"], 1)
            self.assertEqual(store.get("agent", "coding")["evidence_count"], 1)

    def test_missing_stderr_uses_min_observation_sd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            result = store.update(
                [self._cell(stderr=None)],
                observed_at="2026-08-20T00:00:00+00:00",
                source_bundle_id="bundle-1",
            )
            self.assertEqual(result["updated"], 1)
            self.assertAlmostEqual(result["revisions"][0]["effective_observation_sd"], 0.05)

    def test_unavailable_cell_is_recorded_as_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            result = store.update(
                [self._cell(status="unavailable")],
                observed_at="2026-08-20T00:00:00+00:00",
                source_bundle_id="b1",
            )
            self.assertEqual(result["rejected"], 1)
            self.assertIsNone(store.get("agent", "coding"))

    def test_persistence_rebuild_and_hash_chain_corruption_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = CStateStore(root, self.config)
            store.update([self._cell()], observed_at="2026-08-20T00:00:00+00:00", source_bundle_id="b1")
            reloaded = CStateStore(root, self.config, read_only=True)
            current_before = (root / "current.json").read_bytes()
            rebuilt = reloaded.verify_and_rebuild(rebuild_projection=False)
            self.assertEqual((root / "current.json").read_bytes(), current_before)
            self.assertEqual(rebuilt["state_count"], 1)
            self.assertAlmostEqual(reloaded.get("agent", "coding")["mean"], 0.4807692307692308)

            revisions = root / "revisions.jsonl"
            row = json.loads(revisions.read_text().splitlines()[0])
            row["posterior_mean"] = 99
            revisions.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(DatasetError):
                CStateStore(root, self.config).verify_and_rebuild()


if __name__ == "__main__":
    unittest.main()
