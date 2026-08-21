"""P0: ExperienceLedger tests."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.ledger import ExperienceLedger, LedgerError  # noqa: E402
from icle.schema import SchemaError  # noqa: E402


def revision(agent: str = "hermes") -> dict:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent,
        "revision_id": "rev-1",
        "model": "m",
        "cli": "c",
        "provider": "p",
        "persona_sha256": None,
        "memory_sha256": None,
        "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": "2026-08-14T00:00:00Z",
    }


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()) / "ledger"

    def test_append_chain_and_rebuild(self) -> None:
        ledger = ExperienceLedger(self.root)
        first = ledger.append(
            ExperienceLedger.new_record(
                record_id="r1", kind="episode", agent_revision=revision(), payload="episode one"
            )
        )
        second = ledger.append(
            ExperienceLedger.new_record(
                record_id="r2", kind="judgment", agent_revision=revision("kimi"), payload="A>B"
            )
        )
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["prev"], first["chain"])
        # reopen: full replay verifies the chain
        reopened = ExperienceLedger(self.root)
        self.assertEqual(len(reopened.records()), 2)
        self.assertEqual(reopened.records(agent_id="kimi")[0]["record_id"], "r2")
        self.assertEqual(reopened.records(kind="episode")[0]["record_id"], "r1")

    def test_two_instances_reload_chain_tail_before_append(self) -> None:
        first = ExperienceLedger(self.root)
        second = ExperienceLedger(self.root)
        one = first.append(
            ExperienceLedger.new_record(
                record_id="r1", kind="episode", agent_revision=revision(), payload="one"
            )
        )
        two = second.append(
            ExperienceLedger.new_record(
                record_id="r2", kind="judgment", agent_revision=revision(), payload="two"
            )
        )
        self.assertEqual((one["seq"], two["seq"]), (1, 2))
        self.assertEqual(two["prev"], one["chain"])
        self.assertEqual([r["record_id"] for r in ExperienceLedger(self.root).records()], ["r1", "r2"])

    def test_content_transaction_rolls_back_append_or_publish_failure(self) -> None:
        ledger = ExperienceLedger(self.root)
        content = '{"rating_id":"r1"}\n'
        record = ExperienceLedger.new_record(
            record_id="r1", kind="user_rating", agent_revision=revision(), payload=content
        )
        target = self.root.parent / "ratings" / "r1.json"
        with patch.object(ledger, "_append_locked", side_effect=LedgerError("append failed")):
            with self.assertRaises(LedgerError):
                ledger.append_with_content(record, content_path=target, content=content)
        self.assertFalse(target.exists())
        self.assertEqual(ExperienceLedger(self.root).records(), [])
        self.assertEqual(list((self.root / "pending").iterdir()), [])

        original_replace = __import__("os").replace

        def fail_publication(source, destination):
            if Path(destination) == target:
                raise OSError("publish failed")
            return original_replace(source, destination)

        with patch("icle.ledger.os.replace", side_effect=fail_publication):
            with self.assertRaises(OSError):
                ledger.append_with_content(record, content_path=target, content=content)
        self.assertFalse(target.exists())
        self.assertEqual(ExperienceLedger(self.root).records(), [])
        self.assertEqual(list((self.root / "pending").iterdir()), [])

    def test_committed_pending_content_is_recovered_and_uncommitted_is_removed(self) -> None:
        ledger = ExperienceLedger(self.root)
        content = '{"rating_id":"r1"}\n'
        record = ExperienceLedger.new_record(
            record_id="r1", kind="user_rating", agent_revision=revision(), payload=content
        )
        record["content_ref"] = "ratings/r1.json"
        ledger.append(record)
        pending = self.root / "pending"
        pending.mkdir(exist_ok=True)
        (pending / "r1.content").write_text(content, encoding="utf-8")
        (pending / "r1.json").write_text(
            json.dumps({
                "record_id": "r1", "content_ref": "ratings/r1.json",
                "staged_name": "r1.content",
            }) + "\n",
            encoding="utf-8",
        )
        ExperienceLedger(self.root)
        self.assertEqual(
            (self.root.parent / "ratings" / "r1.json").read_text(encoding="utf-8"),
            content,
        )
        self.assertEqual(list(pending.iterdir()), [])

        (pending / "orphan.content").write_text("orphan", encoding="utf-8")
        (pending / "orphan.json").write_text(
            json.dumps({
                "record_id": "not-committed", "content_ref": "ratings/orphan.json",
                "staged_name": "orphan.content",
            }) + "\n",
            encoding="utf-8",
        )
        ExperienceLedger(self.root)
        self.assertFalse((self.root.parent / "ratings" / "orphan.json").exists())
        self.assertEqual(list(pending.iterdir()), [])

    def test_projection_is_recomputable(self) -> None:
        ledger = ExperienceLedger(self.root)
        for index in range(3):
            ledger.append(
                ExperienceLedger.new_record(
                    record_id=f"r{index}", kind="episode",
                    agent_revision=revision(), payload=f"payload {index}",
                )
            )
        profile = ledger.project(
            lambda records: {"episodes": sum(1 for r in records if r["kind"] == "episode")}
        )
        self.assertEqual(profile, {"episodes": 3})

    def test_invalid_record_rejected(self) -> None:
        ledger = ExperienceLedger(self.root)
        bad = ExperienceLedger.new_record(
            record_id="x", kind="not-a-kind", agent_revision=revision(), payload="x"
        )
        with self.assertRaises(SchemaError):
            ledger.append(bad)

    def test_tampered_history_fails_verification(self) -> None:
        ledger = ExperienceLedger(self.root)
        ledger.append(
            ExperienceLedger.new_record(
                record_id="r1", kind="episode", agent_revision=revision(), payload="one"
            )
        )
        # tamper with the stored line
        lines = ledger.path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[0])
        record["kind"] = "judgment"
        ledger.path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            ExperienceLedger(self.root)

    def test_corrupt_line_is_loud(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "ledger.jsonl").write_text("{broken\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            ExperienceLedger(self.root)


if __name__ == "__main__":
    unittest.main()
