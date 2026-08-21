from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..agent_profiles import build_agent_profile, profile_summary
from ..capture import (
    CaptureError,
    capture_claude,
    capture_codex,
    capture_hermes,
    capture_kimi,
    capture_opencode,
    capture_pi,
)
from ..settings import resolve_provider
from ..skills import capture_llm
from ..discovery import (
    DiscoveryError,
    doctor_agents,
    link_agent,
    link_all_agents,
    scan_agents,
    unlink_agent,
)
from ..judge import route_suggest
from ..replay import list_replays
from ..external_evaluation import (
    dataset_quality,
    external_baseline,
    refresh_snapshots,
    resolve_model_identity,
)

router = APIRouter()


class ExternalEvaluationRefreshRequest(BaseModel):
    source_ids: list[str] | None = None
    timeout: float = Field(default=15.0, ge=1.0, le=60.0)

# agent_type → 原生会话源(仅实现 read-only adapter 的 agent)。
# 内置 adapter(格式已验证):hermes(SQLite)/kimi(wire.jsonl)/claude(projects
# jsonl)/codex(rollout jsonl)/opencode(opencode.db SQLite)/pi(agent/sessions
# jsonl)。gemini/aider/qwen 及未来小众 agent 走 LLM skill 兜底(capture-llm)。
CAPTURE_SOURCES = {
    "hermes": "~/.hermes/state.db",
    "kimi": "~/.kimi-code/sessions",
    "claude": "~/.claude/projects",
    "codex": "~/.codex/sessions",
    "opencode": "~/.local/share/opencode/opencode.db",
    "pi": "~/.pi/agent/sessions",
}


def _provider_model_targets(store: Path) -> list[dict]:
    from ..provider import list_providers

    targets = []
    for provider in list_providers(store):
        if provider.get("status") != "connected":
            continue
        if provider.get("type") not in {"openai-compatible", "local"}:
            continue
        if provider.get("type") != "local" and not provider.get("configured"):
            continue
        for model in provider.get("models", []):
            agent_id = f"{provider['provider_id']}/{model['id']}"
            targets.append({
                "agent_id": agent_id,
                "agent_type": "provider-model",
                "display_name": model.get("alias") or model["id"],
                "description": f"{provider['display_name']} model via {provider['type']} API.",
                "description_zh": f"通过 {provider['type']} API 使用的 {provider['display_name']} 模型。",
                "status": "connected",
                "installed": True,
                "linked": True,
                "execution_supported": True,
                "capture_supported": False,
                "capabilities": ["model_api", "text", "planning", "review"],
                "version": None,
                "provider_id": provider["provider_id"],
                "model_id": model["id"],
                "execution_billing_mode": (
                    "LOCAL_COMPUTE" if provider.get("type") == "local" else "API_METERED"
                ),
            })
    return targets


