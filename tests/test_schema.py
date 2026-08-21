"""P0: core schema tests (valid/missing/unknown-enum/path-escape per schema)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle import schema  # noqa: E402


def agent_revision() -> dict:
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": "hermes",
        "revision_id": "rev-0001",
        "model": "gpt-5.6-luna",
        "cli": "hermes-0.20.0",
        "provider": "nous",
        "persona_sha256": "sha256:" + "a" * 64,
        "memory_sha256": None,
        "tools_sha256": None,
        "execution_provider": "direct_cli",
        "created_at": "2026-08-14T00:00:00Z",
    }


def episode() -> dict:
    return {
        "schema_version": "icle-task-episode/v0.1",
        "episode_id": "ep-0001",
        "project_id": "toh",
        "source_agent_revision": agent_revision(),
        "source_session": "session-abc",
        "task_start": {
            "original_user_request": "fix the flaky test",
            "execution_provider": "direct_cli",
            "project_snapshot": {"git_revision": "abc123", "workspace_sha256": "sha256:" + "b" * 64},
        },
        "created_at": "2026-08-14T00:00:00Z",
    }


def replay() -> dict:
    return {
        "schema_version": "icle-replay-run/v0.1",
        "replay_id": "rp-0001",
        "episode_id": "ep-0001",
        "target_agent_revision": agent_revision(),
        "context_bundle": {"mode": "CLEAN", "refs": [], "sha256": "sha256:" + "c" * 64},
        "status": "created",
        "created_at": "2026-08-14T00:00:00Z",
    }


def judgment() -> dict:
    return {
        "schema_version": "icle-judgment/v0.1",
        "judgment_id": "j-0001",
        "subject": {"type": "replay_pair", "a": "rp-0001", "b": "rp-0002"},
        "kind": "prefer_a",
        "judge": "human",
        "reason_tags": ["correctness", "speed"],
        "created_at": "2026-08-14T00:00:00Z",
    }


def llm_judgment() -> dict:
    doc = judgment()
    doc.update(
        {
            "judge": "llm",
            "judge_model": "k3",
            "judge_revision": "rev-9",
            "prompt_sha256": "sha256:" + "d" * 64,
            "input_sha256": "sha256:" + "e" * 64,
        }
    )
    return doc


def ledger_record() -> dict:
    return {
        "schema_version": "icle-experience-record/v0.1",
        "record_id": "rec-0001",
        "kind": "episode",
        "agent_revision": agent_revision(),
        "payload_sha256": "sha256:" + "f" * 64,
        "episode_id": "ep-0001",
        "created_at": "2026-08-14T00:00:00Z",
    }


def provider() -> dict:
    return {
        "schema_version": "icle-execution-provider/v0.1",
        "name": "deepseek_harness",
        "version": "0.1.0-pin-abc",
        "capabilities": {"sessions": True, "subagents": True, "sandbox": False},
        "experimental": True,
    }


def bundle() -> dict:
    return {
        "schema_version": "icle-context-bundle/v0.1",
        "mode": "SELECTED_HISTORY",
        "refs": ["sessions/abc/turns-4-19.jsonl"],
        "sha256": "sha256:" + "1" * 64,
        "created_at": "2026-08-14T00:00:00Z",
    }


CASES = [
    ("agent_revision", schema.validate_agent_revision, agent_revision, None, ("execution_provider", "magic")),
    ("episode", schema.validate_task_episode, episode, None, None),
    ("replay", schema.validate_replay_run, replay, "result_ref", ("status", "teleported")),
    ("judgment", schema.validate_judgment, judgment, None, ("kind", "love_it")),
    ("llm_judgment", schema.validate_judgment, llm_judgment, None, ("kind", "meh")),
    ("ledger_record", schema.validate_experience_record, ledger_record, None, ("kind", "vibe")),
    ("provider", schema.validate_execution_provider, provider, None, ("name", "hal")),
    ("bundle", schema.validate_context_bundle, bundle, None, ("mode", "EVERYTHING")),
]


class SchemaTests(unittest.TestCase):
    def test_valid_documents_pass(self) -> None:
        for name, validate, factory, _, _ in CASES:
            with self.subTest(name=name):
                doc = factory()
                self.assertIs(validate(doc), doc)

    def test_missing_field_rejected(self) -> None:
        for name, validate, factory, _, _ in CASES:
            doc = factory()
            removed = next(iter(doc))
            doc.pop(removed)
            with self.subTest(name=name, field=removed):
                with self.assertRaises(schema.SchemaError):
                    validate(doc)

    def test_unknown_enum_rejected(self) -> None:
        for name, validate, factory, _, enum_case in CASES:
            if enum_case is None:
                continue
            field, bad = enum_case
            doc = factory()
            if field == "execution_provider":
                doc[field] = bad
            elif field == "kind":
                doc[field] = bad
            elif field == "status":
                doc[field] = bad
            elif field == "name":
                doc[field] = bad
            elif field == "mode":
                doc[field] = bad
            with self.subTest(name=name, field=field):
                with self.assertRaises(schema.SchemaError):
                    validate(doc)

    def test_path_escape_rejected(self) -> None:
        doc = replay()
        doc["result_ref"] = "../secret"
        with self.assertRaises(schema.SchemaError):
            schema.validate_replay_run(doc)
        doc2 = bundle()
        doc2["refs"] = ["/etc/passwd"]
        with self.assertRaises(schema.SchemaError):
            schema.validate_context_bundle(doc2)

    def test_llm_judgment_requires_provenance(self) -> None:
        doc = llm_judgment()
        del doc["prompt_sha256"]
        with self.assertRaisesRegex(schema.SchemaError, "prompt_sha256"):
            schema.validate_judgment(doc)

    def test_episode_rejects_bad_snapshot_hash(self) -> None:
        doc = episode()
        doc["task_start"]["project_snapshot"]["workspace_sha256"] = "md5:nope"
        with self.assertRaises(schema.SchemaError):
            schema.validate_task_episode(doc)

    def test_no_credential_fields_anywhere(self) -> None:
        for _, _, factory, _, _ in CASES:
            for key in factory():
                self.assertNotRegex(key.lower(), r"(api_?key|secret|token|password|credential)")


if __name__ == "__main__":
    unittest.main()
