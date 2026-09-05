"""Task API (UI-20..29): thin HTTP layer over the Task core.

Discipline (plan §35): the API calls Task core functions directly, never
reimplements business rules. LLM endpoints resolve the configured provider
and treat LLM output as PROPOSALS that pass central schema validation before
any state mutation. With no LLM configured they fail loudly (400) so the UI
degrades to the manual path, never to silent magic.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import task as task_core
from ..settings import resolve_provider


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

router = APIRouter()


def _is_json_retryable(exc: Exception) -> bool:
    """S6: 只有 JSON 解析/格式类失败才值得重试一次。

    Provider 错误、网络、超时、鉴权失败重试只会双倍扣费且照样 400 —— 禁止。
    """
    from .. import intelligence

    return isinstance(exc, intelligence.IntelligenceError) and "json" in str(exc).lower()


def _store(request: Request) -> Path:
    return Path(request.app.state.store)


def _llm_provider(request: Request, *, role: str = "general"):
    """Resolve the configured intelligence provider (none → HTTP 400)."""
    try:
        provider = resolve_provider(_store(request), role=role)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"LLM unavailable: {exc}")
    if provider.name == "none":
        raise HTTPException(
            status_code=400,
            detail="no LLM provider configured (Settings); use the manual path",
        )
    return provider


# ------------------------------------------------------------------- CRUD (UI-20)


class TaskCreate(BaseModel):
    title: str = Field(..., max_length=200)          # S7: 输入长度上限
    description: str = Field(default="", max_length=5000)
    project_id: str = Field(default="default", max_length=200)
    project_path: str = Field(default="", max_length=2000)


@router.post("/tasks")
def create_task(request: Request, body: TaskCreate) -> dict:
    try:
        return task_core.create_task(
            _store(request),
            title=body.title,
            description=body.description,
            project_id=body.project_id,
            project_path=body.project_path,
        )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/tasks")
def list_tasks(request: Request) -> dict:
    return {"tasks": task_core.list_tasks(_store(request))}


@router.get("/tasks/{task_id}")
def show_task(request: Request, task_id: str) -> dict:
    try:
        return task_core.show_task(_store(request), task_id)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class ChildrenRequest(BaseModel):
    children: list[dict]


@router.post("/tasks/{task_id}/split-proposal")
def split_proposal(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM 拆分子任务建议 · 阶段1(propose):返回两套不同维度的拆分雏形。

    用户从中选一套 → POST /tasks/{task_id}/split-refine 精细化;
    确认后走 POST /tasks/{task_id}/children 落库。全部不落库。
    """
    provider = _llm_provider(request, role="planning")
    task = task_core.show_task(_store(request), task_id)
    try:
        from .. import intelligence

        # LLM 偶发返回非 JSON → 仅 JSON 类失败自动重试一次(2 次调用上限,S6)
        try:
            proposal = intelligence.propose_split_task(
                provider, task=task,
                language=str((body or {}).get("lang") or "en"),
            )
        except Exception as exc:
            if not _is_json_retryable(exc):
                raise
            proposal = intelligence.propose_split_task(
                provider, task=task,
                language=str((body or {}).get("lang") or "en"),
            )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": task_id, "proposal": proposal}


