"""ExperienceLedger: append-only experience evidence (P0).

The ledger is the source of truth; agent experience profiles are projections
that can always be rebuilt from it ('algorithm bugs are recomputable').
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any, Callable

from .schema import validate_experience_record

GENESIS = "sha256:" + "0" * 64


class LedgerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chain(record: dict[str, Any], prev_chain: str) -> str:
    version = record.get("hash_version", 1)
    if version == 2:
        protected = {key: value for key, value in record.items() if key != "chain"}
        protected["prev"] = prev_chain
        return _sha_text(json.dumps(protected, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False))
    if version != 1:
        raise LedgerError(f"unsupported ledger hash_version: {version!r}")
    protected = {
        "record_id": record["record_id"],
        "kind": record["kind"],
        "payload_sha256": record["payload_sha256"],
        "created_at": record["created_at"],
        "prev": prev_chain,
    }
    # v0.1 records did not carry a content reference. New dual-write records do;
    # include it in the chain without changing verification of historical lines.
    if record.get("content_ref") is not None:
        protected["content_ref"] = record["content_ref"]
    payload = json.dumps(protected, sort_keys=True, ensure_ascii=False)
    return _sha_text(payload)


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.RLock())


def _durable_atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ExperienceLedger:
    """Hash-chained append-only JSONL ledger at ``<root>/ledger.jsonl``.

    Every read/append synchronizes with other threads and processes. Evidence
    content uses :meth:`append_with_content`, whose hidden pending file and
    manifest let a later ledger load finish a commit interrupted by a process
    crash. Ordinary exceptions roll back the not-yet-returned append.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "ledger.jsonl"
        self.lock_path = self.root / "ledger.lock"
        self.pending_dir = self.root / "pending"
        self._records: list[dict[str, Any]] = []
        self._synchronize()

    def _read_records_locked(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self.path.is_file():
            return records
        for lineno, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerError(f"corrupt ledger line {lineno}: {exc}") from exc
        self._verify_records(records)
        return records

    @staticmethod
    def _verify_records(records: list[dict[str, Any]]) -> None:
        prev = GENESIS
        for index, record in enumerate(records, start=1):
            if record.get("seq") != index or record.get("prev") != prev:
                raise LedgerError(f"ledger chain broken at seq {index}")
            if record.get("chain") != _chain(record, prev):
                raise LedgerError(f"ledger hash mismatch at seq {index}")
            prev = record["chain"]

    def _verify_chain(self) -> None:
        self._verify_records(self._records)

    def _recover_pending_locked(self, records: list[dict[str, Any]]) -> None:
        """Publish committed staged content; discard uncommitted preparations."""
        if not self.pending_dir.is_dir():
            return
        by_id = {str(record.get("record_id")): record for record in records}
        for manifest_path in sorted(self.pending_dir.glob("*.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                record_id = str(manifest["record_id"])
                content_ref = str(manifest["content_ref"])
                staged_name = str(manifest["staged_name"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise LedgerError(f"corrupt pending ledger transaction: {manifest_path.name}") from exc
            if PurePath(content_ref).is_absolute() or ".." in PurePath(content_ref).parts:
                raise LedgerError(f"unsafe pending content_ref: {content_ref!r}")
            staged = self.pending_dir / staged_name
            target = self.root.parent / content_ref
            committed = by_id.get(record_id)
            if committed is not None:
                if committed.get("content_ref") != content_ref:
                    raise LedgerError(f"pending content_ref mismatch for {record_id}")
                if not staged.is_file():
                    if target.is_file():
                        manifest_path.unlink(missing_ok=True)
                        continue
                    raise LedgerError(f"committed content missing for {record_id}")
                text = staged.read_text(encoding="utf-8")
                if _sha_text(text) != committed.get("payload_sha256"):
                    raise LedgerError(f"pending payload hash mismatch for {record_id}")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
            else:
                staged.unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)

    def _synchronize(self) -> None:
        lock = _thread_lock(self.lock_path)
        with lock, self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                records = self._read_records_locked()
                self._recover_pending_locked(records)
                self._records = records
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _append_locked(self, record: dict[str, Any]) -> tuple[dict[str, Any], int]:
        records = self._read_records_locked()
        prev = records[-1]["chain"] if records else GENESIS
        entry = dict(record)
        entry["hash_version"] = 2
        entry.pop("chain", None)
        entry["seq"] = len(records) + 1
        entry["prev"] = prev
        entry["chain"] = _chain(entry, prev)
        original_size = self.path.stat().st_size if self.path.exists() else 0
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._records = [*records, entry]
        return entry, original_size

    def append(
        self,
        record: dict[str, Any],
        *,
        validate: Callable[[Any], dict] = validate_experience_record,
    ) -> dict[str, Any]:
        """Validate, reload, chain and append one record under an exclusive lock."""
        validate(record)
        lock = _thread_lock(self.lock_path)
        with lock, self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                records = self._read_records_locked()
                self._recover_pending_locked(records)
                entry, _ = self._append_locked(record)
                return entry
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append_with_content(
        self,
        record: dict[str, Any],
        *,
        content_path: str | Path,
        content: str,
        validate: Callable[[Any], dict] = validate_experience_record,
    ) -> dict[str, Any]:
        """Commit a content document and its ledger proof as one transaction.

        The public content path is not visible until the ledger append is
        durable. If a regular exception occurs, the just-appended line is
        truncated while the lock is still held. If the process dies after the
        durable append, the pending manifest lets the next ledger load publish
        the exact hash-verified content.
        """
        validate(record)
        target = Path(content_path)
        try:
            content_ref = target.resolve().relative_to(self.root.parent.resolve()).as_posix()
        except ValueError as exc:
            raise LedgerError("content_path must be inside the ledger store") from exc
        if ".." in PurePath(content_ref).parts:
            raise LedgerError("unsafe content_path")
        if _sha_text(content) != record.get("payload_sha256"):
            raise LedgerError("content does not match record payload_sha256")

        transactional_record = dict(record)
        transactional_record["content_ref"] = content_ref
        lock = _thread_lock(self.lock_path)
        with lock, self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            appended = False
            original_size = 0
            manifest_path: Path | None = None
            staged_path: Path | None = None
            try:
                records = self._read_records_locked()
                self._recover_pending_locked(records)
                if target.exists():
                    raise LedgerError(f"evidence content already exists: {content_ref}")
                if any(item.get("record_id") == record.get("record_id") for item in records):
                    raise LedgerError(f"duplicate ledger record_id: {record.get('record_id')}")

                self.pending_dir.mkdir(parents=True, exist_ok=True)
                transaction_id = f"{record['record_id']}-{os.getpid()}-{threading.get_ident()}"
                staged_path = self.pending_dir / f"{transaction_id}.content"
                manifest_path = self.pending_dir / f"{transaction_id}.json"
                _durable_atomic_write(staged_path, content)
                _durable_atomic_write(
                    manifest_path,
                    json.dumps(
                        {
                            "record_id": record["record_id"],
                            "content_ref": content_ref,
                            "staged_name": staged_path.name,
                        },
                        ensure_ascii=False,
                    ) + "\n",
                )
                entry, original_size = self._append_locked(transactional_record)
                appended = True
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_path, target)
                manifest_path.unlink(missing_ok=True)
                return entry
            except Exception:
                # This is rollback of the current unreturned transaction only;
                # historical ledger evidence is never rewritten.
                if appended:
                    with self.path.open("r+b") as ledger_file:
                        ledger_file.truncate(original_size)
                        ledger_file.flush()
                        os.fsync(ledger_file.fileno())
                    self._records = self._read_records_locked()
                    target.unlink(missing_ok=True)
                if staged_path is not None:
                    staged_path.unlink(missing_ok=True)
                if manifest_path is not None:
                    manifest_path.unlink(missing_ok=True)
                raise
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def records(
        self, *, kind: str | None = None, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        self._synchronize()
        out = self._records
        if kind is not None:
            out = [record for record in out if record["kind"] == kind]
        if agent_id is not None:
            out = [
                record
                for record in out
                if record["agent_revision"]["agent_id"] == agent_id
            ]
        return list(out)

    def project(self, projection: Callable[[list[dict[str, Any]]], Any]) -> Any:
        """Rebuild any profile/view from the full ledger (recomputable)."""
        self._synchronize()
        return projection(list(self._records))

    @staticmethod
    def new_record(
        *,
        record_id: str,
        kind: str,
        agent_revision: dict[str, Any],
        payload: str,
        episode_id: str | None = None,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "schema_version": "icle-experience-record/v0.1",
            "record_id": record_id,
            "kind": kind,
            "agent_revision": agent_revision,
            "payload_sha256": _sha_text(payload),
            "created_at": _now(),
        }
        if episode_id is not None:
            record["episode_id"] = episode_id
        return record
