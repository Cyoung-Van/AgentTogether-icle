from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..intelligence_status import IntelligenceBusyError
from ..settings import (
    SettingsError,
    list_intelligence_models,
    resolve_provider,
    select_intelligence_model,
)

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    store = request.app.state.store
    episodes_dir = store / "episodes"
    return {
        "status": "ok",
        "version": __version__,
        "episodes": len(list(episodes_dir.glob("ep-*.json"))) if episodes_dir.is_dir() else 0,
    }


class IntelligenceModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)


@router.get("/intelligence-status")
def intelligence_status(request: Request) -> dict:
    """Current process-local LLM activity; never includes prompts or secrets."""
    status = request.app.state.intelligence_status.snapshot()
    try:
        provider = resolve_provider(request.app.state.store)
        provider_name = str(getattr(provider, "name", "none") or "none")
        model = str(getattr(provider, "model", "") or "")
        configured = provider_name != "none"
        choices = list_intelligence_models(request.app.state.store)
        selected = next((item for item in choices["models"] if item["selected"]), None)
        display_name = selected["provider"] if selected else provider_name
        provider_id = selected["provider_id"] if selected else None
        error = None
    except Exception:
        provider_name = "unavailable"
        display_name = "unavailable"
        provider_id = None
        model = ""
        configured = False
        error = "provider unavailable"
    return {
        **status,
        "provider": {
            "configured": configured,
            "name": provider_name,
            "display_name": display_name,
            "provider_id": provider_id,
            "model": model,
            "error": error,
        },
    }


@router.get("/intelligence-models")
def intelligence_models(request: Request) -> dict:
    """Executable, secret-free Provider/Model choices for the global switcher."""
    return list_intelligence_models(request.app.state.store)


@router.post("/intelligence-model")
def update_intelligence_model(request: Request, body: IntelligenceModelSelection) -> dict:
    """Switch future LLM requests; active requests keep their original model."""
    try:
        return request.app.state.intelligence_status.run_when_idle(
            lambda: select_intelligence_model(
                request.app.state.store,
                provider_id=body.provider_id,
                model_id=body.model_id,
            )
        )
    except IntelligenceBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SettingsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
