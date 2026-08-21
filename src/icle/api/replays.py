"""Replay + events API (UI-06/07): first side-effecting endpoints.

Replays run in background threads; status is pushed over an in-process event
bus (WS /api/events) AND persisted via core replay.json. The API layer only
orchestrates core functions.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from ..discovery import scan_agents
from ..replay import (
    DIRECT_CLI_AGENTS,
    ReplayError,
    assert_replay_id,
    list_replays,
    make_direct_executor,
    record_failed_replay,
    replay,
)

router = APIRouter()


class ReplayRequest(BaseModel):
    agent_id: str
    context_mode: str = "CLEAN"


class EventBus:
    """Minimal in-process pub/sub for replay status (single-user local).

    S4: publish 由后台线程调用 —— 经 loop.call_soon_threadsafe 调度,
    asyncio.Queue 不被跨线程直接触碰(原实现存在丢事件/运行时异常风险)。
    """

    def __init__(self) -> None:
        self.subscribers: list[asyncio.Queue] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        for queue in list(self.subscribers):
            try:
                if queue.full():
                    continue
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                continue


def _bus(app: Any) -> EventBus:
    if not hasattr(app.state, "bus"):
        app.state.bus = EventBus()
    return app.state.bus


def _publish(request: Request, event: dict[str, Any]) -> None:
    _bus(request.app).publish(event)


def _next_replay_id(store: Path) -> str:
    """M1: 原子保留 replay ID —— O_EXCL 创建空目录,并发请求不会拿到同一 ID。

    预创建的目录由后台线程的 replay(replay_id=...) 复用;即使线程失败,
    空目录残留也只消耗一个序号,不会覆盖任何已有记录。
    """
    replays_dir = store / "replays"
    replays_dir.mkdir(parents=True, exist_ok=True)
    n = len(sorted(replays_dir.glob("rp-*/")))
    while True:
        n += 1
        candidate = f"rp-{n:04d}"
        try:
            (replays_dir / candidate).mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            continue


@router.post("/episodes/{episode_id}/replays")
def start_replay(request: Request, episode_id: str, body: ReplayRequest) -> dict:
    store = request.app.state.store
    capture_store = request.app.state.capture_store
    # 先校验参数(非法请求不消耗预算),再扣预算硬上限(补齐小缺口)
    from ..episode import EpisodeError, _assert_episode_id

    try:
        _assert_episode_id(episode_id)
    except EpisodeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not (store / "episodes" / f"{episode_id}.json").is_file():
        raise HTTPException(status_code=404, detail=f"unknown episode: {episode_id}")
    if body.agent_id not in DIRECT_CLI_AGENTS:
        raise HTTPException(status_code=400, detail=f"unknown agent: {body.agent_id}")
    if body.context_mode not in {"CLEAN", "PROJECT_STATE", "SELECTED_HISTORY"}:
        raise HTTPException(status_code=400, detail="unsupported context_mode")
    # M4: SELECTED_HISTORY 的关键前置条件在扣费前同步校验 ——
    # 无效请求不再烧预算(原实现校验发生在后台线程,预算先被扣掉)。
    if body.context_mode == "SELECTED_HISTORY":
        episode_doc = json.loads(
            (store / "episodes" / f"{episode_id}.json").read_text(encoding="utf-8")
        )
        turn_range = episode_doc.get("task_start", {}).get("turn_range")
        if not turn_range:
            raise HTTPException(
                status_code=400,
                detail="SELECTED_HISTORY requires the episode to have a turn_range",
            )
        source_agent = episode_doc.get("source_agent_revision", {}).get("agent_id", "")
        source_session = episode_doc.get("source_session", "")
        events_path = Path(capture_store) / source_agent / source_session / "events.jsonl"
        if not events_path.is_file():
            raise HTTPException(
                status_code=400,
                detail=f"captured session missing: {source_agent}/{source_session}",
            )
    try:
        from ..active import BudgetError, consume_budget

        consume_budget(store, reason=f"replay:{episode_id}")
    except BudgetError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    replay_id = _next_replay_id(store)
    executor_factory = getattr(request.app.state, "executor_factory", None)
    capture_store = request.app.state.capture_store
    _publish(
        request,
        {"type": "replay.status", "replay_id": replay_id, "episode_id": episode_id, "status": "queued"},
    )

    def run() -> None:
        _publish(
            request,
            {"type": "replay.status", "replay_id": replay_id, "episode_id": episode_id, "status": "running"},
        )
        revision = {
            "schema_version": "icle-agent-revision/v0.1",
            "agent_id": body.agent_id,
            "revision_id": "webui-replay",
            "model": "unknown", "cli": "unknown", "provider": "unknown",
            "persona_sha256": None, "memory_sha256": None, "tools_sha256": None,
            "execution_provider": "direct_cli",
            "created_at": "2026-08-14T00:00:00Z",
        }
        try:
            run_result = replay(
                store,
                episode_id,
                target_agent_revision=revision,
                mode=body.context_mode,
                capture_store=capture_store,
                replay_id=replay_id,  # M1: API 返回 ID 与落盘 ID 一致
                executor=(
                    executor_factory(body.agent_id)
                    if executor_factory
                    else make_direct_executor(body.agent_id)
                ),
            )
            status = run_result["status"]
        except Exception as exc:  # surfaced and persisted as failed, never swallowed
            status = "failed"
            record_failed_replay(
                store,
                replay_id=replay_id,
                episode_id=episode_id,
                target_agent_revision=revision,
                mode=body.context_mode,
                error=f"replay error: {exc}",
            )
            _publish(
                request,
                {
                    "type": "replay.output",
                    "replay_id": replay_id,
                    "episode_id": episode_id,
                    "text": f"replay error: {exc}"[:300],
                },
            )
        _publish(
            request,
            {
                "type": "replay.status",
                "replay_id": replay_id,
                "episode_id": episode_id,
                "status": status,
            },
        )

    threading.Thread(target=run, daemon=True).start()
    return {"replay_id": replay_id, "status": "queued"}


@router.get("/replay-targets")
def replay_targets(request: Request) -> dict:
    """Return direct-CLI replay targets with current local discovery state."""
    detected = {item["agent_type"]: item for item in scan_agents(request.app.state.store)}
    targets = []
    for agent_type in sorted(DIRECT_CLI_AGENTS):
        item = detected.get(agent_type, {})
        targets.append({
            "agent_type": agent_type,
            "display_name": item.get("display_name", agent_type),
            "status": item.get("status", "unavailable"),
            "available": item.get("status") == "linked",
            "version": item.get("version"),
        })
    return {"targets": targets}


@router.get("/replays")
def replays(request: Request, episode: str | None = None) -> dict:
    return {"replays": list_replays(request.app.state.store, episode)}


def _workspace_diff(workspace: Path) -> str:
    if not (workspace / ".git").is_dir():
        return ""
    completed = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    return completed.stdout


@router.get("/replays/{replay_id}")
def replay_detail(request: Request, replay_id: str) -> dict:
    store = request.app.state.store
    try:
        assert_replay_id(replay_id)  # S2
    except ReplayError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    replay_json = store / "replays" / replay_id / "replay.json"
    if not replay_json.is_file():
        raise HTTPException(status_code=404, detail=f"unknown replay: {replay_id}")
    run = json.loads(replay_json.read_text(encoding="utf-8"))
    root = store / "replays" / replay_id
    stdout = (root / "stdout.log").read_text(encoding="utf-8") if (root / "stdout.log").is_file() else ""
    stderr = (root / "stderr.log").read_text(encoding="utf-8") if (root / "stderr.log").is_file() else ""
    return {
        "replay": run,
        "stdout_tail": stdout[-4000:],
        "stderr_tail": stderr[-2000:],
        "workspace_diff": _workspace_diff(root / "workspace"),
    }


@router.get("/compare")
def compare(request: Request, a: str, b: str) -> dict:
    """Side-by-side data for two replays of the same episode."""
    first = replay_detail(request, a)
    second = replay_detail(request, b)
    if first["replay"]["episode_id"] != second["replay"]["episode_id"]:
        raise HTTPException(status_code=400, detail="replays belong to different episodes")
    return {
        "episode_id": first["replay"]["episode_id"],
        "a": first,
        "b": second,
    }


_ALLOWED_WS_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}


@router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    # S4: 校验 Origin(浏览器 WebSocket 不受 CORS 限制,任意网页可直连本机端口);
    # S5: 配置 ICLE_TOKEN 时 WS 也要求 query token 匹配。
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    same_origin = False
    if origin and host:
        parsed = urlsplit(origin)
        same_origin = parsed.netloc == host and parsed.scheme in {"http", "https"}
    if origin and origin not in _ALLOWED_WS_ORIGINS and not same_origin:
        await websocket.close(code=1008)
        return
    token = os.environ.get("ICLE_TOKEN", "")
    if token and websocket.query_params.get("token", "") != token:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    bus = _bus(websocket.app)
    bus.attach(asyncio.get_running_loop())
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    bus.subscribers.append(queue)
    try:
        await websocket.send_json({"type": "connected"})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if queue in bus.subscribers:
            bus.subscribers.remove(queue)