@router.get("/agents")
def agents(request: Request) -> dict:
    """Unified Agent directory: discovery + Provider Models + evidence profile."""
    store = Path(request.app.state.store)
    replays = list_replays(store)
    replay_counts: dict[str, int] = {}
    for entry in replays:
        replay_counts[entry["agent"]] = replay_counts.get(entry["agent"], 0) + 1
    discovered = scan_agents(
        store, capture_store=request.app.state.capture_store, probe=False
    )
    directory: dict[str, dict] = {
        item["agent_type"]: {
            "agent_id": item["agent_type"],
            "agent_type": item["agent_type"],
            "display_name": item["display_name"],
            "description": item.get("description", ""),
            "description_zh": item.get("description_zh", ""),
            "status": item["status"],
            "installed": item.get("executable_path") is not None,
            "linked": item["status"] == "linked",
            "execution_supported": item.get("execution_supported", False),
            "capture_supported": item.get("capture_supported", False),
            "capabilities": item.get("capabilities", []),
            "version": item.get("version"),
            "session_capture_count": item.get("session_capture_count", 0),
        }
        for item in discovered
    }
    provider_targets = _provider_model_targets(store)
    for item in provider_targets:
        directory[item["agent_id"]] = item
    candidate_ids = sorted(directory)
    suggestion = route_suggest(store, candidates=candidate_ids)
    shell = {r["agent"]: r for r in suggestion.get("preferences", suggestion.get("ranking", []))}
    evidence_ids = set(replay_counts) | set(shell)
    for agent_id in evidence_ids:
        directory.setdefault(agent_id, {
            "agent_id": agent_id,
            "agent_type": "historical",
            "display_name": agent_id,
            "description": "Historical Agent identity found in ICLE evidence.",
            "description_zh": "从 ICLE 历史证据中识别出的 Agent 身份。",
            "status": "historical",
            "installed": False,
            "linked": False,
            "execution_supported": False,
            "capture_supported": False,
            "capabilities": [],
            "version": None,
        })
    rows = []
    for agent_id, metadata in sorted(directory.items()):
        evidence = shell.get(agent_id, {})
        decision_profile = build_agent_profile(
            store, agent_id=agent_id, metadata=metadata
        )
        measurement = decision_profile.get("measurement") or {}
        observed_axes = [
            axis for axis, row in (measurement.get("axes") or {}).items()
            if (row or {}).get("status") == "observed"
        ]
        rows.append({
            **metadata,
            "replays": replay_counts.get(agent_id, 0),
            "decision_profile": profile_summary(decision_profile),
            "measurement": {
                "publication_status": measurement.get("publication_status"),
                "observed_axis_count": measurement.get("observed_axis_count", 0),
                "observed_axes": observed_axes,
            },
            "outcomes": evidence.get("outcomes", 0),
            "pairwise": evidence.get("pairwise", 0),
            "flags": evidence.get("flags", ["insufficient-data"]),
            "model_identity": resolve_model_identity(store, agent_id, metadata=metadata),
            "external_baseline": external_baseline(
                store,
                model_identity=resolve_model_identity(store, agent_id, metadata=metadata),
                agent_id=agent_id,
                domain="general",
            ),
        })
    rows.sort(key=lambda item: (
        -(item["measurement"]["observed_axis_count"] or 0),
        item["display_name"],
    ))
    return {"agents": rows}


@router.get("/agent-evaluations")
def agent_evaluations(request: Request, domain: str = "coding") -> dict:
    """Return model-matched external priors for the current Agent directory."""
    store = Path(request.app.state.store)
    return {
        "domain": domain,
        "agents": [
            {
                "agent_id": item["agent_id"],
                "model_identity": resolve_model_identity(store, item["agent_id"], metadata=item),
                "external_baseline": external_baseline(
                    store,
                    model_identity=resolve_model_identity(store, item["agent_id"], metadata=item),
                    agent_id=item["agent_id"],
                    domain=domain,
                ),
            }
            for item in _directory_items(request)
        ],
    }


@router.get("/agent-evaluations/quality")
def agent_evaluation_quality(request: Request) -> dict:
    """Read-only health report for cached external benchmark snapshots."""
    return dataset_quality(request.app.state.store)


@router.post("/agent-evaluations/refresh")
def refresh_agent_evaluations(
    request: Request,
    body: ExternalEvaluationRefreshRequest | None = None,
) -> dict:
    """Explicitly refresh public benchmark snapshots; stale cache is retained on failure."""
    body = body or ExternalEvaluationRefreshRequest()
    try:
        return refresh_snapshots(
            request.app.state.store,
            source_ids=body.source_ids,
            timeout=body.timeout,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"external evaluation refresh failed: {exc}")


def _directory_items(request: Request) -> list[dict]:
    """Build the same discovery/provider identity set without ranking recursion."""
    store = Path(request.app.state.store)
    discovered = scan_agents(store, capture_store=request.app.state.capture_store, probe=False)
    items = [
        {
            "agent_id": item["agent_type"],
            "agent_type": item["agent_type"],
            "display_name": item["display_name"],
            "execution_supported": item.get("execution_supported", False),
        }
        for item in discovered
    ]
    return [*items, *_provider_model_targets(store)]


