"""Session 浏览与「会话 → 项目任务」转换(WebUI 用,无 LLM 依赖部分)。

对应需求:
- 获取的会话能在 WebUI 中看到(list/read)
- 用户选择一个或多个会话 → 先生成可编辑 Task 预览；明确确认后创建一个 Task
- 接入 LLM 后,选一个或多个会话 → LLM 生成任务 Proposal；只有用户在 UI
  明确接受后才会创建 Task，Proposal 本身不落 Episode/Ledger
- create_episode_from_session 仅保留给旧 CLI/兼容调用，不是 WebUI 创建任务路径

capture-store 结构:<capture_store>/<agent>/<session>/events.jsonl + <capture_store>/index.json
experience-store 结构:<store>/tasks/t-*.json + episodes/ep-*.json
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .capture import CaptureError
from .episode import create_episode, list_episodes
from .intelligence import IntelligenceError, analyze_session

SESSIONS_PAGE = 200  # 单会话事件详情默认返回条数(前端分页取更多)

_CAPTURE_AGENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CAPTURE_SESSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _assert_capture_ids(agent_id: str, session_id: str) -> None:
    """S3: capture 路径安全 —— 白名单校验,拒绝 / 与 .. 段(HTTP 可达的穿越入口)。"""
    if not _CAPTURE_AGENT_RE.match(agent_id):
        raise SessionError(f"invalid agent_id: {agent_id!r}")
    if not _CAPTURE_SESSION_RE.match(session_id):
        raise SessionError(f"invalid session_id: {session_id!r}")


class SessionError(ValueError):
    pass


def _events_of(events_path: Path) -> list[dict]:
    if not events_path.is_file():
        return []
    rows = []
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def list_sessions(capture_store: str | Path) -> dict:
    """列出 capture-store 全部会话,按 agent 分组(含事件数与最后事件时间)。"""
    root = Path(capture_store)
    agents: dict[str, list[dict]] = {}
    total_events = 0
    if root.is_dir():
        for agent_dir in sorted(root.iterdir()):
            if not agent_dir.is_dir():
                continue
            sessions = []
            for session_dir in sorted(agent_dir.iterdir()):
                events_path = session_dir / "events.jsonl"
                if not events_path.is_file():
                    continue
                events = _events_of(events_path)
                if not events:
                    continue
                total_events += len(events)
                last = events[-1].get("ts") or events[-1].get("created_at")
                sessions.append({
                    "session_id": session_dir.name,
                    "agent_id": agent_dir.name,
                    "events": len(events),
                    "last_event": last,
                })
            if sessions:
                agents[agent_dir.name] = sessions
    return {"schema_version": "icle-sessions/v0.1", "agents": agents, "total_events": total_events}


def read_session(
    capture_store: str | Path, agent_id: str, session_id: str,
    offset: int = 0, limit: int = SESSIONS_PAGE,
) -> dict:
    """读取单个会话的事件(时间线视图)。"""
    _assert_capture_ids(agent_id, session_id)  # S3
    events_path = Path(capture_store) / agent_id / session_id / "events.jsonl"
    events = _events_of(events_path)
    if not events:
        raise SessionError(f"captured session not found: {agent_id}/{session_id}")
    return {
        "agent_id": agent_id,
        "session_id": session_id,
        "total": len(events),
        "offset": offset,
        "events": events[offset:offset + limit],
    }


def create_task_from_proposal(
    store: str | Path,
    *,
    proposal_task: dict,
    provenance: dict | None,
    project_id: str,
    project_path: str | Path,
    source_sessions: list[dict[str, str]] | None = None,
) -> dict:
    """接受 SessionAnalysisProposal 的任务 → 创建 Task(带 TaskProfile)(§31 调用链)。

    把 analyze 的 task_type/subtype/difficulty/risk + goal 映射到 TaskProfile;
    缺的字段用默认值。source='llm' 且带 provenance(skill 分析的审计链)。
    subtype 若不属于 primary 的子类则清空(避免校验失败,信息保留在 reason)。
    """
    from .task import TASK_SUBTYPES, create_task, set_task_profile, show_task

    primary = proposal_task.get("task_type") or "OTHER"
    subtype = proposal_task.get("subtype") or ""
    allowed = TASK_SUBTYPES.get(primary, ())
    if subtype not in allowed:
        subtype = ""
    title = proposal_task.get("title") or proposal_task.get("final_request") or "Untitled task"
    goal = proposal_task.get("goal") or proposal_task.get("original_request") or ""
    task = create_task(
        store, title=title, description=goal,
        project_id=project_id, project_path=str(project_path),
        creation_source="session_analysis_proposal",
        source_sessions=source_sessions or [],
    )
    profile = {
        "schema_version": "icle-task-profile/v0.1",
        "primary_type": primary,
        "subtype": subtype,
        "difficulty": proposal_task.get("difficulty") or "D2",
        "risk": proposal_task.get("risk") or "R1",
        "context_requirement": "MEDIUM",
        "tool_requirement": [],
        "estimated_duration": "unknown",
        "decomposition": "not_recommended",
        "review": "not_recommended",
        "reason": proposal_task.get("goal") or proposal_task.get("final_request") or "",
        "source": "llm",
        "provenance": {
            "provider": (provenance or {}).get("provider", "unknown"),
            "model": (provenance or {}).get("model", "unknown"),
            "prompt_sha256": (provenance or {}).get("prompt_sha256", ""),
        },
    }
    set_task_profile(store, task["task_id"], profile)
    return show_task(store, task["task_id"])


def preview_task_from_sessions(
    capture_store: str | Path,
    picks: list[dict],
) -> dict:
    """Build a deterministic, editable preview for one Task from 1+ sessions.

    No Task/Episode is persisted. Every selected source is validated before a
    preview is returned, so the subsequent explicit confirmation cannot refer
    to invented or stale session IDs.
    """
    if not isinstance(picks, list) or not picks:
        raise SessionError("at least one session required")
    if len(picks) > 50:
        raise SessionError("at most 50 sessions can be merged into one task")
    sources: list[dict[str, str]] = []
    summaries: list[str] = []
    seen: set[tuple[str, str]] = set()
    for index, pick in enumerate(picks, start=1):
        if not isinstance(pick, dict):
            raise SessionError(f"sessions[{index}] must be an object")
        agent_id = str(pick.get("agent_id") or "")
        session_id = str(pick.get("session_id") or "")
        _assert_capture_ids(agent_id, session_id)
        identity = (agent_id, session_id)
        if identity in seen:
            raise SessionError(f"duplicate session: {agent_id}/{session_id}")
        seen.add(identity)
        session = read_session(capture_store, agent_id, session_id, limit=1)
        summary = _fallback_request(capture_store, agent_id, session_id)
        sources.append({
            "agent_id": agent_id,
            "session_id": session_id,
            "summary": summary,
            "events": str(session.get("total") or ""),
        })
        summaries.append(f"[{agent_id}/{session_id}] {summary}")
    first = sources[0]["summary"]
    title = first[:180]
    if len(sources) > 1:
        suffix = f" (+{len(sources) - 1} sessions)"
        title = title[: max(1, 200 - len(suffix))] + suffix
    return {
        "title": title or "Task from captured sessions",
        "description": "\n\n".join(summaries),
        "sources": sources,
        "count": len(sources),
    }


def create_task_from_sessions(
    store: str | Path,
    capture_store: str | Path,
    *,
    picks: list[dict],
    title: str,
    description: str,
    project_id: str,
    project_path: str | Path,
) -> dict:
    """Persist one draft Task after explicit confirmation of a session preview."""
    from .task import create_task

    preview = preview_task_from_sessions(capture_store, picks)
    clean_title = str(title or preview["title"]).strip()
    clean_description = str(description or preview["description"]).strip()
    if not clean_title:
        raise SessionError("task title required")
    sources = [
        {"agent_id": item["agent_id"], "session_id": item["session_id"]}
        for item in preview["sources"]
    ]
    return create_task(
        store,
        title=clean_title[:200],
        description=clean_description[:5000],
        project_id=str(project_id or "default"),
        project_path=str(project_path or ""),
        creation_source=f"captured_sessions:{len(sources)}",
        source_sessions=sources,
    )


def create_episode_from_session(    store: str | Path,
    capture_store: str | Path,
    *,
    agent_id: str,
    session_id: str,
    user_request: str,
    project_id: str,
    project_path: str | Path,
    from_seq: int | None = None,
    to_seq: int | None = None,
) -> dict:
    """Legacy CLI compatibility: convert one session directly to an Episode.

    The WebUI no longer calls this function; it previews and creates a Task via
    create_task_from_sessions, and only an accepted Task result becomes an
    Episode through the normal Task lifecycle.
    """
    user_request = (user_request or "").strip()
    if not user_request:
        # 说明是保底项,非必填:留空时用会话首个 user 消息兜底,
        # 保证「无 LLM 时手动创建」也总有可读的任务请求。
        user_request = _fallback_request(capture_store, agent_id, session_id)
    if (from_seq is None) != (to_seq is None):
        raise SessionError("from_seq/to_seq must be both set or both None")
    # 校验会话真实存在(即使不切 turn_range,也禁止凭空创建)
    try:
        read_session(capture_store, agent_id, session_id, limit=1)
    except SessionError as exc:
        raise SessionError(f"captured session not found: {agent_id}/{session_id}") from exc
    try:
        return create_episode(
            store,
            session_id=session_id,
            agent_revision=_session_agent_revision(agent_id),
            project_id=project_id,
            project_path=project_path,
            user_request=user_request,
            from_seq=from_seq,
            to_seq=to_seq,
            capture_store=capture_store,
        )
    except Exception as exc:  # EpisodeError/其他 → 统一 SessionError 语义
        raise SessionError(str(exc)) from exc


def _fallback_request(capture_store: str | Path, agent_id: str, session_id: str) -> str:
    """保底说明:取会话首个「自然指令」user 消息;JSON 包裹的 wire 事件
    递归提取 text;没有则退回通用描述。说明字段是保底项,非必填。"""
    try:
        events = read_session(capture_store, agent_id, session_id, limit=200)["events"]
    except SessionError:
        return f"Task from captured session {agent_id}/{session_id}"
    for ev in events:
        content = (ev.get("content") or "").strip()
        if ev.get("kind") != "user" or not content:
            continue
        text = _first_text(content)
        # 跳过 JSON 结构/超长内容(工具回显、配置块),更像自然指令
        if text[0] in "{[" or len(text) > 500:
            continue
        if text:
            return text[:300]
    # 都没有自然指令 → 退回首条 user(至少可读)或通用描述
    for ev in events:
        content = (ev.get("content") or "").strip()
        if ev.get("kind") == "user" and content:
            return _first_text(content)[:300] or f"Task from captured session {agent_id}/{session_id}"
    return f"Task from captured session {agent_id}/{session_id}"


def _first_text(data: object) -> str:
    """从 JSON wire 事件里递归提取第一段人类可读 text;非 JSON 原样返回。"""
    if isinstance(data, str):
        stripped = data.strip()
        if stripped and not (stripped[0] in "{[" and stripped[-1] in "}]"):
            return stripped
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        return _first_text(parsed)
    if isinstance(data, list):
        for item in data:
            found = _first_text(item)
            if found:
                return found
        return ""
    if isinstance(data, dict):
        for key in ("text", "content", "message", "input", "user_message"):
            if key in data:
                found = _first_text(data[key])
                if found:
                    return found
        return ""
    return ""


def _session_agent_revision(agent_id: str) -> dict:
    """构造合法的 icle-agent-revision/v0.1(捕获会话的简化 agent 配置描述)。

    revision_id 用 agent_id+时间派生,保持稳定;persona/memory/tools 哈希未知
    时用空串(validate_agent_revision 只要求字符串类型)。
    """
    from .episode import _now

    digest = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()
    return {
        "schema_version": "icle-agent-revision/v0.1",
        "agent_id": agent_id,
        "revision_id": f"{agent_id}@{_now()[:19]}",
        "model": "captured-session",
        "cli": agent_id,
        "provider": "unknown",
        "persona_sha256": f"sha256:{digest}",
        "memory_sha256": f"sha256:{digest}",
        "tools_sha256": f"sha256:{digest}",
        "execution_provider": "direct_cli",
        "created_at": _now(),
    }


def extract_tasks_from_sessions(
    store: str | Path,
    capture_store: str | Path,
    provider,
    *,
    picks: list[dict],
    project_id: str,
    project_path: str | Path,
    language: str = "en",
) -> dict:
    """Generate task proposals from selected sessions without persisting them.

    The store/project arguments remain for API compatibility. Persistence is
    intentionally deferred to create_task_from_proposal after explicit user
    acceptance in the WebUI.
    """
    del store, project_id, project_path
    if not picks:
        raise SessionError("at least one session required")
    proposals = []
    errors = []
    for pick in picks:
        agent_id = pick.get("agent_id")
        session_id = pick.get("session_id")
        if not agent_id or not session_id:
            errors.append({"session": pick, "error": "missing agent_id/session_id"})
            continue
        try:
            session = read_session(capture_store, agent_id, session_id, limit=10_000)
            analysis = analyze_session(provider, session["events"], language=language)
        except (CaptureError, SessionError, IntelligenceError, KeyError) as exc:
            errors.append({"agent_id": agent_id, "session_id": session_id, "error": str(exc)})
            continue
        if analysis.get("status") != "ok" or not analysis.get("tasks"):
            errors.append({
                "agent_id": agent_id,
                "session_id": session_id,
                "error": analysis.get("reason") or "analyze-session produced no tasks",
            })
            continue
        override = str(pick.get("description") or "").strip()
        tasks = []
        for task in analysis["tasks"]:
            item = dict(task)
            if override:
                item["goal"] = override
                item["final_request"] = override
                item["title"] = override[:200]
            tasks.append(item)
        proposals.append({
            "agent_id": agent_id,
            "session_id": session_id,
            "analysis": {
                "status": analysis.get("status"),
                "tasks": tasks,
                "provenance": analysis.get("provenance") or {},
            },
        })
    return {
        "proposals": proposals,
        "errors": errors,
        "proposal_count": sum(len(item["analysis"]["tasks"]) for item in proposals),
    }


def store_status(store: str | Path, capture_store: str | Path) -> dict:
    """Sessions 页顶部的 store 概况。"""
    episodes = list_episodes(store)
    return {
        "episodes": len(episodes),
        "capture_events": list_sessions(capture_store).get("total_events", 0),
    }
