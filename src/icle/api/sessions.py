"""Sessions API:捕获会话浏览 + 会话→任务(手动 / LLM 批量)。

- GET  /api/sessions                   列出全部捕获会话(按 agent 分组)
- GET  /api/sessions/{agent}/{sid}     单个会话事件时间线(分页)
- POST /api/sessions/task-preview       选 1+ 会话 → 可编辑 Task 预览(不落库)
- POST /api/sessions/task               明确确认预览 → 创建一个 Task draft
- POST /api/sessions/extract-tasks      LLM:选 1+ 会话 → Task Proposals(不落库)

业务逻辑在 sessionlib(核心层);本文件只做参数绑定与 provider 解析。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..sessionlib import (
    SessionError,
    create_task_from_proposal,
    create_task_from_sessions,
    extract_tasks_from_sessions,
    list_sessions,
    preview_task_from_sessions,
    read_session,
)
from ..settings import resolve_provider

router = APIRouter()


def _capture_store(request: Request) -> Path:
    return Path(request.app.state.capture_store)


def _store(request: Request) -> Path:
    return Path(request.app.state.store)


@router.get("/sessions")
def sessions_list(request: Request) -> dict:
    return list_sessions(_capture_store(request))


@router.get("/sessions/{agent_id}/{session_id}")
def sessions_detail(request: Request, agent_id: str, session_id: str, offset: int = 0, limit: int = 200) -> dict:
    if limit > 1000:
        raise HTTPException(status_code=400, detail="limit max 1000")
    try:
        return read_session(_capture_store(request), agent_id, session_id, offset=offset, limit=limit)
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/sessions/{agent_id}/{session_id}/analyze")
def sessions_analyze(request: Request, agent_id: str, session_id: str, body: dict | None = None) -> dict:
    """Skill-driven 会话分析(icle-task-intelligence / analyze-session)。

    返回 SessionAnalysisProposal——只输出 Proposal,绝不落库;用户在前端
    接受/编辑/拒绝后才会创建 Task。需要 Settings 配置 LLM。
    """
    try:
        provider = resolve_provider(_store(request), role="extraction")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"LLM unavailable: {exc}")
    if provider.name == "none":
        raise HTTPException(
            status_code=400,
            detail="no LLM provider configured (Settings); session analysis needs one",
        )
    try:
        session = read_session(_capture_store(request), agent_id, session_id, limit=10_000)
        from ..intelligence import analyze_session

        proposal = analyze_session(
            provider, session["events"],
            language=(body or {}).get("lang") or "en",
        )
    except SessionError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"agent_id": agent_id, "session_id": session_id, "analysis": proposal}


@router.post("/sessions/{agent_id}/{session_id}/task-from-proposal")
def sessions_task_from_proposal(request: Request, agent_id: str, session_id: str, body: dict) -> dict:
    """接受 analyze 的 Proposal 任务 → 创建 Task(带 TaskProfile),跳转规划(§31)。

    body: {task: <proposal 的 task 子项>, provenance: <analysis.provenance>,
           project_id, project_path}。随后可继续 profile/plan/execute。
    """
    body = body or {}
    proposal_task = body.get("task") or {}
    if not proposal_task.get("title") and not proposal_task.get("goal"):
        raise HTTPException(status_code=400, detail="task title/goal required")
    try:
        read_session(_capture_store(request), agent_id, session_id, limit=1)
        task = create_task_from_proposal(
            _store(request),
            proposal_task=proposal_task,
            provenance=body.get("provenance"),
            project_id=str(body.get("project_id") or "default"),
            project_path=str(body.get("project_path") or Path.cwd()),
            source_sessions=[{"agent_id": agent_id, "session_id": session_id}],
        )
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": task}


@router.post("/sessions/task-preview")
def sessions_task_preview(request: Request, body: dict) -> dict:
    """Validate 1+ selected sessions and return an editable no-write preview."""
    try:
        return {"preview": preview_task_from_sessions(
            _capture_store(request), (body or {}).get("sessions") or []
        )}
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/sessions/task")
def sessions_create_merged_task(request: Request, body: dict) -> dict:
    """Create one draft Task from 1+ sessions after explicit UI confirmation."""
    body = body or {}
    try:
        task = create_task_from_sessions(
            _store(request),
            _capture_store(request),
            picks=body.get("sessions") or [],
            title=str(body.get("title") or ""),
            description=str(body.get("description") or ""),
            project_id=str(body.get("project_id") or "default"),
            project_path=str(body.get("project_path") or Path.cwd()),
        )
    except (SessionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": task}


@router.post("/sessions/{agent_id}/{session_id}/task")
def sessions_create_task(request: Request, agent_id: str, session_id: str, body: dict) -> dict:
    """Compatibility endpoint; still requires an explicit POST confirmation."""
    body = body or {}
    try:
        task = create_task_from_sessions(
            _store(request),
            _capture_store(request),
            picks=[{"agent_id": agent_id, "session_id": session_id}],
            title=str(body.get("title") or ""),
            description=str(body.get("request") or body.get("description") or ""),
            project_id=str(body.get("project_id") or "default"),
            project_path=str(body.get("project_path") or Path.cwd()),
        )
    except (SessionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": task}


@router.post("/sessions/extract-tasks")
def sessions_extract_tasks(request: Request, body: dict) -> dict:
    """Generate task Proposals from selected sessions without persisting them.

    Each candidate must still be accepted through task-from-proposal before a
    Task is created. Per-session failures are returned in the errors list.
    """
    body = body or {}
    picks = body.get("sessions") or []
    project_id = body.get("project_id") or "default"
    project_path = body.get("project_path") or str(Path.cwd())
    language = body.get("lang") or "en"
    if not picks:
        raise HTTPException(status_code=400, detail="sessions[] is required")
    try:
        provider = resolve_provider(_store(request), role="extraction")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"LLM unavailable: {exc}")
    if provider.name == "none":
        raise HTTPException(
            status_code=400,
            detail="no LLM provider configured (Settings); LLM task extraction needs one",
        )
    try:
        result = extract_tasks_from_sessions(
            _store(request), _capture_store(request), provider,
            picks=picks, project_id=str(project_id), project_path=str(project_path),
            language=language,
        )
    except SessionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result
