"""ProviderConfig API (UI-16): thin HTTP layer over the provider core.

Secrets never cross this boundary: create/update accept an api_key, the
response only reports `configured: bool` (core masks before returning).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import provider as provider_core
from ..intelligence_status import IntelligenceBusyError

router = APIRouter()
SUPPORTED_PROVIDER_TYPES = {"openai-compatible", "local", "custom"}


class ProviderCreate(BaseModel):
    display_name: str
    type: str
    base_url: str = ""
    api_key: str | None = None
    roles: list[str] = []
    models: list[dict] = []


class ProviderPatch(BaseModel):
    display_name: str | None = None
    type: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    roles: list[str] | None = None
    models: list[dict] | None = None
    selected_model: str | None = None


@router.get("/providers")
def providers(request: Request) -> dict:
    return {"providers": provider_core.list_providers(request.app.state.store)}


@router.get("/providers/presets")
def provider_presets() -> dict:
    """Provider presets (LobeChat-style picker): metadata only, never secrets."""
    return {"presets": provider_core.list_presets()}


class DetectRequest(BaseModel):
    api_key: str


@router.post("/providers/detect")
def detect_provider(request: Request, body: DetectRequest) -> dict:
    """Auto-detect provider type/base_url/name from an API key (never stored).

    The key is read only to match its prefix; it is not persisted and never
    appears in the response. Frontend uses the suggestion to prefill the form.
    """
    return provider_core.detect_provider(body.api_key)


@router.post("/providers")
def create_provider(request: Request, body: ProviderCreate) -> dict:
    if body.type not in SUPPORTED_PROVIDER_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"provider type {body.type!r} has no end-to-end execution adapter",
        )
    config = provider_core.default_provider_config(
        display_name=body.display_name,
        provider_type=body.type,
        base_url=body.base_url,
        roles=body.roles,
        models=body.models,
    )
    try:
        masked = provider_core.save_provider(
            request.app.state.store, config, api_key=body.api_key
        )
    except provider_core.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"provider": masked}


@router.patch("/providers/{provider_id}")
def update_provider(request: Request, provider_id: str, body: ProviderPatch) -> dict:
    try:
        config = provider_core.get_provider(request.app.state.store, provider_id)
    except provider_core.ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    patch = body.model_dump(exclude_unset=True)
    if patch.get("type") not in (None, *SUPPORTED_PROVIDER_TYPES):
        raise HTTPException(
            status_code=400,
            detail=f"provider type {patch['type']!r} has no end-to-end execution adapter",
        )
    api_key = patch.pop("api_key", None)
    for key, value in patch.items():
        if value is not None:
            config[key] = value
    from ..settings import list_intelligence_models
    current = list_intelligence_models(request.app.state.store).get("current")
    active_selection = isinstance(current, dict) and current.get("provider_id") == provider_id
    if active_selection:
        selected_model = config.get("selected_model") or current.get("model_id")
        model_ids = {item.get("id") for item in config.get("models", [])}
        roles = config.get("roles") or []
        if (
            config.get("type") not in {"openai-compatible", "local"}
            or "general" not in roles
            or selected_model not in model_ids
        ):
            raise HTTPException(
                status_code=409,
                detail="active intelligence provider must keep its general role and selected model",
            )
    def persist() -> dict:
        masked_provider = provider_core.save_provider(
            request.app.state.store, config, api_key=api_key
        )
        if "selected_model" in patch and active_selection:
            from ..settings import select_intelligence_model
            select_intelligence_model(
                request.app.state.store,
                provider_id=provider_id,
                model_id=config["selected_model"],
            )
        return masked_provider

    try:
        masked = (
            request.app.state.intelligence_status.run_when_idle(persist)
            if active_selection else persist()
        )
    except IntelligenceBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (provider_core.ProviderError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"provider": masked}


@router.delete("/providers/{provider_id}")
def delete_provider(request: Request, provider_id: str) -> dict:
    from ..settings import clear_intelligence_selection, list_intelligence_models
    from ..task import list_tasks, show_task

    selection = list_intelligence_models(request.app.state.store).get("current")
    active_selection = isinstance(selection, dict) and selection.get("provider_id") == provider_id
    references = []
    for summary in list_tasks(request.app.state.store):
        task = show_task(request.app.state.store, summary["task_id"])
        for step in (task.get("plan") or {}).get("steps", []):
            if str(step.get("recommended_agent") or "").startswith(f"{provider_id}/"):
                references.append(task["task_id"])
                break
    if references:
        raise HTTPException(
            status_code=409,
            detail=f"provider is referenced by task plans: {', '.join(references[:5])}",
        )
    def remove() -> None:
        provider_core.delete_provider(request.app.state.store, provider_id)
        clear_intelligence_selection(request.app.state.store, provider_id=provider_id)

    try:
        if active_selection:
            request.app.state.intelligence_status.run_when_idle(remove)
        else:
            remove()
    except IntelligenceBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except provider_core.ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": provider_id}


@router.post("/providers/{provider_id}/test")
def test_provider(request: Request, provider_id: str) -> dict:
    """Test the connection; on success auto-discover models into the store
    (only when the provider has none — a manual whitelist is never replaced)."""
    try:
        return provider_core.test_and_discover(request.app.state.store, provider_id)
    except provider_core.ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/providers/{provider_id}/models")
def provider_models(request: Request, provider_id: str) -> dict:
    try:
        return provider_core.discover_models(request.app.state.store, provider_id)
    except provider_core.ProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