@router.post("/tasks/{task_id}/split-refine")
def split_refine(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM 拆分子任务建议 · 阶段2(refine):精细化用户选中的一套雏形。

    body.candidate 由前端把 split-proposal 返回的某个 candidate 原样回传
    (Proposal 不落库,无状态);输出单套完整 children,仍由用户确认后落库。
    """
    provider = _llm_provider(request, role="planning")
    task = task_core.show_task(_store(request), task_id)
    candidate = (body or {}).get("candidate")
    if not isinstance(candidate, dict):
        raise HTTPException(status_code=400, detail="body.candidate (the chosen split candidate) is required")
    try:
        from .. import intelligence

        proposal = intelligence.refine_split_task(
            provider, task=task, candidate=candidate,
            language=str((body or {}).get("lang") or "en"),
        )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": task_id, "proposal": proposal}


@router.get("/templates")
def list_templates_api(primary_type: str | None = None) -> dict:
    """固定拆分模板列表(单一来源 templates.py);?primary_type 过滤 + Blank 常驻。"""
    from .. import templates as template_lib

    return {"templates": template_lib.list_templates(primary_type=primary_type)}


@router.post("/tasks/{task_id}/recommend-template")
def recommend_template_api(request: Request, task_id: str) -> dict:
    """Task Analyzer:规则推荐最合适的拆分模板(确定性,无 LLM)。"""
    from .. import templates as template_lib

    task = task_core.show_task(_store(request), task_id)
    return template_lib.recommend_template(task)


@router.post("/tasks/{task_id}/plan-from-template")
def plan_from_template_api(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM 按固定模板骨架做语义定制(template-customize mode)。

    body.template_id 选定模板;LLM 只具体化各步骤 title/description,不增删
    骨架;输出 TemplatePlanProposal(不落库),用户确认后走 /children。
    """
    provider = _llm_provider(request, role="planning")
    task = task_core.show_task(_store(request), task_id)
    template_id = str((body or {}).get("template_id") or "")
    from .. import templates as template_lib

    template = template_lib.get_template(template_id)
    if template is None:
        raise HTTPException(status_code=400, detail=f"unknown template: {template_id}")
    try:
        from .. import intelligence

        # LLM 偶发返回非 JSON → 仅 JSON 类失败自动重试一次(2 次调用上限,S6)
        try:
            proposal = intelligence.plan_from_template(
                provider, task=task, template=template,
                language=str((body or {}).get("lang") or "en"),
            )
        except Exception as exc:
            if not _is_json_retryable(exc):
                raise
            proposal = intelligence.plan_from_template(
                provider, task=task, template=template,
                language=str((body or {}).get("lang") or "en"),
            )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": task_id, "proposal": proposal}


@router.get("/tasks/{task_id}/cost-estimates")
def task_cost_estimates(request: Request, task_id: str) -> dict:
    """Compare every currently executable Agent/Model for this Task.

    This is a read-only projection.  It uses the saved TaskProfile when
    available and an explicit deterministic rule profile for a new draft.
    No Plan, route authorization, CostActual, or historical evidence is
    created by viewing estimates.
    """
    from ..agent_profiles import estimate_agent_task_cost
    from ..discovery import scan_agents
    from ..provider import list_providers

    store = _store(request)
    task = task_core.show_task(store, task_id)
    profile = task.get("profile") or task_core.profile_for_task(
        task.get("description") or "", task.get("title") or ""
    )
    profile_source = "saved" if task.get("profile") else "rule_preview"
    targets: list[dict] = []
    for item in scan_agents(
        store,
        capture_store=request.app.state.capture_store,
        probe=False,
    ):
        if item.get("status") != "linked" or not item.get("execution_supported"):
            continue
        targets.append({
            "agent_id": item["agent_type"],
            "display_name": item.get("display_name") or item["agent_type"],
            "kind": "local-agent",
            "metadata": {
                "agent_type": item["agent_type"],
                "capabilities": item.get("capabilities", []),
                "execution_billing_mode": "UNKNOWN",
            },
        })
    for provider in list_providers(store):
        if provider.get("status") != "connected":
            continue
        if provider.get("type") not in {"openai-compatible", "local"}:
            continue
        if provider.get("type") != "local" and not provider.get("configured"):
            continue
        for model in provider.get("models", []):
            model_id = model.get("id")
            if not model_id:
                continue
            targets.append({
                "agent_id": f"{provider['provider_id']}/{model_id}",
                "display_name": model.get("alias") or model_id,
                "kind": "provider-model",
                "metadata": {
                    "agent_type": "provider-model",
                    "provider_id": provider["provider_id"],
                    "model_id": model_id,
                    "capabilities": ["model_api", "text", "planning", "review"],
                    "execution_billing_mode": (
                        "LOCAL_COMPUTE" if provider.get("type") == "local" else "API_METERED"
                    ),
                },
            })
    estimates = []
    for target in targets:
        estimate = estimate_agent_task_cost(
            store,
            agent_id=target["agent_id"],
            task_profile=profile,
            metadata=target["metadata"],
        )
        estimates.append({
            **estimate,
            "display_name": target["display_name"],
            "kind": target["kind"],
        })
    estimates.sort(
        key=lambda item: (
            item.get("api_equivalent_cost") is None,
            float(item.get("api_equivalent_cost") or 0),
            str(item.get("display_name") or item.get("agent_id")),
        )
    )
    return {
        "schema_version": "icle-task-agent-cost-comparison/v0.1",
        "task_id": task_id,
        "profile_source": profile_source,
        "profile": {
            "primary_type": profile.get("primary_type"),
            "subtype": profile.get("subtype"),
            "difficulty": profile.get("difficulty"),
        },
        "estimates": estimates,
        "generated_at": _now(),
    }


@router.post("/tasks/{task_id}/route-prediction")
def route_prediction_api(request: Request, task_id: str, body: dict | None = None) -> dict:
    """五维预测(COST-07/08):对给定 route(agent/model/provider)输出 Quality/
    Cost/Time/Intervention + Confidence/Evidence。供 Router 与 Execution Options UI。
    """
    from .. import cost as cost_lib

    task = task_core.show_task(_store(request), task_id)
    profile = task.get("profile") or {}
    route = (body or {}).get("route") or {}
    agent = str(route.get("agent") or "")
    model = str(route.get("model") or "")
    provider = str(route.get("provider") or "")
    if not agent:
        raise HTTPException(status_code=400, detail="route.agent required")
    if not model:
        raise HTTPException(status_code=400, detail="route.model required")
    route_key = {"agent": agent, "model": model, "provider": provider,
                 "execution_provider": str(route.get("execution_provider") or "direct_cli"),
                 "context_policy": str(route.get("context_policy") or "PROJECT_STATE")}
    prediction = cost_lib.route_prediction(
        _store(request), profile=profile, route=route_key, provider=provider, model=model)
    prediction["task_id"] = task_id
    prediction["profile"] = {"primary_type": profile.get("primary_type"),
                             "subtype": profile.get("subtype"), "difficulty": profile.get("difficulty")}
    return {"prediction": prediction}


@router.post("/tasks/{task_id}/execution-options")
def execution_options_api(request: Request, task_id: str, body: dict | None = None) -> dict:
    """COST-10 执行选项:对任务的可用 route(providers 默认模型)输出五维
    RouteCandidate + policy 排序。供 Task Studio Execution Options UI。
    """
    from .. import value as value_lib

    store = _store(request)
    task = task_core.show_task(store, task_id)
    from ..settings import get_system_settings

    policy = str((body or {}).get("policy") or get_system_settings(store)["value_policy"])
    profile = task.get("profile") or {}

    # 可用 route = providers.json 里 role 含 general 的 provider 的每个模型
    routes: list[dict[str, Any]] = []
    import json as _json

    providers_path = Path(store) / "providers.json"
    if providers_path.is_file():
        try:
            providers = _json.loads(providers_path.read_text(encoding="utf-8"))
        except _json.JSONDecodeError:
            providers = {}
        for provider_id, config in providers.items():
            config = config or {}
            roles = config.get("roles") or []
            if roles and "general" not in roles:
                continue
            display = config.get("display_name") or provider_id
            for model_entry in (config.get("models") or []):
                model_id = model_entry.get("id") if isinstance(model_entry, dict) else model_entry
                if not model_id:
                    continue
                routes.append({
                    "agent": provider_id, "model": model_id, "provider": display,
                    "execution_provider": "direct_cli", "context_policy": "PROJECT_STATE",
                })

    candidates = [
        value_lib.build_route_candidate(store, profile=profile, route=route, policy=policy)
        for route in routes
    ]
    ranked = value_lib.rank_candidates(candidates, policy=policy)
    return {
        "schema_version": "icle-route-candidates/v0.1",
        "task_id": task_id,
        "policy": policy,
        "routes": ranked,
        "created_at": _now(),
    }


def ai_assign_agents(request: Request, task_id: str, body: dict | None = None) -> dict:
    """AI 一键分配执行 agent(用户需求):对任务(及子任务)按 required_capabilities/
    difficulty 匹配已链接 agent 的 capabilities;有 LLM 时可用 LLM 精调,规则版
    确定性兜底(免费)。返回 {task_id: agent} 赋值表,不落库(由调用方使用)。
    """
    from ..discovery import scan_agents

    store = _store(request)
    linked = [
        a for a in scan_agents(store)
        if a["status"] == "linked" and a.get("execution_supported", False)
    ]
    if not linked:
        return {"assignments": {}, "reason": "no linked execution agent available"}

    targets: list[dict[str, Any]] = [{"task_id": task_id, "title": "", "profile": {}}]
    # M5: 用 list_children 构建目标池 —— subtask_projection 没有 children 键,
    # 旧实现导致子任务从未进入分配(assign-agents / execute-tree llm_assign 失效)。
    for child in task_core.list_children(store, task_id):
        targets.append({"task_id": child["task_id"], "title": child.get("title", ""), "profile": {}})

    assignments: dict[str, str] = {}
    planned_targets: list[dict[str, Any]] = []
    skipped: list[str] = []
    for target in targets:
        task = task_core.show_task(store, target["task_id"])
        target["profile"] = task.get("profile") or {}
        target["plan"] = task.get("plan") or {}
        if target["plan"].get("steps"):
            planned_targets.append(target)
        else:
            skipped.append(target["task_id"])

    # 规则匹配:required_capabilities(或难度)∩ agent capabilities → 覆盖最多者
    for target in planned_targets:
        profile = target.get("profile") or {}
        plan = target.get("plan") or {}
        required: set[str] = set()
        for step in plan.get("steps") or []:
            required.update(step.get("required_capabilities") or [])
        if not required:
            required = {"shell", "filesystem"}
        best, best_score = None, -1
        for agent in linked:
            caps = set(agent.get("capabilities") or [])
            score = len(required & caps)
            if score > best_score:
                best, best_score = agent["agent_type"], score
        assignments[target["task_id"]] = best or linked[0]["agent_type"]
    return {
        "assignments": assignments,
        "skipped": skipped,
        "reason": "rule-based capability match",
    }


@router.post("/tasks/{task_id}/assign-agents")
def assign_agents_api(request: Request, task_id: str, body: dict | None = None) -> dict:
    """AI 一键分配(有 LLM 时可选 llm=true 精调;默认规则版确定性兜底)。"""
    result = ai_assign_agents(request, task_id, body)
    if (body or {}).get("llm") and not result.get("assignments"):
        pass  # 无 agent 可用时即使要求 LLM 也返回空
    return result


@router.post("/tasks/{task_id}/assign-agents/apply")
def apply_agent_assignments_api(request: Request, task_id: str, body: dict | None = None) -> dict:
    """Apply a reviewed assignment proposal and require re-approval."""
    assignments = (body or {}).get("assignments")
    if not isinstance(assignments, dict):
        raise HTTPException(status_code=400, detail="body.assignments must be an object")
    try:
        return task_core.apply_agent_assignments(_store(request), task_id, assignments)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/execute-tree")
def execute_tree(request: Request, task_id: str, body: dict | None = None) -> dict:
    """子任务顺序执行 + 协同(用户需求):父任务先执行,子任务按 sibling_order
    依次执行;body.agents = {task_id: agent} 为每个任务指定执行 agent
    (子任务可分别选 agent);body.llm_assign=true 时用 AI 一键分配。
    任一步失败则停止后续(顺序协同)。
    """
    from ..discovery import scan_agents

    store = _store(request)
    body = body or {}
    agents: dict[str, str] = body.get("agents") or {}
    children = task_core.list_children(store, task_id)
    chain: list[dict[str, Any]] = [{"task_id": task_id, "order": 0, "agent": agents.get(task_id, "")}]
    for index, child in enumerate(children, start=1):
        chain.append({"task_id": child["task_id"], "order": index, "agent": agents.get(child["task_id"], "")})

    if body.get("llm_assign"):
        assign = ai_assign_agents(request, task_id, body)
        for entry in chain:
            entry["agent"] = assign.get("assignments", {}).get(entry["task_id"], entry["agent"])

    # Validate the complete chain before running anything. This preserves the
    # parent -> child atomicity users expect from the sequential workflow.
    executor_factory = getattr(request.app.state, "executor_factory", None)
    for entry in chain:
        try:
            current = task_core.show_task(store, entry["task_id"])
        except task_core.TaskError as exc:
            return {"status": "failed", "chain": [], "error": str(exc), "task_id": entry["task_id"]}
        plan = current.get("plan") or {}
        if plan.get("status") != "approved":
            return {
                "status": "failed", "chain": [],
                "error": f"{entry['task_id']} plan is not approved",
                "task_id": entry["task_id"],
            }
        selected = entry["agent"] or None
        targets = [selected] if selected else [s.get("recommended_agent", "") for s in plan.get("steps", [])]
        if not targets or any(not task_core.execution_target_available(store, target, executor_factory=executor_factory) for target in targets):
            return {
                "status": "failed", "chain": [],
                "error": f"{entry['task_id']} has an unavailable execution target",
                "task_id": entry["task_id"],
            }

    results: list[dict[str, Any]] = []
    for entry in chain:
        try:
            run = task_core.run_task(
                store, entry["task_id"],
                executor_factory=getattr(request.app.state, "executor_factory", None),
                override_agent=entry["agent"] or None,
            )
        except Exception as exc:
            return {
                "status": "failed", "chain": results,
                "error": str(exc)[:300], "task_id": entry["task_id"],
            }
        results.append({
            "task_id": entry["task_id"], "order": entry["order"],
            "agent": entry["agent"], "status": run.get("status"), "run_id": run.get("run_id"),
        })
        if run.get("status") != "completed":
            # 顺序协同:失败即停,不继续后续子任务
            return {"status": "failed", "chain": results, "error": f"{entry['task_id']} failed"}
    return {"status": "completed", "chain": results, "task_id": task_id}


@router.post("/tasks/{task_id}/children")
def create_children(request: Request, task_id: str, body: ChildrenRequest) -> dict:
    """创建子任务(Task Hierarchy,all-or-nothing)。

    子任务是独立 Task(parent_task_id 指向父);继承 project_id,其余从 draft
    独立开始;深度受 MAX_TASK_DEPTH 策略限制;任一 child 非法 → 全部不创建。
    """
    try:
        created = task_core.create_children(
            _store(request), task_id, body.children,
            creation_source="manual_split",
        )
    except task_core.TaskTreeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"children": created, "count": len(created)}


