"""Decision-layer loss: Q-weighted C/J, missing is not 0, no totals."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.coordination import coordination_portrait, team_revision_id  # noqa: E402
from icle.decision import (  # noqa: E402
    FORBIDDEN_TOTAL_KEYS,
    LOSS_ID,
    score_measurement,
    score_route,
)
from icle.evolution import CANONICAL_AXES, _safe_subject, subject_id_for  # noqa: E402


def _measurement(**axis_rows: dict) -> dict:
    axes = {axis: {"status": "unavailable", "c": None, "j": None} for axis in CANONICAL_AXES}
    axes.update(axis_rows)
    return {"axes": axes, "ranking": False}


class DecisionLossTests(unittest.TestCase):
    def test_unmapped_profile_has_no_quality(self) -> None:
        signal = score_measurement(_measurement(), {"primary_type": "OTHER"})
        self.assertEqual(signal["loss_id"], LOSS_ID)
        self.assertIsNone(signal["quality"])
        self.assertEqual(signal["quality_source"], "none")
        self.assertEqual(signal["reason"], "q_unmapped")
        self.assertTrue(FORBIDDEN_TOTAL_KEYS.isdisjoint(signal))

    def test_missing_axes_are_skipped_not_zero(self) -> None:
        signal = score_measurement(_measurement(), {"primary_type": "CODING"})
        self.assertIsNone(signal["quality"])
        self.assertEqual(signal["axes_skipped"], ["coding", "agentic_coding"])
        self.assertEqual(signal["reason"], "no_observed_quality_axis")

    def test_c_residual_is_preferred(self) -> None:
        signal = score_measurement(
            _measurement(coding={"status": "observed", "c": 0.4, "mean": 0.4, "j": 2.0}),
            {"primary_type": "CODING", "subtype": "refactor"},
        )
        self.assertEqual(signal["quality_source"], "c_residual")
        self.assertEqual(signal["quality"], 0.4)
        self.assertEqual(signal["axes_used"][0]["axis"], "coding")
        self.assertGreater(signal["quality_unit"], 0.5)

    def test_j_is_used_when_c_gate_fails(self) -> None:
        signal = score_measurement(
            _measurement(coding={"status": "unavailable", "c": None, "j": 1.0}),
            {"primary_type": "CODING", "subtype": "refactor"},
        )
        self.assertEqual(signal["quality_source"], "j_observed")
        self.assertEqual(signal["quality"], 1.0)

    def test_a_is_never_quality(self) -> None:
        signal = score_measurement(
            _measurement(coding={"status": "unavailable", "a": 0.9, "c": None, "j": None}),
            {"primary_type": "CODING", "subtype": "refactor"},
        )
        self.assertIsNone(signal["quality"])

    def test_score_route_reads_layer_j(self) -> None:
        store = Path(tempfile.mkdtemp()) / "store"
        store.mkdir()
        route = {"agent": "kimi", "provider_id": "", "model": "kimi-for-coding"}
        subject = subject_id_for(route)
        layer = {
            "j": {
                "status": "observed",
                "cells": [{"dimension_id": "coding", "value": 0.8, "status": "observed"}],
            }
        }
        path = store / "evolution" / "layers" / f"{_safe_subject(subject)}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(layer), encoding="utf-8")
        signal = score_route(
            store, route=route, task_profile={"primary_type": "CODING", "subtype": "refactor"}
        )
        self.assertEqual(signal["quality_source"], "j_observed")
        self.assertIsNotNone(signal["quality_unit"])


class CoordinationContractTests(unittest.TestCase):
    def test_team_revision_is_stable_and_ordered(self) -> None:
        first = team_revision_id(members=["a", "b"], topology="sequential")
        second = team_revision_id(members=["a", "b"], topology="sequential")
        other = team_revision_id(members=["b", "a"], topology="sequential")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_missing_solo_baseline_keeps_axes_unavailable(self) -> None:
        portrait = coordination_portrait(members=["a", "b"], topology="parallel")
        self.assertTrue(portrait["team_revision_id"].startswith("team-"))
        self.assertTrue(all(row["status"] == "unavailable" for row in portrait["axes"].values()))
        self.assertNotIn("total", portrait)


if __name__ == "__main__":
    unittest.main()
