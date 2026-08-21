"""LLM provider settings (store/settings.json) — local-only, key masked.

Security rules: the key file is 0600; the API never returns the full key;
errors are sanitized before reaching any client.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .intelligence import KimiProvider, NoneProvider, OpenAICompatibleProvider, IntelligenceError

LLM_PROVIDERS = ("none", "kimi", "openai")
VALUE_POLICIES = ("quality_first", "balanced", "cost_first", "speed_first")
DEFAULT_SYSTEM_SETTINGS = {
    "default_language": "zh",
    "value_policy": "balanced",
    "monthly_cost_budget_usd": None,
    "cost_warning_percent": 80,
}


class SettingsError(ValueError):
    pass


def _path(store: str | Path) -> Path:
    return Path(store) / "settings.json"


def load_settings(store: str | Path) -> dict[str, Any]:
    path = _path(store)
    if not path.is_file():
        return {"llm": {"provider": "none", "base_url": "", "model": "", "api_key": ""}}
    return json.loads(path.read_text(encoding="utf-8"))


def get_system_settings(store: str | Path) -> dict[str, Any]:
    """Validated, secret-free runtime preferences with stable defaults."""
    raw = load_settings(store).get("system") or {}
    result = {**DEFAULT_SYSTEM_SETTINGS, **raw}
    if result["default_language"] not in ("zh", "en"):
        result["default_language"] = DEFAULT_SYSTEM_SETTINGS["default_language"]
    if result["value_policy"] not in VALUE_POLICIES:
        result["value_policy"] = DEFAULT_SYSTEM_SETTINGS["value_policy"]
    budget = result.get("monthly_cost_budget_usd")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0:
        result["monthly_cost_budget_usd"] = None
    warning = result.get("cost_warning_percent")
    if isinstance(warning, bool) or not isinstance(warning, int) or not 1 <= warning <= 100:
        result["cost_warning_percent"] = DEFAULT_SYSTEM_SETTINGS["cost_warning_percent"]
    return result


def save_system_settings(
    store: str | Path,
    *,
    default_language: str,
    value_policy: str,
    monthly_cost_budget_usd: float | None,
    cost_warning_percent: int,
) -> dict[str, Any]:
    if default_language not in ("zh", "en"):
        raise SettingsError("default_language must be zh or en")
    if value_policy not in VALUE_POLICIES:
        raise SettingsError(f"value_policy must be one of {', '.join(VALUE_POLICIES)}")
    if monthly_cost_budget_usd is not None:
        if isinstance(monthly_cost_budget_usd, bool) or monthly_cost_budget_usd <= 0:
            raise SettingsError("monthly_cost_budget_usd must be greater than 0 or null")
        monthly_cost_budget_usd = round(float(monthly_cost_budget_usd), 2)
    if isinstance(cost_warning_percent, bool) or not 1 <= cost_warning_percent <= 100:
        raise SettingsError("cost_warning_percent must be between 1 and 100")
    settings = load_settings(store)
    settings["system"] = {
        "default_language": default_language,
        "value_policy": value_policy,
        "monthly_cost_budget_usd": monthly_cost_budget_usd,
        "cost_warning_percent": int(cost_warning_percent),
    }
    _write_settings(store, settings)
    return get_system_settings(store)


def save_llm_settings(
    store: str | Path,
    *,
    provider: str,
    base_url: str = "",
    model: str = "",
    api_key: str | None = None,
) -> dict[str, Any]:
    if provider not in LLM_PROVIDERS:
        raise SettingsError(f"provider must be one of {', '.join(LLM_PROVIDERS)}")
    settings = load_settings(store)
    existing_key = settings.get("llm", {}).get("api_key", "")
    key = existing_key if api_key is None else api_key  # None = keep existing
    if provider == "openai":
        if not base_url.strip() or not model.strip():
            raise SettingsError("openai provider requires base_url and model")
        if not key.strip():
            raise SettingsError("openai provider requires an api_key")
    settings["llm"] = {
        "provider": provider,
        "base_url": base_url.strip(),
        "model": model.strip(),
        "api_key": key,
    }
    path = _path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        temp = handle.name
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    return mask(settings)


def mask(settings: dict[str, Any]) -> dict[str, Any]:
    """API-safe copy: the key is never returned in full."""
    masked = json.loads(json.dumps(settings))
    key = masked.get("llm", {}).get("api_key", "")
    if key:
        masked["llm"]["api_key"] = key[:6] + "…" + key[-4:] if len(key) > 12 else "***"
        masked["llm"]["has_key"] = True
    else:
        masked["llm"]["has_key"] = False
    return masked


def _write_settings(store: str | Path, settings: dict[str, Any]) -> dict[str, Any]:
    path = _path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        temp = handle.name
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    os.chmod(path, 0o600)
    return mask(settings)


def _provider_pool(store: str | Path, *, role: str = "general") -> list[dict[str, Any]]:
    from .provider import ProviderError, list_providers, resolve_api_secret

    try:
        providers = list_providers(store)
    except Exception:
        providers = []
    pool = []
    for provider in providers:
        if (
            provider.get("status") != "connected"
            or provider.get("type") not in {"openai-compatible", "local"}
            or not provider.get("models")
            or not provider.get("configured")
            or (role not in (provider.get("roles") or []) and "general" not in (provider.get("roles") or []))
        ):
            continue
        try:
            resolve_api_secret(store, provider["provider_id"])
        except (ProviderError, KeyError):
            continue
        pool.append(provider)
    return pool


def _selected_provider(store: str | Path, selection: dict[str, Any], *, role: str) -> Any:
    from .provider import ProviderError, get_provider, resolve_api_secret

    provider_id = selection.get("provider_id")
    model_id = selection.get("model_id")
    if not isinstance(provider_id, str) or not isinstance(model_id, str):
        raise SettingsError("intelligence model selection is invalid")
    try:
        config = get_provider(store, provider_id)
    except ProviderError as exc:
        raise SettingsError("selected intelligence provider was not found") from exc
    if config.get("status") != "connected" or config.get("type") not in {"openai-compatible", "local"}:
        raise SettingsError("selected intelligence provider is unavailable")
    roles = config.get("roles") or []
    if role not in roles and "general" not in roles:
        raise SettingsError("selected intelligence provider does not support this operation")
    if model_id not in {item.get("id") for item in config.get("models", [])}:
        raise SettingsError("selected intelligence model is no longer available")
    try:
        key = resolve_api_secret(store, provider_id)
    except (ProviderError, KeyError) as exc:
        raise SettingsError("selected intelligence provider has no configured key") from exc
    provider = OpenAICompatibleProvider(base_url=config["base_url"], api_key=key, model=model_id)
    from .metering import MeteredProvider

    return MeteredProvider(
        provider,
        store=store,
        billing_provider=str(config.get("display_name") or provider_id),
        provider_id=provider_id,
    )


def list_intelligence_models(store: str | Path) -> dict[str, Any]:
    """Return safe, executable model choices for the global intelligence layer."""
    settings = load_settings(store)
    selection = settings.get("intelligence_selection")
    pool = _provider_pool(store, role="general")
    current_provider = selection.get("provider_id") if isinstance(selection, dict) else None
    current_model = selection.get("model_id") if isinstance(selection, dict) else None
    legacy_active = settings.get("llm", {}).get("provider", "none") != "none"
    if not selection and legacy_active:
        current_provider = None
        current_model = None
    elif not any(
        p.get("provider_id") == current_provider
        and current_model in {item.get("id") for item in p.get("models", [])}
        for p in pool
    ):
        chosen = next((p for p in pool if p.get("selected_model")), pool[0] if pool else None)
        current_provider = chosen.get("provider_id") if chosen else None
        current_model = (chosen.get("selected_model") or chosen["models"][0]["id"]) if chosen else None
    models = []
    for provider in pool:
        for model in provider.get("models", []):
            model_id = model.get("id")
            if not model_id:
                continue
            models.append({
                "provider_id": provider["provider_id"],
                "provider": provider.get("display_name") or provider["provider_id"],
                "model_id": model_id,
                "model": model.get("alias") or model_id,
                "status": provider.get("status"),
                "selected": provider["provider_id"] == current_provider and model_id == current_model,
            })
    return {
        "models": models,
        "current": {"provider_id": current_provider, "model_id": current_model} if current_provider and current_model else None,
    }


def clear_intelligence_selection(store: str | Path, *, provider_id: str) -> None:
    """Remove a global selection when its Provider is being deleted."""
    settings = load_settings(store)
    selection = settings.get("intelligence_selection")
    if isinstance(selection, dict) and selection.get("provider_id") == provider_id:
        settings.pop("intelligence_selection", None)
        _write_settings(store, settings)


def select_intelligence_model(store: str | Path, *, provider_id: str, model_id: str) -> dict[str, Any]:
    """Persist the global model choice after validating it is executable."""
    candidates = list_intelligence_models(store)["models"]
    if not any(item["provider_id"] == provider_id and item["model_id"] == model_id for item in candidates):
        raise SettingsError("model is not an available connected intelligence target")
    settings = load_settings(store)
    settings["intelligence_selection"] = {"provider_id": provider_id, "model_id": model_id}
    from .provider import get_provider, save_provider
    config = get_provider(store, provider_id)
    previous_model = config.get("selected_model", "")
    save_provider(store, {**config, "selected_model": model_id})
    try:
        _write_settings(store, settings)
    except Exception:
        save_provider(store, {**config, "selected_model": previous_model})
        raise
    return list_intelligence_models(store)


def resolve_provider(store: str | Path, *, role: str = "general"):
    """Build the configured intelligence provider for a requested role.

    An explicit global model choice is authoritative. Without one, preserve the
    legacy settings.json path and then use the Providers registry fallback.
    """
    settings = load_settings(store)
    selection = settings.get("intelligence_selection")
    if isinstance(selection, dict) and selection.get("provider_id"):
        return _selected_provider(store, selection, role=role)

    llm = settings.get("llm", {})
    name = llm.get("provider", "none")
    if name == "kimi":
        from .metering import MeteredProvider

        return MeteredProvider(KimiProvider(), store=store, billing_provider="kimi")
    if name == "openai":
        from .metering import MeteredProvider

        provider = OpenAICompatibleProvider(
            base_url=llm.get("base_url", ""),
            api_key=llm.get("api_key", ""),
            model=llm.get("model", ""),
        )
        return MeteredProvider(provider, store=store, billing_provider="openai")
    if name != "none":
        raise SettingsError(f"unknown llm provider in settings: {name}")

    from .provider import ProviderError, resolve_api_secret
    pool = _provider_pool(store, role=role)
    if pool:
        chosen = next((p for p in pool if p.get("selected_model")), pool[0])
        model = chosen.get("selected_model") or chosen["models"][0]["id"]
        try:
            key = resolve_api_secret(store, chosen["provider_id"])
        except (ProviderError, KeyError) as exc:
            raise SettingsError(
                f"provider {chosen['provider_id']} has no stored key"
            ) from exc
        provider = OpenAICompatibleProvider(
            base_url=chosen["base_url"], api_key=key, model=model
        )
        from .metering import MeteredProvider

        return MeteredProvider(
            provider,
            store=store,
            billing_provider=str(chosen.get("display_name") or chosen["provider_id"]),
            provider_id=str(chosen["provider_id"]),
        )
    return NoneProvider()


def verify_provider(store: str | Path, timeout: float = 45) -> dict[str, Any]:
    """Live-check the configured provider with a minimal prompt."""
    try:
        provider = resolve_provider(store)
    except (SettingsError, IntelligenceError) as exc:
        return {"ok": False, "error": str(exc)}
    if provider.name == "none":
        return {"ok": False, "error": "provider is 'none' — nothing to verify"}
    try:
        reply = provider.complete("Reply with exactly: ok")
    except IntelligenceError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    return {
        "ok": True,
        "provider": provider.name,
        "model": provider.model,
        "reply_preview": reply[:40],
    }