@router.get("/tasks/{task_id}/children")
def list_children(request: Request, task_id: str) -> dict:
    """直属子任务(仅一层,按 sibling_order 排序)。"""
    try:
        children = task_core.list_children(_store(request), task_id)
        projection = task_core.subtask_projection(_store(request), task_id)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"children": children, "projection": projection}


@router.delete("/tasks/{task_id}")
def delete_task(request: Request, task_id: str) -> dict:
    """删除任务;有子任务的父任务拒绝(409 parent_has_children,避免 orphan)。"""
    try:
        deleted = task_core.delete_task(_store(request), task_id)  # S1: 核心层校验 ID
    except task_core.TaskTreeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"deleted": deleted}


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    project_id: str | None = Field(default=None, max_length=200)
    project_path: str | None = Field(default=None, max_length=2000)


@router.patch("/tasks/{task_id}")
def update_task(request: Request, task_id: str, body: TaskPatch) -> dict:
    patch = body.model_dump(exclude_unset=True)
    try:
        return task_core.update_task(_store(request), task_id, **patch)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/end")
def end_task(request: Request, task_id: str) -> dict:
    """Explicit user cancellation; never fabricates a successful Run/Episode."""
    try:
        return task_core.end_task(_store(request), task_id)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks/{task_id}/archive")