@router.get("/execution-targets")
def execution_targets(request: Request) -> dict:
    """Targets with verified adapters for Tasks, Replay-like work and Collabs."""
    store = Path(request.app.state.store)
    local = [
        {
            "agent_id": item["agent_type"],
            "agent_type": item["agent_type"],
            "display_name": item["display_name"],
            "description": item.get("description", ""),
            "description_zh": item.get("description_zh", ""),
            "kind": "local-agent",
            "version": item.get("version"),
        }
        for item in scan_agents(
            store, capture_store=request.app.state.capture_store, probe=False
        )
        if item["status"] == "linked" and item.get("execution_supported")
    ]
    models = [{**item, "kind": "provider-model"} for item in _provider_model_targets(store)]
    return {"targets": [*local, *models]}


# ------------------------------------------------- v0.4 P9: local agent discovery


@router.get("/agents/discovery")
def agent_discovery(request: Request) -> dict:
    """Scan local agent CLIs (read-only; never runs login/chat/install)."""
    store = request.app.state.store
    capture_store = request.app.state.capture_store
    return {"agents": scan_agents(store, capture_store=capture_store)}


@router.get("/agents/doctor")
def agent_doctor(request: Request) -> dict:
    """Per-agent health table + summary (§13)."""
    store = request.app.state.store
    capture_store = request.app.state.capture_store
    return doctor_agents(store, capture_store=capture_store)


@router.post("/agents/{agent_type}/link")
def agent_link(request: Request, agent_type: str) -> dict:
    """User explicitly links a detected agent (discovery ≠ authorization)."""
    try:
        record = link_agent(request.app.state.store, agent_type)
    except DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"agent_type": agent_type, "linked_at": record["linked_at"]}


@router.post("/agents/{agent_type}/unlink")
def agent_unlink(request: Request, agent_type: str) -> dict:
    unlink_agent(request.app.state.store, agent_type)
    return {"agent_type": agent_type, "linked": False}


@router.post("/agents/link-all")
def agent_link_all(request: Request) -> dict:
    """一键链接所有可运行 agent(§11「Link all detected」);单个可随时 unlink。"""
    return link_all_agents(request.app.state.store)


@router.post("/agents/{agent_type}/capture-llm")
def agent_capture_llm(request: Request, agent_type: str) -> dict:
    """小众 agent 的 skill 模式捕获(附加功能):无内置 adapter 时,接入 LLM
    后按 skills/capture/<agent>.md 由 LLM 现场解析原始会话(需 Settings 配置 LLM)。

    无 skill / 无 LLM / 解析校验失败 → 400 明确报错,绝不静默写脏数据。
    """
    try:
        provider = resolve_provider(Path(request.app.state.store), role="extraction")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"LLM unavailable: {exc}")
    if provider.name == "none":
        raise HTTPException(
            status_code=400,
            detail="no LLM provider configured (Settings); skill capture needs an LLM",
        )
    try:
        result = capture_llm(agent_type, request.app.state.capture_store, provider)
    except CaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"agent_type": agent_type, "mode": "llm-skill", **result}


@router.post("/agents/{agent_type}/capture")
def agent_capture(request: Request, agent_type: str) -> dict:
    """用户点击某 agent → 获取其历史会话(§48 Session Capture,无 LLM)。

    只读 hermes/kimi 的原生会话源,标准化写入 capture-store(append-only,
    已有事件跳过)。未实现 adapter 的 agent 返回 400(明确,不静默)。
    """
    source = CAPTURE_SOURCES.get(agent_type)
    if source is None:
        raise HTTPException(
            status_code=400,
            detail=f"no built-in adapter for {agent_type}; if it is a niche agent, configure an LLM and use capture-llm (skill mode)",
        )
    try:
        if agent_type == "hermes":
            result = capture_hermes(Path(source).expanduser(), request.app.state.capture_store)
        elif agent_type == "claude":
            result = capture_claude(Path(source).expanduser(), request.app.state.capture_store)
        elif agent_type == "codex":
            result = capture_codex(Path(source).expanduser(), request.app.state.capture_store)
        elif agent_type == "opencode":
            result = capture_opencode(Path(source).expanduser(), request.app.state.capture_store)
        elif agent_type == "pi":
            result = capture_pi(Path(source).expanduser(), request.app.state.capture_store)
        else:
            result = capture_kimi(Path(source).expanduser(), request.app.state.capture_store)
    except CaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"agent_type": agent_type, **result}
