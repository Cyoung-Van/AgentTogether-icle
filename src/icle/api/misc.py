"""Judgments, agent profiles, recommend, activity, settings, collabs API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..active import get_budget, set_budget
from ..judge import record_judgment, record_result_mark
from ..localtime import local_day_key
from ..rating import list_ratings, rating_summary, record_rating
from ..replay import ReplayError, assert_replay_id
from ..router import executable_candidates, recommend_agent
from ..settings import (
    SettingsError,
    get_system_settings,
    list_intelligence_models,
    resolve_provider,
    save_system_settings,
)
from ..agent_profiles import build_agent_profile
from ..external_evaluation import rank_agent
from ..judge import route_suggest

router = APIRouter()


def _episode_path(store: Path, episode_id: str) -> Path:
    try:
        from ..episode import _assert_episode_id

        _assert_episode_id(episode_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    path = store / "episodes" / f"{episode_id}.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown episode: {episode_id}")
    return path


def _replay_doc(store: Path, replay_id: str) -> dict[str, Any]:
    try:
        assert_replay_id(replay_id)
    except ReplayError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    path = store / "replays" / replay_id / "replay.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown replay: {replay_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid replay record: {replay_id}") from exc


def _reference_episode(store: Path, episode_id: str) -> None:
    _episode_path(store, episode_id)


def _episode_agents(store: Path, episode_id: str) -> set[str]:
    episode = json.loads(_episode_path(store, episode_id).read_text(encoding="utf-8"))
    agents = {str(episode.get("source_agent_revision", {}).get("agent_id") or "")}
    for replay in _load_all(store / "replays", pattern="rp-*/replay.json"):
        if replay.get("episode_id") == episode_id:
            agents.add(str(replay.get("target_agent_revision", {}).get("agent_id") or ""))
    return {agent for agent in agents if agent}


def _require_episode_agent(store: Path, episode_id: str, agent_id: str) -> None:
    if agent_id not in _episode_agents(store, episode_id):
        raise HTTPException(
            status_code=400,
            detail=f"agent {agent_id!r} did not participate in episode {episode_id}",
        )


class JudgmentRequest(BaseModel):
    pair: list[str] | None = None
    episode: str | None = None
    kind: str
    reason_tags: list[str] = []
    note: str = ""


@router.post("/judgments")
def create_judgment(request: Request, body: JudgmentRequest) -> dict:
    store = Path(request.app.state.store)
    if body.pair:
        if len(body.pair) != 2:
            raise HTTPException(status_code=400, detail="pair must have exactly two replay ids")
        first = _replay_doc(store, body.pair[0])
        second = _replay_doc(store, body.pair[1])
        if first.get("episode_id") != second.get("episode_id"):
            raise HTTPException(status_code=400, detail="replays must belong to the same episode")
        episode_id = str(first.get("episode_id") or "")
        _reference_episode(store, episode_id)
        subject = {"type": "replay_pair", "a": body.pair[0], "b": body.pair[1]}
    elif body.episode:
        episode_id = body.episode
        _reference_episode(store, episode_id)
        subject = {"type": "episode", "id": episode_id}
    else:
        raise HTTPException(status_code=400, detail="pair or episode required")
    try:
        result = record_judgment(
            request.app.state.store,
            subject=subject,
            kind=body.kind,
            episode_id=episode_id,
            reason_tags=body.reason_tags,
            note=body.note,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


class MarkRequest(BaseModel):
    episode_id: str
    agent_id: str
    mark: str
    note: str = ""


@router.post("/marks")
def create_mark(request: Request, body: MarkRequest) -> dict:
    store = Path(request.app.state.store)
    _require_episode_agent(store, body.episode_id, body.agent_id)
    try:
        return record_result_mark(
            request.app.state.store,
            episode_id=body.episode_id,
            mark=body.mark,
            agent_id=body.agent_id,
            note=body.note,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class RatingRequest(BaseModel):
    agent_id: str
    episode_id: str
    dimensions: dict[str, int]
    overall_preference: int
    would_use_again: str
    comment: str = ""


@router.post("/ratings")
def create_rating(request: Request, body: RatingRequest) -> dict:
    store = Path(request.app.state.store)
    _require_episode_agent(store, body.episode_id, body.agent_id)
    try:
        return record_rating(
            request.app.state.store,
            agent_id=body.agent_id,
            episode_id=body.episode_id,
            dimensions=body.dimensions,
            overall_preference=body.overall_preference,
            would_use_again=body.would_use_again,
            comment=body.comment,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/ratings")
def ratings(request: Request, agent_id: str | None = None) -> dict:
    store = request.app.state.store
    return {"ratings": list_ratings(store, agent_id=agent_id)}


@router.get("/agent-profile")
def agent_profile_query(request: Request, agent_id: str) -> dict:
    """Agent profile query form supports provider/model IDs containing '/'."""
    return _agent_profile(request, agent_id)


@router.get("/agents/{agent_id}")
def agent_profile(request: Request, agent_id: str) -> dict:
    """Compatibility route for simple local-agent IDs."""
    return _agent_profile(request, agent_id)


def _profile_metadata(request: Request, agent_id: str) -> dict[str, Any]:
    """Resolve non-sensitive catalog metadata for an Agent detail projection."""
    store = Path(request.app.state.store)
    if "/" in agent_id:
        provider_id, _, model_id = agent_id.partition("/")
        try:
            from ..provider import list_providers

            for provider in list_providers(store):
                if provider.get("provider_id") != provider_id:
                    continue
                if any(item.get("id") == model_id for item in provider.get("models", [])):
                    return {
                        "agent_type": "provider-model",
                        "provider_id": provider_id,
                        "model_id": model_id,
                        "capabilities": ["model_api", "text", "planning", "review"],
                        "execution_billing_mode": (
                            "LOCAL_COMPUTE" if provider.get("type") == "local" else "API_METERED"
                        ),
                    }
        except Exception:
            pass
        return {"agent_type": "provider-model", "provider_id": provider_id, "model_id": model_id}
    try:
        from ..discovery import scan_agents

        for item in scan_agents(store, capture_store=request.app.state.capture_store, probe=False):
            if item.get("agent_type") == agent_id:
                return {
                    "agent_type": agent_id,
                    "capabilities": item.get("capabilities", []),
                    "version": item.get("version"),
                }
    except Exception:
        pass
    return {}


def _agent_profile(request: Request, agent_id: str) -> dict:
    store = Path(request.app.state.store)
    marks = _load_all(store / "marks")
    replays_dir = store / "replays"
    replays = _load_all(replays_dir, pattern="rp-*/replay.json")
    judgments = _load_all(store / "judgments")

    own_marks = [m for m in marks if m.get("agent_id") == agent_id]
    own_replays = [r for r in replays if r["target_agent_revision"]["agent_id"] == agent_id]
    positive = sum(1 for m in own_marks if m["mark"] in {"accept", "adopted", "adopted_with_minor_edits"})
    negative = sum(1 for m in own_marks if m["mark"] in {"reject", "redo", "abandoned_result", "requested_rework"})

    wins = losses = ties = 0
    for judgment in judgments:
        subject = judgment.get("subject", {})
        if subject.get("type") != "replay_pair":
            continue
        agents = {}
        for key in ("a", "b"):
            try:
                assert_replay_id(subject[key])  # S2: 防穿越
            except Exception:
                continue
            replay_json = replays_dir / subject[key] / "replay.json"
            if replay_json.is_file():
                agents[key] = json.loads(replay_json.read_text(encoding="utf-8"))[
                    "target_agent_revision"
                ]["agent_id"]
        if agent_id not in agents.values():
            continue
        kind = judgment.get("kind")
        if kind == "tie":
            ties += 1
        elif kind in {"prefer_a", "prefer_b"}:
            winner_key = "a" if kind == "prefer_a" else "b"
            if agents.get(winner_key) == agent_id:
                wins += 1
            else:
                losses += 1

    durations = [r.get("duration_ms", 0) for r in own_replays if r.get("duration_ms")]
    local_ranking = {
        item["agent"]: item
        for item in route_suggest(store, candidates=[agent_id])["ranking"]
    }
    local = local_ranking.get(agent_id, {})
    evaluation = rank_agent(
        store,
        agent_id=agent_id,
        local={
            "score": local.get("score"),
            "outcomes": local.get("outcomes", 0),
            "pairwise": local.get("pairwise", 0),
        },
        metadata={"agent_type": "provider-model"} if "/" in agent_id else None,
        domain="general",
    )
    profile_metadata = _profile_metadata(request, agent_id)
    return {
        "agent_id": agent_id,
        "episodes": len({m["episode_id"] for m in own_marks}),
        "accepted": positive,
        "rejected": negative,
        "replays": len(own_replays),
        "pairwise": {"wins": wins, "losses": losses, "ties": ties},
        "median_duration_ms": sorted(durations)[len(durations) // 2] if durations else None,
        "recent_marks": own_marks[-5:],
        "user_rating": rating_summary(store, agent_id),
        "evaluation": evaluation,
        # Read-only Agent + model decision profile.  It does not make a
        # routing or execution decision and never changes existing evidence.
        "decision_profile": build_agent_profile(
            store, agent_id=agent_id, metadata=profile_metadata
        ),
    }


class RecommendRequest(BaseModel):
    task: str
    project_id: str | None = None
    # COST-10:五维推荐(可选)。routes=[{agent, model, provider, ...}] + policy;
    # 无 routes 时回退旧 evidence 推荐。
    routes: list[dict] | None = None
    policy: str | None = None
    profile: dict | None = None


@router.post("/recommend")
def recommend(request: Request, body: RecommendRequest) -> dict:
    try:
        if body.routes:
            from .. import value as value_lib
            from ..settings import get_system_settings

            policy = body.policy or get_system_settings(request.app.state.store)["value_policy"]
            profile = body.profile or _infer_profile(body.task)
            candidates = [
                value_lib.build_route_candidate(
                    request.app.state.store, profile=profile,
                    route={**route, "strategy": route.get("strategy", "DIRECT")},
                    policy=policy)
                for route in body.routes
            ]
            ranked = value_lib.rank_candidates(candidates, policy=policy)
            return {
                "schema_version": "icle-route-candidates/v0.1",
                "task": {"task": body.task, "profile": profile, "policy": policy},
                "routes": ranked,
                "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            }
        candidates = executable_candidates(
            request.app.state.store,
            capture_store=request.app.state.capture_store,
        )
        return recommend_agent(
            request.app.state.store,
            task=body.task,
            project_id=body.project_id,
            candidates=candidates,
            task_profile=body.profile,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _infer_profile(task_text: str) -> dict:
    """Rule-based profile inference for value recommendations (New Task flow)."""
    from ..task import profile_for_task

    return profile_for_task(task_text)


@router.get("/similar")
def similar(request: Request, query: str, project_id: str | None = None, limit: int = 10) -> dict:
    from ..episode import list_episodes, show_episode
    from ..intelligence import similar_episodes

    store = request.app.state.store
    episodes = [show_episode(store, e["episode_id"]) for e in list_episodes(store)]
    return {
        "similar": similar_episodes(episodes, query, project_id=project_id, limit=limit)
    }


@router.get("/activity")
def activity(request: Request, limit: int = 50) -> dict:
    """Unified recent-activity feed from judgments, marks and replays."""
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    store = Path(request.app.state.store)
    items: list[dict[str, Any]] = []
    for mark in _load_all(store / "marks"):
        items.append(
            {
                "type": "mark",
                "at": mark.get("created_at"),
                "text": f"{mark.get('agent_id')} marked {mark.get('mark')} on {mark.get('episode_id')}",
                "agent_id": mark.get("agent_id"),
                "mark": mark.get("mark"),
                "episode_id": mark.get("episode_id"),
                "target": "episode",
                "ref": mark.get("episode_id"),
            }
        )
    for judgment in _load_all(store / "judgments"):
        subject = judgment.get("subject") or {}
        ref = subject.get("id") if subject.get("type") == "episode" else None
        if ref is None and subject.get("type") == "replay_pair":
            try:
                ref = _replay_doc(store, subject.get("a", "")).get("episode_id")
            except HTTPException:
                ref = None
        items.append(
            {
                "type": "judgment",
                "at": judgment.get("created_at"),
                "text": f"{judgment.get('judge', 'human')} judged {judgment.get('kind')}",
                "judge": judgment.get("judge", "human"),
                "judgment_kind": judgment.get("kind"),
                "target": "episode" if ref else None,
                "ref": ref,
            }
        )
    for run in _load_all(store / "replays", pattern="rp-*/replay.json"):
        items.append(
            {
                "type": "replay",
                "at": run.get("created_at"),
                "text": f"{run['target_agent_revision']['agent_id']} replayed {run['episode_id']} → {run['status']}",
                "agent_id": run["target_agent_revision"]["agent_id"],
                "episode_id": run["episode_id"],
                "status": run["status"],
                "target": "episode",
                "ref": run.get("episode_id"),
            }
        )
    items.sort(key=lambda item: item.get("at") or "", reverse=True)
    return {"activity": items[:limit]}


class PricingCatalogRefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout: float = Field(default=30.0, ge=1.0, le=60.0)


@router.get("/pricing-catalog")
def pricing_catalog(request: Request) -> dict:
    """Licensed live pricing source status; never returns provider secrets."""
    from ..tariff import pricing_catalog_status

    return pricing_catalog_status(request.app.state.store)


@router.post("/pricing-catalog/refresh")
def refresh_pricing_catalog(
    request: Request,
    body: PricingCatalogRefreshRequest | None = None,
) -> dict:
    """Explicitly refresh models.dev; invalid/failing data keeps old cache."""
    from ..tariff import sync_litellm, sync_models_dev

    timeout = (body or PricingCatalogRefreshRequest()).timeout
    result = sync_models_dev(request.app.state.store, timeout=timeout)
    fallback = sync_litellm(request.app.state.store, timeout=timeout)
    result["fallback"] = fallback
    if not result.get("synced"):
        suffix = "; previous valid catalog retained" if result.get("available") else ""
        raise HTTPException(
            status_code=502,
            detail=f"{result.get('reason') or 'pricing refresh failed'}{suffix}",
        )
    return result


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replay_per_day: int | None = Field(default=None, ge=1, le=1000)
    default_language: str | None = None
    value_policy: str | None = None
    monthly_cost_budget_usd: float | None = Field(default=None, gt=0, le=1_000_000)
    cost_budget_enabled: bool | None = None
    cost_warning_percent: int | None = Field(default=None, ge=1, le=100)


@router.get("/settings")
def settings(request: Request) -> dict:
    store = request.app.state.store
    replay = get_budget(store)
    system = get_system_settings(store)
    from ..active import remaining_budget
    from ..cost import cost_budget_status, list_actuals
    from ..provider import list_providers
    from ..task import list_tasks
    from ..tariff import pricing_catalog_status

    try:
        provider = resolve_provider(store)
        provider_name = getattr(provider, "name", "none")
        model = getattr(provider, "model", "")
        intelligence_provider = f"{provider_name}/{model}" if model else provider_name
    except Exception:
        intelligence_provider = "unavailable"
    models = list_intelligence_models(store)
    providers = list_providers(store)
    tasks = list_tasks(store)
    episodes_dir = Path(store) / "episodes"
    actuals = list_actuals(store, limit=100_000)
    return {
        **system,
        "replay_per_day": replay["per_day"],
        "replay_used_today": replay["used"].get(local_day_key(), 0),
        "replay_remaining_today": remaining_budget(store),
        "intelligence_provider": intelligence_provider,
        "intelligence_selection": models.get("current"),
        "intelligence_models": models.get("models", []),
        "execution_provider": "direct_cli + provider_api",
        "providers": {
            "total": len(providers),
            "connected": sum(1 for item in providers if item.get("status") == "connected"),
            "configured": sum(1 for item in providers if item.get("configured")),
        },
        "cost_budget": cost_budget_status(store),
        "pricing_catalog": pricing_catalog_status(store),
        "data": {
            "tasks": len(tasks),
            "episodes": len(list(episodes_dir.glob("ep-*.json"))) if episodes_dir.is_dir() else 0,
            "cost_records": len(actuals),
        },
        "safety": {
            "approval_required": True,
            "append_only_evidence": True,
            "replay_isolated": True,
            "secrets_masked": True,
        },
    }


@router.post("/settings")
def update_settings(request: Request, body: SettingsRequest) -> dict:
    store = request.app.state.store
    current = get_system_settings(store)
    budget_enabled = body.cost_budget_enabled
    if budget_enabled is None:
        next_cost_budget = current["monthly_cost_budget_usd"]
    elif budget_enabled:
        next_cost_budget = body.monthly_cost_budget_usd or current["monthly_cost_budget_usd"]
        if next_cost_budget is None:
            raise HTTPException(status_code=400, detail="monthly cost budget amount is required")
    else:
        next_cost_budget = None
    try:
        save_system_settings(
            store,
            default_language=body.default_language or current["default_language"],
            value_policy=body.value_policy or current["value_policy"],
            monthly_cost_budget_usd=next_cost_budget,
            cost_warning_percent=body.cost_warning_percent or current["cost_warning_percent"],
        )
        if body.replay_per_day is not None:
            set_budget(store, body.replay_per_day)
    except (SettingsError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return settings(request)


@router.get("/collabs")
def collabs(request: Request) -> dict:
    store = request.app.state.store
    return {
        "collabs": [
            _public_collab(item)
            for item in _load_all(store / "collabs", pattern="co-*/collab.json")
        ]
    }


@router.get("/collab-template-recommendation")
def collab_template_recommendation(request: Request, episode_id: str) -> dict:
    """Recommend one of the same 12 fixed Task templates for an Episode."""
    from .. import templates as template_lib

    path = _episode_path(Path(request.app.state.store), episode_id)
    episode = json.loads(path.read_text(encoding="utf-8"))
    task_text = episode.get("task_start", {}).get("original_user_request", "")
    result = template_lib.recommend_template({
        "title": task_text[:200],
        "description": task_text,
        "profile": None,
    })
    return {**result, "episode_id": episode_id}


class CollabRequest(BaseModel):
    mode: str  # handoff | parallel | review | template
    episode_id: str
    agents: list[str] = Field(default_factory=list)
    template_id: str = ""
    assignments: list[dict[str, str]] = Field(default_factory=list)
    timeout_sec: float = 600


def _public_collab(result: dict[str, Any]) -> dict[str, Any]:
    """Remove local filesystem paths while keeping auditable step evidence."""
    return {key: value for key, value in result.items() if key != "run_dir"}


@router.post("/collabs")
def run_collab(request: Request, body: CollabRequest) -> dict:
    """执行协作模式(补齐缺口:此前 collab.py 只有核心无 API 端点)。

    mode: handoff(A→B 接力)/parallel(独立结果对比)/review(A 产出+B 评审)/
    template(固定任务模板多阶段工作流)。每次协作消耗 replay 预算一次。
    """
    from .. import collab as collab_lib
    from ..active import BudgetError, consume_budget
    from ..task import execution_target_available, executor_for_target

    store = request.app.state.store
    if body.mode not in {"handoff", "parallel", "review", "template"}:
        raise HTTPException(status_code=400, detail=f"unknown mode: {body.mode}")
    if body.mode == "template":
        try:
            collab_lib.validate_template_workflow(body.template_id, body.assignments)
        except collab_lib.CollabError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        selected_agents = [str(item.get("agent") or "") for item in body.assignments]
    else:
        selected_agents = body.agents
        if len(body.agents) < 2 or len(set(body.agents)) != len(body.agents):
            raise HTTPException(
                status_code=400,
                detail="collab requires at least 2 distinct agents/models",
            )
    selected_agents = [agent for agent in selected_agents if agent]
    if len(set(selected_agents)) < 2:
        raise HTTPException(status_code=400, detail="collab requires at least 2 distinct agents/models")
    if not (store / "episodes" / f"{body.episode_id}.json").is_file():
        raise HTTPException(status_code=404, detail=f"unknown episode: {body.episode_id}")

    executor_factory = getattr(request.app.state, "executor_factory", None)
    unavailable = [
        agent for agent in sorted(set(selected_agents))
        if not execution_target_available(store, agent, executor_factory=executor_factory)
    ]
    if unavailable:
        raise HTTPException(
            status_code=400,
            detail=f"no collaboration executor for: {', '.join(unavailable)}",
        )
    try:
        consume_budget(store, reason=f"collab:{body.mode}:{body.episode_id}")
    except BudgetError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    def executor_for(agent_id: str):
        return executor_for_target(
            store, agent_id, executor_factory=executor_factory
        )

    try:
        if body.mode == "handoff":
            result = collab_lib.handoff(
                store, body.episode_id,
                agent_a=body.agents[0], agent_b=body.agents[1],
                executor_a=executor_for(body.agents[0]),
                executor_b=executor_for(body.agents[1]),
                timeout_sec=body.timeout_sec,
            )
        elif body.mode == "parallel":
            result = collab_lib.parallel_compare(
                store, body.episode_id,
                agents=[(agent, executor_for(agent)) for agent in body.agents],
                timeout_sec=body.timeout_sec,
            )
        elif body.mode == "review":
            result = collab_lib.author_reviewer(
                store, body.episode_id,
                author=body.agents[0], reviewer=body.agents[1],
                executor_author=executor_for(body.agents[0]),
                executor_reviewer=executor_for(body.agents[1]),
                timeout_sec=body.timeout_sec,
            )
        else:
            result = collab_lib.template_workflow(
                store,
                body.episode_id,
                template_id=body.template_id,
                assignments=body.assignments,
                executor_factory=executor_for,
                timeout_sec=body.timeout_sec,
            )
    except Exception as exc:  # noqa: BLE001 — surface collab errors cleanly
        raise HTTPException(status_code=400, detail=str(exc)[:300])
    return {"collab": _public_collab(result)}


def _load_all(directory: Path, pattern: str = "*.json") -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob(pattern)):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return out


class SkillFeedbackRequest(BaseModel):
    skill: str
    mode: str
    action: str
    session_id: str = ""
    candidate_id: str = ""
    detail: str = ""


@router.post("/skills/feedback")
def skill_feedback(request: Request, body: SkillFeedbackRequest) -> dict:
    """记录用户对 skill 产物的 accept/edit/reject 行为(§30 自我评估)。"""
    from ..skill_feedback import SkillFeedbackError, record_feedback

    try:
        event = record_feedback(
            request.app.state.store,
            skill=body.skill, mode=body.mode, action=body.action,
            session_id=body.session_id, candidate_id=body.candidate_id,
            detail=body.detail,
        )
    except SkillFeedbackError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"event": event}


@router.get("/skills/stats")
def skill_stats(request: Request) -> dict:
    """Skill 评估统计:各 mode 的 accept/edit/reject 分布与接受率。"""
    from ..skill_feedback import feedback_stats

    return feedback_stats(request.app.state.store)