def archive_task(request: Request, task_id: str) -> dict:
    """Archive a completed/ended task while preserving its lifecycle status."""
    try:
        return task_core.set_task_archived(_store(request), task_id, archived=True)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/tasks/{task_id}/unarchive")
def unarchive_task(request: Request, task_id: str) -> dict:
    try:
        return task_core.set_task_archived(_store(request), task_id, archived=False)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ------------------------------------------------------------------- profile (UI-22/23)


class ProfileRequest(BaseModel):
    # manual profile path: full TaskProfile fields
    primary_type: str | None = None
    subtype: str = ""
    difficulty: str | None = None
    risk: str | None = None
    context_requirement: str | None = None
    tool_requirement: list[str] = []
    estimated_duration: str | None = None
    decomposition: str | None = None
    review: str | None = None
    reason: str = ""


@router.post("/tasks/{task_id}/profile")
def set_profile(request: Request, task_id: str, body: ProfileRequest) -> dict:
    """Manual profile (No-LLM path): fixed standard values chosen by the user."""
    data = body.model_dump(exclude_unset=True)
    profile = {"schema_version": "icle-task-profile/v0.1", "source": "manual"}
    for key, value in data.items():
        if value not in (None, "", []):
            profile[key] = value
    try:
        return task_core.set_task_profile(_store(request), task_id, profile)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/analyze")
