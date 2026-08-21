"""TaskEpisode creation and store (P2).

A TaskEpisode marks "one real thing a user once had an agent do", derived from
captured sessions (turn range) plus a task-start snapshot (git revision when
available, workspace manifest hash otherwise). Deterministic only; task
UNDERSTANDING (boundaries, types) is left for the later Intelligence Layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import SchemaError, validate_task_episode

_EPISODE_ID_RE = re.compile(r"^ep-[A-Za-z0-9_-]+$")
_CAPTURE_AGENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CAPTURE_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _assert_episode_id(episode_id: Any) -> str:
    """S1: episode_id 路径穿越防护 —— 只允许白名单字符。"""
    if not isinstance(episode_id, str) or not _EPISODE_ID_RE.match(episode_id):
        raise EpisodeError(f"invalid episode_id: {episode_id!r}")
    return episode_id


def _assert_capture_ids(agent_id: Any, session_id: Any) -> None:
    """S3: capture 路径安全 —— agent/session id 白名单,拒绝 / 与 .. 段。"""
    if not isinstance(agent_id, str) or not _CAPTURE_AGENT_RE.match(agent_id):
        raise EpisodeError(f"invalid agent_id: {agent_id!r}")
    if not isinstance(session_id, str) or not _CAPTURE_SESSION_RE.match(session_id):
        raise EpisodeError(f"invalid session_id: {session_id!r}")


class EpisodeError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


def _git_revision(project: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _workspace_hash(project: Path, *, max_files: int = 5000) -> str:
    """Deterministic manifest hash for non-git projects (content+path based)."""
    digest = hashlib.sha256()
    count = 0
    for item in sorted(project.rglob("*")):
        if count >= max_files:
            digest.update(b"<truncated>")
            break
        if item.is_symlink() or not item.is_file():
            continue
        if any(part in {".git", "node_modules", "__pycache__", ".venv"} for part in item.parts):
            continue
        digest.update(item.relative_to(project).as_posix().encode())
        digest.update(b"\0")
        try:
            # S7: 超大文件不整读入内存(哈希语义保持确定;超限文件记占位)
            if item.stat().st_size > 25 * 1024 * 1024:
                digest.update(b"<oversized>")
            else:
                digest.update(hashlib.sha256(item.read_bytes()).hexdigest().encode())
        except OSError:
            continue
        digest.update(b"\0")
        count += 1
    return "sha256:" + digest.hexdigest()


def task_start_snapshot(project: str | Path) -> dict[str, Any]:
    """Snapshot the project state at task start (git preferred)."""
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise EpisodeError(f"project directory not found: {project}")
    snapshot: dict[str, Any] = {"project_path": str(project)}
    revision = _git_revision(project)
    if revision:
        snapshot["git_revision"] = revision
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project, capture_output=True, text=True, check=False, timeout=10,
        )
        snapshot["git_dirty"] = bool(dirty.stdout.strip())
    else:
        snapshot["workspace_sha256"] = _workspace_hash(project)
    return snapshot


def _next_episode_id(store: str | Path) -> str:
    episodes_dir = Path(store) / "episodes"
    existing = sorted(episodes_dir.glob("ep-*.json")) if episodes_dir.is_dir() else []
    return f"ep-{len(existing) + 1:04d}"


def create_episode(
    store: str | Path,
    *,
    session_id: str,
    agent_revision: dict[str, Any],
    project_id: str,
    project_path: str | Path,
    user_request: str,
    from_seq: int | None = None,
    to_seq: int | None = None,
    capture_store: str | Path | None = None,
    execution_provider: str = "direct_cli",
) -> dict[str, Any]:
    """Create one TaskEpisode from a captured session (manual marking)."""
    store = Path(store)
    turn_range: dict[str, int] = {}
    if from_seq is not None or to_seq is not None:
        if from_seq is None or to_seq is None or from_seq > to_seq:
            raise EpisodeError("turn range requires from_seq <= to_seq")
        turn_range = {"from_seq": from_seq, "to_seq": to_seq}
        if capture_store is None:
            raise EpisodeError("turn range requires capture_store to verify bounds")
        _assert_capture_ids(agent_revision["agent_id"], session_id)  # S3
        events_path = Path(capture_store) / agent_revision["agent_id"] / session_id / "events.jsonl"
        if not events_path.is_file():
            raise EpisodeError(f"captured session not found: {session_id}")
        seqs = [
            json.loads(line)["seq"]
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if from_seq not in seqs or to_seq not in seqs:
            raise EpisodeError(
                f"turn range {from_seq}-{to_seq} outside captured session bounds"
            )

    snapshot = task_start_snapshot(project_path)
    episode = {
        "schema_version": "icle-task-episode/v0.1",
        "episode_id": "",  # M1: 由原子分配在下方确定
        "project_id": project_id,
        "source_agent_revision": agent_revision,
        "source_session": session_id,
        "task_start": {
            "original_user_request": user_request,
            "execution_provider": execution_provider,
            "project_snapshot": snapshot,
            "turn_range": turn_range or None,
        },
        "created_at": _now(),
    }
    episodes_dir = store / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)
    # M1: ID 分配 + 写入原子化 —— O_EXCL 创建文件,并发/删除复用不再覆盖,
    # 保持 ep-NNNN 递增格式(存量兼容)。冲突则 +N 重试。
    n = len(list(episodes_dir.glob("ep-*.json")))
    while True:
        n += 1
        episode_id = f"ep-{n:04d}"
        episode["episode_id"] = episode_id
        validate_task_episode(episode)
        path = episodes_dir / f"{episode_id}.json"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(episode, ensure_ascii=False, indent=2) + "\n")
        return episode


def list_episodes(store: str | Path) -> list[dict[str, Any]]:
    episodes_dir = Path(store) / "episodes"
    if not episodes_dir.is_dir():
        return []
    out = []
    for path in sorted(episodes_dir.glob("ep-*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        out.append(
            {
                "episode_id": doc["episode_id"],
                "project_id": doc["project_id"],
                "agent": doc["source_agent_revision"]["agent_id"],
                "session": doc["source_session"],
                "request": doc["task_start"]["original_user_request"][:80],
                "created_at": doc["created_at"],
            }
        )
    return out


def show_episode(store: str | Path, episode_id: str) -> dict[str, Any]:
    _assert_episode_id(episode_id)  # S1
    path = Path(store) / "episodes" / f"{episode_id}.json"
    if not path.is_file():
        raise EpisodeError(f"unknown episode: {episode_id}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    validate_task_episode(doc)
    return doc
