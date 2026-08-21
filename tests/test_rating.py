"""UI-18 AgentRating core tests: validation, persistence, ledger, summary."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.judge import record_judgment, record_result_mark  # noqa: E402
from icle.ledger import ExperienceLedger, LedgerError  # noqa: E402
from icle.rating import (  # noqa: E402
    RATING_DIMENSIONS,
    RatingError,
    list_ratings,
    rating_summary,
    record_rating,
    validate_rating,
)

FIVE = {dim: 5 for dim in RATING_DIMENSIONS}


class RatingCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()

    def _rating(self, **overrides) -> dict:
        doc = {
            "schema_version": "icle-agent-rating/v0.1",
            "rating_id": "r-test",
            "source": "user",
            "agent_id": "kimi",
            "episode_id": "ep-test",
            "agent_revision": {
                "schema_version": "icle-agent-revision/v0.1",
                "agent_id": "kimi",
                "revision_id": "rev-1",
                "model": "m", "cli": "c", "provider": "p",
                "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
                "execution_provider": "direct_cli",
                "created_at": "2026-08-14T00:00:00Z",
            },
            "dimensions": FIVE,
            "overall_preference": 5,
            "would_use_again": "yes",
            "comment": "",
            "created_at": "2026-08-15T00:00:00Z",
        }
        doc.update(overrides)
        return doc

    def test_validates_ok(self) -> None:
        validate_rating(self._rating())

    def test_dimension_out_of_range_rejected(self) -> None:
        for bad in (0, 6, True, 3.5, "5"):
            with self.assertRaises(RatingError):
                validate_rating(self._rating(dimensions={**FIVE, "correctness": bad}))

    def test_missing_dimension_rejected(self) -> None:
        with self.assertRaises(RatingError):
            validate_rating(self._rating(dimensions={"correctness": 4}))

    def test_would_use_again_enum(self) -> None:
        for bad in ("Y", "sometimes", ""):
            with self.assertRaises(RatingError):
                validate_rating(self._rating(would_use_again=bad))

    def test_source_enum(self) -> None:
        with self.assertRaises(RatingError):
            validate_rating(self._rating(source="robot"))

    def test_record_writes_file_and_ledger(self) -> None:
        result = record_rating(
            self.store,
            agent_id="kimi",
            episode_id="ep-test",
            dimensions={**FIVE, "correctness": 4},
            overall_preference=4,
            would_use_again="maybe",
            comment="solid but verbose",
        )
        rating = result["rating"]
        self.assertTrue(rating["rating_id"].startswith("r-"))
        path = self.store / "ratings" / f"{rating['rating_id']}.json"
        self.assertTrue(path.is_file())
        self.assertIsNotNone(result["ledger_seq"])

        ledger = ExperienceLedger(self.store / "ledger")
        kinds = [r["kind"] for r in ledger.records()]
        self.assertIn("user_rating", kinds)
        # the ledger only proves existence; it never duplicates the rating
        self.assertEqual([k for k in kinds].count("user_rating"), 1)

    def test_broken_ledger_does_not_publish_rating_judgment_or_mark(self) -> None:
        ledger_dir = self.store / "ledger"
        ledger_dir.mkdir()
        (ledger_dir / "ledger.jsonl").write_text("{broken\n", encoding="utf-8")
        operations = [
            lambda: record_rating(
                self.store, agent_id="kimi", episode_id="ep-test",
                dimensions=FIVE, overall_preference=5, would_use_again="yes",
            ),
            lambda: record_judgment(
                self.store, subject={"type": "episode", "id": "ep-test"},
                kind="prefer_a", reason_tags=["correctness"],
            ),
            lambda: record_result_mark(
                self.store, episode_id="ep-test", mark="accept", agent_id="kimi",
            ),
        ]
        for operation in operations:
            with self.assertRaises(LedgerError):
                operation()
        self.assertEqual(list((self.store / "ratings").glob("r-*.json")), [])
        self.assertEqual(list((self.store / "judgments").glob("j-*.json")), [])
        self.assertEqual(list((self.store / "marks").glob("m-*.json")), [])

    def test_list_filter_by_agent(self) -> None:
        record_rating(self.store, agent_id="kimi", episode_id="e1",
                      dimensions=FIVE, overall_preference=5, would_use_again="yes")
        record_rating(self.store, agent_id="hermes", episode_id="e2",
                      dimensions={**FIVE, "efficiency": 3}, overall_preference=3,
                      would_use_again="no")
        self.assertEqual(len(list_ratings(self.store)), 2)
        self.assertEqual(len(list_ratings(self.store, agent_id="kimi")), 1)
        self.assertEqual(len(list_ratings(self.store, agent_id="nobody")), 0)

    def test_summary_averages(self) -> None:
        record_rating(self.store, agent_id="kimi", episode_id="e1",
                      dimensions={**FIVE, "correctness": 5}, overall_preference=5,
                      would_use_again="yes")
        record_rating(self.store, agent_id="kimi", episode_id="e2",
                      dimensions={**FIVE, "correctness": 3}, overall_preference=3,
                      would_use_again="maybe")
        summary = rating_summary(self.store, "kimi")
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["dimensions"]["correctness"], 4.0)
        self.assertEqual(summary["overall_preference"], 4.0)
        # other agents stay clean
        empty = rating_summary(self.store, "hermes")
        self.assertEqual(empty["count"], 0)
        self.assertIsNone(empty["dimensions"])

    def test_capability_not_touched(self) -> None:
        """User ratings never mutate capability evidence (no score fields on agent)."""
        record_rating(self.store, agent_id="kimi", episode_id="e1",
                      dimensions=FIVE, overall_preference=5, would_use_again="yes")
        rating = list_ratings(self.store)[0]
        self.assertNotIn("capability", rating)
        self.assertNotIn("score", rating)


if __name__ == "__main__":
    unittest.main()
