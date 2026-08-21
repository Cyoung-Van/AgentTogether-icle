"""FastAPI app for the ICLE WebUI (UI-01).

Architecture discipline: the API layer calls ICLE Core functions DIRECTLY —
the same functions the CLI uses. No business logic is reimplemented here and
no store files are read by anything except the core layer.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..intelligence_status import IntelligenceStatus


def _intelligence_operation(path: str, method: str) -> str | None:
    if method != "POST":
        return None
    if path == "/api/sessions/extract-tasks":
        return "extract_tasks"
    if path.startswith("/api/sessions/") and path.endswith("/analyze"):
        return "analyze_session"
    if path.startswith("/api/agents/") and path.endswith("/capture-llm"):
        return "capture_session"
    if not path.startswith("/api/tasks/"):
        return None
    suffixes = (
        ("/split-proposal", "split_task"),
        ("/split-refine", "refine_split"),
        ("/plan-from-template", "customize_template"),
        ("/plan/llm", "plan_task"),
        ("/replan-llm", "replan_task"),
        ("/judge-llm", "judge_result"),
        ("/analyze", "analyze_task"),
    )
    return next((operation for suffix, operation in suffixes if path.endswith(suffix)), None)


def _cost_context(path: str, operation: str | None) -> dict[str, str]:
    context = {"operation": operation} if operation else {}
    if path.startswith("/api/tasks/"):
        parts = path.split("/")
        if len(parts) > 3 and parts[3]:
            context["task_id"] = parts[3]
    return context

DEFAULT_STORE = Path(os.environ.get("ICLE_STORE", "./experience-store"))
CAPTURE_STORE = Path(os.environ.get("ICLE_CAPTURE_STORE", "./capture-store"))


def create_app(
    store: str | Path | None = None,
    capture_store: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="ICLE", version=__version__)
    app.state.store = Path(store) if store else DEFAULT_STORE
    app.state.capture_store = Path(capture_store) if capture_store else CAPTURE_STORE
    app.state.intelligence_status = IntelligenceStatus()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_api_token(request: Request, call_next):
        """Protect state-changing and data APIs when ICLE_TOKEN is configured.

        Health stays public so the UI can distinguish an offline server from an
        authenticated one. The browser sends the token as X-ICLE-Token; Bearer
        is accepted as well for curl and other API clients.
        """
        token = os.environ.get("ICLE_TOKEN", "").strip()
        if token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            if request.method == "OPTIONS":
                return await call_next(request)
            provided = request.headers.get("x-icle-token", "")
            authorization = request.headers.get("authorization", "")
            if not provided and authorization.lower().startswith("bearer "):
                provided = authorization[7:].strip()
            if not hmac.compare_digest(provided, token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "ICLE_TOKEN required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        operation = _intelligence_operation(request.url.path, request.method)
        operation_id = app.state.intelligence_status.start(operation) if operation else None
        from ..metering import reset_cost_context, set_cost_context

        context_token = set_cost_context(**_cost_context(request.url.path, operation))
        try:
            response = await call_next(request)
        except Exception:
            if operation_id:
                app.state.intelligence_status.finish(operation_id, outcome="failed")
            raise
        finally:
            reset_cost_context(context_token)
        if operation_id:
            app.state.intelligence_status.finish(
                operation_id,
                outcome="completed" if response.status_code < 400 else "failed",
            )
        return response

    from . import agents, control, episodes, health, misc, providers, replays, sessions, tasks

    app.include_router(health.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(episodes.router, prefix="/api")
    app.include_router(replays.router, prefix="/api")
    app.include_router(misc.router, prefix="/api")
    app.include_router(providers.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(control.router, prefix="/api")
    app.include_router(sessions.router, prefix="/api")

    # production: serve the built WebUI (web/dist) at /
    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.is_dir():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str) -> FileResponse:
            candidate = dist / full_path
            if full_path and candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            # index.html 必须 no-cache:每次重新验证,避免浏览器引用旧 JS;
            # 带 hash 的 assets(/assets/*)保持默认可缓存,内容变更 hash 即变。
            return FileResponse(
                dist / "index.html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
    return app
