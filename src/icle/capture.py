"""Session Capture (P1): normalize heterogeneous agent sessions into SessionEvent.

Deterministic recording only — NO understanding, NO LLM. Raw events are
append-only; re-capturing is idempotent. Credentials are always redacted
(real incident: hermes request dumps contain live Bearer headers).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REDACT_PATTERNS = [
    re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(bearer\s+)[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)([\"'\s:=]+)[a-z0-9._~+/=-]{8,}"),
    re.compile(r"sk-[a-z0-9._-]{8,}"),
]

EVENT_KINDS = ("user", "assistant", "tool_call", "tool_result", "artifact", "failure", "usage", "meta")


class CaptureError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(text: str) -> str:
    out = text
    for pattern in REDACT_PATTERNS:
        if pattern.pattern.startswith("(?i)(authorization"):
            out = pattern.sub(r"\1\2[REDACTED]", out)
        elif "bearer" in pattern.pattern:
            out = pattern.sub("Bearer [REDACTED]", out)
        elif "api" in pattern.pattern:
            out = pattern.sub(r"\1\2[REDACTED]", out)
        else:
            out = pattern.sub("sk-[REDACTED]", out)
    return out


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


def _event(
    session_id: str,
    agent_id: str,
    seq: int,
    kind: str,
    ts: float | str | None,
    content: str,
    *,
    project: str | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind not in EVENT_KINDS:
        raise CaptureError(f"unknown event kind: {kind}")
    return {
        "schema_version": "icle-session-event/v0.1",
        "session_id": session_id,
        "agent_id": agent_id,
        "seq": seq,
        "kind": kind,
        "ts": ts,
        "project": project,
        "content": redact(content),
        "content_sha256": _sha(content),
        "detail": detail or {},
    }


# ----------------------------------------------------------------- hermes


def capture_hermes(state_db: str | Path, store: str | Path) -> dict[str, Any]:
    """Hermes stores sessions/messages in ~/.hermes/state.db (SQLite)."""
    state_db = Path(state_db).expanduser()
    if not state_db.is_file():
        raise CaptureError(f"hermes state.db not found: {state_db}")
    conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True)
    captured = []
    try:
        sessions = conn.execute(
            "SELECT id, model, cwd, title, started_at FROM sessions"
        ).fetchall()
        for session_id, model, cwd, title, started_at in sessions:
            existing = _existing_seqs(store, "hermes", session_id)
            rows = conn.execute(
                "SELECT id, role, content, tool_calls, tool_name, timestamp, finish_reason "
                "FROM messages WHERE session_id = ? ORDER BY timestamp, id",
                (session_id,),
            ).fetchall()
            events = []
            for msg_id, role, content, tool_calls, tool_name, ts, finish in rows:
                if msg_id in existing:
                    continue
                kind = {
                    "user": "user",
                    "assistant": "assistant",
                    "tool": "tool_result",
                    "system": "meta",
                }.get(role, "meta")
                if tool_calls:
                    kind = "tool_call"
                if finish and finish not in {"stop", "end_turn", None}:
                    kind = "failure"
                events.append(
                    _event(
                        str(session_id), "hermes", msg_id, kind, ts,
                        content or "",
                        project=cwd,
                        detail={"role": role, "tool_name": tool_name, "finish_reason": finish},
                    )
                )
            captured.append(_write_session(store, "hermes", session_id, events, model=model, title=title))
    finally:
        conn.close()
    return _write_index(store, captured)


# ----------------------------------------------------------------- kimi


def _kimi_kind(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "turn.prompt":
        return "user"
    if event_type == "context.append_message":
        role = payload.get("message", {}).get("role") if isinstance(payload.get("message"), dict) else None
        return {"user": "user", "assistant": "assistant"}.get(role, "meta")
    if event_type == "context.append_loop_event":
        return "assistant"
    if event_type == "usage.record":
        return "usage"
    if event_type in {"turn.cancel", "turn.error"}:
        return "failure"
    return "meta"


def _load_kimi_index(sessions_root: Path) -> dict[str, str]:
    index_path = sessions_root.parent / "session_index.jsonl"
    mapping: dict[str, str] = {}
    if index_path.is_file():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                mapping[row["sessionId"]] = row.get("workDir", "")
            except (json.JSONDecodeError, KeyError):
                continue
    return mapping


def capture_kimi(sessions_root: str | Path, store: str | Path) -> dict[str, Any]:
    """Kimi stores turns in sessions/wd_*/session_*/agents/main/wire.jsonl."""
    sessions_root = Path(sessions_root).expanduser()
    if not sessions_root.is_dir():
        raise CaptureError(f"kimi sessions dir not found: {sessions_root}")
    index = _load_kimi_index(sessions_root)
    captured = []
    for wire in sorted(sessions_root.glob("*/session_*/agents/main/wire.jsonl")):
        session_dir = wire.parents[2]
        session_id = session_dir.name
        existing = _existing_seqs(store, "kimi", session_id)
        state = {}
        state_path = session_dir / "state.json"
        if state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                state = {}
        events = []
        for seq, line in enumerate(wire.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if seq in existing or not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"type": "unparseable", "raw": line}
            event_type = payload.get("type", "unknown")
            events.append(
                _event(
                    session_id, "kimi", seq, _kimi_kind(event_type, payload),
                    payload.get("created_at") or payload.get("time"),
                    line,
                    project=index.get(session_id) or None,
                    detail={"source_type": event_type},
                )
            )
        captured.append(
            _write_session(store, "kimi", session_id, events, title=state.get("title"))
        )
    return _write_index(store, captured)


# ----------------------------------------------------------------- store


def _existing_seqs(store: str | Path, agent_id: str, session_id: str) -> set[int]:
    path = Path(store) / agent_id / str(session_id) / "events.jsonl"
    if not path.is_file():
        return set()
    seqs = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                seqs.add(json.loads(line)["seq"])
            except (json.JSONDecodeError, KeyError):
                continue
    return seqs


def _write_session(
    store: str | Path,
    agent_id: str,
    session_id: str,
    new_events: list[dict[str, Any]],
    **meta: Any,
) -> dict[str, Any]:
    session_dir = Path(store) / agent_id / str(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    with events_path.open("a", encoding="utf-8") as handle:
        for event in new_events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    with events_path.open(encoding="utf-8") as count_handle:
        total = sum(1 for _ in count_handle)  # 修复: 句柄随 with 关闭,不再泄漏
    return {
        "agent_id": agent_id,
        "session_id": str(session_id),
        "new_events": len(new_events),
        "total_events": total,
        **{k: v for k, v in meta.items() if v},
    }


def _write_index(store: str | Path, captured: list[dict[str, Any]]) -> dict[str, Any]:
    store = Path(store)
    store.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": "icle-capture-index/v0.1",
        "captured_at": _now(),
        "sessions": captured,
    }
    _atomic_write(store / "index.json", json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return index


# ----------------------------------------------------------------- generic JSONL capture

# 每个 agent 的会话都是 JSONL,但事件 schema 不同(Claude 顶层 type;Codex
# 嵌套 payload.type/role;Kimi wire.jsonl)。用声明式映射复用同一套读取/写入
# 框架,而不是每个 agent 重写几十行。adapter 只负责三件事:
#   kind_of    事件 → user/assistant/failure(None = 跳过噪音)
#   content_of 事件 → 文本内容
#   ts_of      事件 → 时间戳
# 其余(幂等、脱敏、哈希、写入、index)全由框架处理。


def _capture_jsonl_sessions(
    store: str | Path,
    agent_id: str,
    files: list[Path],
    *,
    session_id_of,
    kind_of,
    content_of,
    ts_of,
    title_of=None,
    project_of=None,
    content_limit: int = 6000,
) -> dict[str, Any]:
    """Generic JSONL session capture: one framework, per-agent mapping only."""
    captured = []
    for path in files:
        session_id = session_id_of(path)
        existing = _existing_seqs(store, agent_id, session_id)
        events = []
        for seq, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if seq in existing or not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                payload = {"type": "unparseable", "raw": line}
            kind = kind_of(payload)
            if kind is None:
                continue  # 跳过噪音事件(queue-operation / event_msg / token_count)
            content = content_of(payload, line)[:content_limit]
            events.append(
                _event(
                    session_id, agent_id, seq, kind, ts_of(payload), content,
                    project=project_of(payload) if project_of else None,
                    detail={"source_type": payload.get("type")},
                )
            )
        captured.append(
            _write_session(store, agent_id, session_id, events,
                           title=title_of(path) if title_of else None)
        )
    return _write_index(store, captured)


# ----------------------------------------------------------------- claude code


def capture_claude(projects_root: str | Path, store: str | Path) -> dict[str, Any]:
    """Claude Code: ~/.claude/projects/<proj>/<uuid>.jsonl。

    事件顶层 type: user / assistant(其余 queue-operation/thinking/skill_listing
    等为噪音,跳过)。session_id = 文件名(uuid);title = 项目目录名。
    """
    projects_root = Path(projects_root).expanduser()
    if not projects_root.is_dir():
        raise CaptureError(f"claude projects dir not found: {projects_root}")
    files = sorted(projects_root.glob("**/*.jsonl"))

    def kind_of(payload: dict) -> str | None:
        return {"user": "user", "assistant": "assistant"}.get(payload.get("type"))

    def content_of(payload: dict, line: str) -> str:
        # 真实 claude 事件:内容在 message.content(可能为字符串或数组)。
        # 兼容旧版顶层 content。数组优先取 text 元素(thinking 是内部推理)。
        message = payload.get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if content is None:
            content = payload.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if texts:
                return "\n".join(texts)
            # 无 text 的轮次(如纯思考)取 thinking——同样是自然语言
            thinks = [
                part.get("thinking", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "thinking"
            ]
            if thinks:
                return "\n".join(thinks)
        return line

    def ts_of(payload: dict) -> str | None:
        return payload.get("timestamp")

    def session_id_of(path: Path) -> str:
        return path.stem

    def title_of(path: Path) -> str:
        return path.parent.name

    return _capture_jsonl_sessions(
        store, "claude", files,
        session_id_of=session_id_of, kind_of=kind_of,
        content_of=content_of, ts_of=ts_of, title_of=title_of,
    )


# ----------------------------------------------------------------- codex


def capture_codex(sessions_root: str | Path, store: str | Path) -> dict[str, Any]:
    """Codex: ~/.codex/sessions/<year>/<month>/<day>/rollout-<uuid>.jsonl。

    事件 type=response_item + payload.type=message + payload.role=user/assistant;
    content 在 payload.content[].text。session_id = 文件名(uuid);
    title 取文件首行 metadata 的 thread_name。
    """
    sessions_root = Path(sessions_root).expanduser()
    if not sessions_root.is_dir():
        raise CaptureError(f"codex sessions dir not found: {sessions_root}")
    files = sorted(sessions_root.glob("**/rollout-*.jsonl"))

    def kind_of(payload: dict) -> str | None:
        if payload.get("type") != "response_item":
            return None
        inner = payload.get("payload") or {}
        if inner.get("type") != "message":
            return None
        return {"user": "user", "assistant": "assistant"}.get(inner.get("role"))

    def content_of(payload: dict, line: str) -> str:
        inner = payload.get("payload") or {}
        content = inner.get("content")
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "input_text"
            ]
            return "\n".join(parts) or line
        return content if isinstance(content, str) else line

    def ts_of(payload: dict) -> str | None:
        return payload.get("timestamp")

    def session_id_of(path: Path) -> str:
        return path.stem

    def title_of(path: Path) -> str | None:
        try:
            first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
            meta = json.loads(first)
            return meta.get("thread_name")
        except (IndexError, json.JSONDecodeError):
            return None

    return _capture_jsonl_sessions(
        store, "codex", files,
        session_id_of=session_id_of, kind_of=kind_of,
        content_of=content_of, ts_of=ts_of, title_of=title_of,
    )


# ----------------------------------------------------------------- opencode (SQLite)

def capture_opencode(db_path: str | Path, store: str | Path) -> dict[str, Any]:
    """OpenCode: ~/.local/share/opencode/opencode.db (SQLite, WAL)。

    新版 opencode 用 SQLite:session(id,title,directory) / message(id,session_id,
    time_created,data) / part(message_id,data)。role 在 message.data.role;
    文本内容在关联 part 的 data.type=text.data.text(JSON-in-TEXT,key 顺序
    由 writer 决定,必须逐字段解析)。只读打开,不影响运行中的 opencode。
    """
    db = Path(db_path).expanduser()
    if not db.is_file():
        raise CaptureError(f"opencode db not found: {db}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    captured = []
    try:
        sessions = conn.execute(
            "SELECT id, title, directory FROM session ORDER BY id"
        ).fetchall()
        for session_id, title, directory in sessions:
            existing = _existing_seqs(store, "opencode", session_id)
            messages = conn.execute(
                "SELECT id, time_created, data FROM message "
                "WHERE session_id=? ORDER BY time_created, id",
                (session_id,),
            ).fetchall()
            events = []
            seq = 0
            for msg_id, ts, data in messages:
                try:
                    payload = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue  # summary/agent 等无 role 或系统消息跳过
                seq += 1
                if seq in existing:
                    continue
                parts = conn.execute(
                    "SELECT data FROM part WHERE message_id=? ORDER BY rowid",
                    (msg_id,),
                ).fetchall()
                texts = []
                for (part_data,) in parts:
                    try:
                        part = json.loads(part_data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    text = part.get("text") if part.get("type") == "text" else None
                    if isinstance(text, str):
                        texts.append(text)
                content = "\n".join(texts)
                events.append(
                    _event(
                        session_id, "opencode", seq, role, ts,
                        content, project=directory,
                        detail={"source_type": "message"},
                    )
                )
            captured.append(_write_session(store, "opencode", session_id, events, title=title))
    finally:
        conn.close()
    return _write_index(store, captured)


# ----------------------------------------------------------------- pi (JSONL)


def capture_pi(sessions_root: str | Path, store: str | Path) -> dict[str, Any]:
    """pi (pi-coding-agent): ~/.pi/agent/sessions/<proj>/<ts>_<uuid>.jsonl。

    每行事件带 type;消息行 {"type":"message","message":{"role":...,
    "content":[{"type":"text","text":...}]}}。thinking/toolCall/model_change/
    session 等行跳过。session_id = 文件名中的 uuid 段。
    """
    sessions_root = Path(sessions_root).expanduser()
    if not sessions_root.is_dir():
        raise CaptureError(f"pi sessions dir not found: {sessions_root}")
    files = sorted(sessions_root.glob("**/*.jsonl"))

    def kind_of(payload: dict) -> str | None:
        if payload.get("type") != "message":
            return None
        role = payload.get("message", {}).get("role")
        return {
            "user": "user",
            "assistant": "assistant",
            "toolResult": "tool_result",
            "toolCall": "tool_call",
            "system": "meta",
        }.get(role)

    def content_of(payload: dict, line: str) -> str:
        content = payload.get("message", {}).get("content")
        if isinstance(content, list):
            texts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "\n".join(texts) or line
        return line

    def ts_of(payload: dict) -> str | None:
        return payload.get("timestamp")

    def session_id_of(path: Path) -> str:
        return path.stem.split("_")[-1]  # <ts>_<uuid>.jsonl → uuid

    return _capture_jsonl_sessions(
        store, "pi", files,
        session_id_of=session_id_of, kind_of=kind_of,
        content_of=content_of, ts_of=ts_of,
    )