def analyze_task(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM Task Profiler (UI-23): returns a validated 'AI suggested' profile.

    body.lang (optional) = UI language; threaded into the prompt so the LLM
    writes prose (reason) in the user's language.
    """
    provider = _llm_provider(request, role="extraction")
    task = task_core.show_task(_store(request), task_id)
    language = (body or {}).get("lang") or "en"
    try:
        from .. import intelligence

        proposal = intelligence.propose_task_profile(
            provider, title=task["title"], description=task["description"],
            language=language,
        )
        profile = {**proposal, "schema_version": "icle-task-profile/v0.1"}
        profile = task_core.set_task_profile(_store(request), task_id, profile)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": profile, "ai_suggested": True}


# ------------------------------------------------------------------- plan (UI-24/25)


class PlanRequest(BaseModel):
    strategy: str
    steps: list[dict]
    requires_user_approval: bool = True


@router.post("/tasks/{task_id}/plan")
def save_plan(request: Request, task_id: str, body: PlanRequest) -> dict:
    """Save a manual plan (No-LLM path) as 'proposed'."""
    try:
        return task_core.save_plan(
            _store(request),
            task_id,
            strategy=body.strategy,
            steps=body.steps,
            planner="manual",
            requires_user_approval=body.requires_user_approval,
        )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/plan/llm")
def plan_llm(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM Task Planner (UI-25): proposal validated centrally, saved as proposed."""
    provider = _llm_provider(request, role="planning")
    task = task_core.show_task(_store(request), task_id)
    profile = task.get("profile") or {}
    language = (body or {}).get("lang") or "en"
    try:
        from .. import intelligence

        proposal = intelligence.plan_task(
            provider,
            title=task["title"],
            description=task["description"] or task["title"],
            difficulty=profile.get("difficulty", "D3"),
            risk=profile.get("risk", "R1"),
            context_requirement=profile.get("context_requirement", "MEDIUM"),
            decomposition=profile.get("decomposition", "recommended"),
            project_path=task.get("project_path") or "",
            language=language,
        )
        # Skill 输出 1-3 个 PlanCandidate;默认保存候选 A(语义结构由 skill 定,
        # 具体排序留给 CostEngine/RecommendationEngine 后续接入)。
        best = proposal["candidates"][0]
        result = task_core.save_plan(
            _store(request),
            task_id,
            strategy=best["execution_pattern"],
            steps=[_skill_step_to_task_step(s) for s in best["steps"]],
            planner="llm-skill",
        )
        # 返回全部候选,前端可 A/B/C 切换(§14:Planner 不决定最终方案)
        candidates = [
            {
                "candidate_id": c["candidate_id"],
                "execution_pattern": c["execution_pattern"],
                "summary": c.get("summary", ""),
                "expected_complexity": c.get("expected_complexity", ""),
                "steps": [_skill_step_to_task_step(s) for s in c["steps"]],
            }
            for c in proposal["candidates"]
        ]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": result, "ai_suggested": True, "candidates": candidates}


class PlanStepsRequest(BaseModel):
    steps: list[dict]
    strategy: str | None = None


def _skill_step_to_task_step(step: dict) -> dict:
    """Skill PlanStep(schema v0.1)→ ICLE TaskStep(icle-task-step/v0.1)。

    Skill 只给语义结构(goal/expected_outputs/dependencies/verification dict);
    TaskStep 需要 title/description/type/depends_on/verification str。转换时
    保留 required_capabilities,recommended_agent 留空(由 Router 决定)。
    """
    verification = step.get("verification") or {}
    return {
        "step_id": str(step.get("step_id") or "").split("-")[-1],
        "title": step.get("goal") or step.get("step_id") or "Step",
        "description": step.get("goal") or "",
        "type": "other",
        "context_policy": step.get("context_policy") or "CLEAN",
        "risk": step.get("risk") or "R0",
        # dependencies 可能是候选内引用(S1)或带候选前缀(A-S1);统一取最后段,
        # 同时保留步骤身份与依赖，避免位置变化使引用指向错误步骤
        "depends_on": [str(d).split("-")[-1] for d in (step.get("dependencies") or [])],
        "required_capabilities": list(step.get("required_capabilities") or []),
        "expected_output": "\n".join(step.get("expected_outputs") or []),
        "verification": verification.get("type", "") if isinstance(verification, dict) else str(verification or ""),
    }


@router.post("/tasks/{task_id}/plan/steps")
def update_steps(request: Request, task_id: str, body: PlanStepsRequest) -> dict:
    """Plan Editor save (UI-26): replace steps, keep identity, mark revised."""
    try:
        return task_core.update_plan_steps(
            _store(request), task_id, steps=body.steps, strategy=body.strategy
        )
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/plan/approve")
def approve_plan(request: Request, task_id: str) -> dict:
    """User approves the plan (passes UI-28 Execution Review)."""
    try:
        return task_core.approve_plan(_store(request), task_id)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------- execute (UI-27/29)


@router.post("/tasks/{task_id}/replan-llm")
def replan_llm(request: Request, task_id: str, body: dict | None = None) -> dict:
    """LLM Replanner (skill replan-task): failure classification + local replan.

    输入原 plan + 失败信息,返回 ReplanProposal(只输出,不落库);前端预览
    kept/replaced/new steps 后决定是否应用(应用走 plan/steps 端点)。
    """
    provider = _llm_provider(request, role="planning")
    task = task_core.show_task(_store(request), task_id)
    plan = task.get("plan") or {}
    if not plan.get("steps"):
        raise HTTPException(status_code=400, detail="task has no plan to replan")
    body = body or {}
    try:
        from .. import intelligence

        proposal = intelligence.replan_task(
            provider,
            original_plan=plan,
            failure=str(body.get("failure") or "step failed"),
            progress=str(body.get("progress") or ""),
            language=str(body.get("lang") or "en"),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": task_id, "replan": proposal}


@router.post("/tasks/{task_id}/candidates")
def task_candidates(request: Request, task_id: str) -> dict:
    """Route Candidates (v0.4 §30/§44): A/B/C 多方案 + 成本报价。

    Uses the task's profile; available agents = linked local agents + provider
    models (deepseek/xxx). Deterministic rules, no LLM.
    """
    from ..discovery import scan_agents
    from ..planning import plan_candidates

    store = _store(request)
    task = task_core.show_task(store, task_id)
    profile = task.get("profile") or {}
    difficulty = profile.get("difficulty", "D3")
    risk = profile.get("risk", "R1")
    # available: linked local agents + configured provider models
    linked_agents = [
        a["agent_type"] for a in scan_agents(store)
        if a["status"] == "linked" and a.get("execution_supported", False)
    ]
    provider_models = []
    try:
        from ..provider import list_providers

        for provider in list_providers(store):
            if provider.get("status") != "connected":
                continue
            if provider.get("type") not in {"openai-compatible", "local"}:
                continue
            if provider.get("type") != "local" and not provider.get("configured"):
                continue
            for model in provider.get("models", []):
                provider_models.append(f"{provider['provider_id']}/{model['id']}")
    except Exception:  # noqa: BLE001 — candidates are best-effort
        pass
    available = linked_agents + provider_models
    if not available:
        raise HTTPException(
            status_code=409,
            detail="no executable route available; link Kimi/Hermes or connect an OpenAI-compatible provider",
        )
    try:
        candidates = plan_candidates(
            store,
            title=task["title"],
            description=task["description"],
            difficulty=difficulty,
            risk=risk,
            agents=available,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return candidates


@router.post("/tasks/{task_id}/execute")
def execute_task(request: Request, task_id: str, body: dict | None = None) -> dict:
    """Execute an approved plan step by step (UI-29).

    Real agents come from make_direct_executor (replay seam); tests inject
    fakes via app.state.executor_factory (same pattern as the replay API).
    Execution consumes the daily budget per step. Optional body.agent
    overrides every plan step's recommended_agent for THIS run only (H2/H3:
    不再经 save_plan 落盘 —— 旧实现会把已批准计划重置为 proposed 导致必然
    失败,且覆盖用户 Plan Editor 的选择)。
    """
    try:
        agent = str((body or {}).get("agent") or "").strip()
        executor_factory = getattr(request.app.state, "executor_factory", None)
        run = task_core.run_task(
            _store(request), task_id,
            executor_factory=executor_factory,
            override_agent=agent or None,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"run": run, "task": task_core.show_task(_store(request), task_id)}


@router.get("/tasks/{task_id}/run")
def task_run(request: Request, task_id: str) -> dict:
    run = task_core.latest_run(_store(request), task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="task has no run yet")
    return run


# ------------------------------------------------------------------- evaluation (UI-30)


@router.post("/tasks/{task_id}/accept")
def accept_task(request: Request, task_id: str, agent: str = "") -> dict:
    """Accept the result: creates an Episode + accept mark (§27)."""
    try:
        return task_core.accept_task(_store(request), task_id, agent=agent)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/tasks/{task_id}/baseline-probe")
def baseline_probe_status(request: Request, task_id: str, agent: str) -> dict:
    """How far this install is from a usable A for the given agent."""
    from .. import baseline

    store = _store(request)
    task_core.show_task(store, task_id)
    status = baseline.probe_status(store, agent)
    status["task_id"] = task_id
    return status


@router.post("/tasks/{task_id}/baseline-probe")
def run_baseline_probe(request: Request, task_id: str, agent: str) -> dict:
    """Re-run the task on the agent's bare model to calibrate A locally.

    A needs at least two probed tasks on an axis before the engine trusts it, so
    a single probe is expected to report `ready: false`.
    """
    from .. import baseline

    store = _store(request)
    try:
        return baseline.run_baseline_probe(
            store,
            task_id,
            agent=agent,
            executor_factory=getattr(request.app.state, "executor_factory", None),
        )
    except baseline.BaselineError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/tasks/{task_id}/report")
def task_report(request: Request, task_id: str) -> dict:
    """The fixed evaluation form: per-agent forms + the consolidated one.

    Consolidation follows the intelligence layer: off → the finishing agent's
    form; on → a summary over every subtask form on the same template.
    """
    from .. import report as report_core

    store = _store(request)
    try:
        provider = _llm_provider(request, role="general")
    except HTTPException:
        provider = None
    try:
        entries = task_core.collect_report_entries(store, task_id)
        consolidated = task_core.consolidate_task_report(store, task_id, provider=provider)
    except task_core.TaskError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {
        "task_id": task_id,
        "report_contract_id": report_core.REPORT_CONTRACT_ID,
        "template": report_core.blank_report(),
        "fields": list(report_core.FORM_FIELD_NAMES),
        "metric_contract": [dict(metric) for metric in report_core.METRIC_CONTRACT],
        "intelligence_enabled": provider is not None,
        "entries": entries,
        "consolidated": consolidated,
    }


@router.post("/tasks/{task_id}/report")
def fill_task_report(request: Request, task_id: str, body: dict) -> dict:
    """Fill the form by hand when an agent did not return one.

    The form is attached to the task's last step, so the finishing-agent rule
    keeps working and the origin stays auditable.
    """
    from .. import report as report_core

    try:
        return task_core.set_manual_report(_store(request), task_id, body or {})
    except report_core.ReportError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except task_core.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/tasks/{task_id}/judge-llm")
def judge_llm(request: Request, task_id: str, agent: str = "") -> dict:
    """LLM Judge (UI-30): 5-dimension rating on the task result (source=llm)."""
    provider = _llm_provider(request, role="judging")
    store = _store(request)
    task = task_core.show_task(store, task_id)
    run = task_core.latest_run(store, task_id)
    if run is None:
        raise HTTPException(status_code=400, detail="task has no run to judge")
    run_agents = sorted({
        str(step.get("agent") or "")
        for step in run.get("steps", [])
        if str(step.get("agent") or "")
    })
    agent = agent.strip()
    if not agent:
        if len(run_agents) == 1:
            agent = run_agents[0]
        elif len(run_agents) > 1:
            raise HTTPException(
                status_code=400,
                detail=f"multiple execution agents; choose one of {', '.join(run_agents)}",
            )
    if not agent:
        raise HTTPException(status_code=400, detail="cannot attribute result to any agent")
    if run_agents and agent not in run_agents:
        raise HTTPException(status_code=400, detail=f"agent {agent!r} did not execute this task")
    result_text = "\n\n".join(
        f"STEP {s['step_id']} ({s['agent']})\n{s.get('stdout_tail', '')}" for s in run["steps"]
    )
    try:
        from .. import intelligence
        from ..rating import list_ratings, record_rating

        if any(
            rating.get("source") == "llm"
            and rating.get("task_id") == task_id
            and rating.get("agent_id") == agent
            for rating in list_ratings(store, agent_id=agent)
        ):
            raise HTTPException(
                status_code=409,
                detail="this task result already has an LLM rating for the selected agent",
            )
        proposal = intelligence.propose_rating(
            provider, task=task["description"] or task["title"], result=result_text
        )
        outcome = record_rating(
            store,
            agent_id=agent,
            episode_id=task.get("episode_id") or f"task-{task_id}",
            task_id=task_id,  # M8: 真实来源锚点,悬挂占位引用可追溯
            dimensions=proposal["dimensions"],
            overall_preference=proposal["overall_preference"],
            would_use_again=proposal["would_use_again"],
            comment=proposal["comment"],
            source="llm",
            provenance=proposal["provenance"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"rating": outcome["rating"], "ledger_seq": outcome["ledger_seq"]}
