from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experience_evaluation.c_state import CStateStore
from experience_evaluation.profile import FORBIDDEN_TOTAL_KEYS, render_subject_profile
from experience_evaluation.public_dataset import DatasetError


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "contract_id": "c-state-test/v1",
            "initial_mean": 0.0,
            "initial_sd": 1.0,
            "mean_half_life_days": 10.0,
            "process_variance_per_day": 0.01,
            "min_observation_sd": 0.05,
        }
        self.policy = json.loads(
            Path(__file__).resolve().parents[1].joinpath("config/profile_publication_policy.json").read_text(encoding="utf-8")
        )

    def _cell(self, dimension, value=0.4, scope="local_controlled"):
        return {
            "cell_id": f"c-{dimension}",
            "c_generator_version": "experience-evaluation-c-generator/v0.4",
            "subject_id": "agent-alpha",
            "dimension_id": dimension,
            "status": "available",
            "value": value,
            "stderr": 0.2,
            "scale_id": "canonical_logit/v0.1",
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "calibration_contract_id": "local-usage-c/v0.1",
            "publication_scope": scope,
            "evidence_origin": "local_controlled_runs",
            "warnings": [],
        }

    def test_portrait_lists_all_seven_axes_and_never_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            store.update(
                [self._cell("reasoning"), self._cell("coding", 0.1)],
                observed_at="2026-08-20T00:00:00+00:00",
                source_bundle_id="bundle-1",
            )
            portrait = render_subject_profile(store, "agent-alpha", self.policy)
            self.assertEqual(portrait["publication_status"], "publishable_local_portrait")
            self.assertFalse(portrait["ranking"])
            self.assertEqual(portrait["observed_axis_count"], 2)
            self.assertEqual(portrait["unavailable_axis_count"], 5)
            self.assertEqual(portrait["axes"]["agentic_coding"]["status"], "unavailable")
            self.assertIsNone(portrait["axes"]["agentic_coding"]["mean"])
            self.assertTrue(FORBIDDEN_TOTAL_KEYS.isdisjoint(portrait))

    def test_shadow_scope_cannot_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            store.update(
                [self._cell("reasoning", scope="shadow")],
                observed_at="2026-08-20T00:00:00+00:00",
                source_bundle_id="bundle-1",
            )
            portrait = render_subject_profile(store, "agent-alpha", self.policy)
            self.assertEqual(portrait["publication_status"], "shadow_only")
            self.assertEqual(portrait["axes"]["reasoning"]["status"], "observed")

    def test_policy_rejects_total_score(self) -> None:
        policy = dict(self.policy)
        policy["emit_total_score"] = True
        with tempfile.TemporaryDirectory() as tmp:
            store = CStateStore(Path(tmp), self.config)
            with self.assertRaises(DatasetError):
                render_subject_profile(store, "agent-alpha", policy)


if __name__ == "__main__":
    unittest.main()
