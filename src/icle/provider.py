"""ProviderConfig (UI-16): LLM/API provider registry with secret masking.

Configs live in <store>/providers.json and contain NO secrets. Secrets live in
<store>/secrets.json (0600) or are referenced as env vars (`env:VAR`). The API
layer only ever sees `configured: bool` — the raw key never leaves this module.

Discipline (WebUI plan §35): the API does not own business rules; this core
module validates and persists providers, and the API calls it directly.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .http_transport import validate_endpoint, open_authenticated

PROVIDER_TYPES = ("openai-compatible", "anthropic-compatible", "local", "cli", "custom")
PROVIDER_ROLES = ("extraction", "judging", "planning", "routing", "general")
PROVIDER_STATUSES = ("connected", "unavailable", "misconfigured")
SCHEMA = "icle-provider-config/v0.1"

_TIMEOUT = 12.0


class ProviderError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str, *, chmod: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as handle:
        handle.write(content)
        temp = handle.name
    os.chmod(temp, chmod)
    os.replace(temp, path)
    os.chmod(path, chmod)


# ---------------------------------------------------------------- validation


def validate_provider_config(data: Any) -> dict:
    """Validate one provider config document (unknown enums rejected)."""
    if not isinstance(data, dict):
        raise ProviderError(f"{SCHEMA}: document must be an object")
    if data.get("schema_version") != SCHEMA:
        raise ProviderError(f"{SCHEMA}: schema_version must be {SCHEMA!r}")
    for field in ("provider_id", "display_name", "type"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ProviderError(f"{SCHEMA}: {field} must be a non-empty string")
    if data["type"] not in PROVIDER_TYPES:
        raise ProviderError(
            f"{SCHEMA}: type must be one of {', '.join(PROVIDER_TYPES)}"
        )
    base_url = data.get("base_url", "")
    if not isinstance(base_url, str):
        raise ProviderError(f"{SCHEMA}: base_url must be a string")
    if data["type"] in ("openai-compatible", "anthropic-compatible", "local") and not base_url.strip():
        raise ProviderError(f"{SCHEMA}: {data['type']} requires base_url")
    if data["type"] in ("openai-compatible", "anthropic-compatible", "local"):
        try:
            validate_endpoint(base_url)
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc
    roles = data.get("roles", [])
    if not isinstance(roles, list) or not all(r in PROVIDER_ROLES for r in roles):
        raise ProviderError(f"{SCHEMA}: roles must be known roles {', '.join(PROVIDER_ROLES)}")
    models = data.get("models", [])
    if not isinstance(models, list):
        raise ProviderError(f"{SCHEMA}: models must be an array")
    model_ids = []
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not model["id"]:
            raise ProviderError(f"{SCHEMA}: each model needs a non-empty id")
        model_ids.append(model["id"])
    # selected_model: the model currently chosen on the provider card (button
    # chips). It must reference a discovered/managed model when models exist;
    # when the model list is empty it must be empty too.
    selected_model = data.get("selected_model", "")
    if selected_model:
        if not isinstance(selected_model, str):
            raise ProviderError(f"{SCHEMA}: selected_model must be a string")
        if model_ids and selected_model not in model_ids:
            raise ProviderError(
                f"{SCHEMA}: selected_model {selected_model!r} is not in this provider's models"
            )
    status = data.get("status", "misconfigured")
    if status not in PROVIDER_STATUSES:
        raise ProviderError(f"{SCHEMA}: status must be one of {', '.join(PROVIDER_STATUSES)}")
    secret_ref = data.get("secret_ref", "")
    if secret_ref and not (secret_ref.startswith("env:") or secret_ref == "store"):
        raise ProviderError(f"{SCHEMA}: secret_ref must be 'store' or 'env:VAR'")
    last_checked_at = data.get("last_checked_at")
    if last_checked_at is not None and not isinstance(last_checked_at, str):
        raise ProviderError(f"{SCHEMA}: last_checked_at must be a string or null")
    return data


def _serialized(func):
    from functools import wraps
    from .storage import transaction

    @wraps(func)
    def wrapped(store, *args, **kwargs):
        with transaction(Path(store) / "providers.lock"):
            return func(store, *args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- persistence


def _config_path(store: str | Path) -> Path:
    return Path(store) / "providers.json"


def _secrets_path(store: str | Path) -> Path:
    return Path(store) / "secrets.json"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_configs(store: str | Path) -> dict[str, dict[str, Any]]:
    docs = _load_json(_config_path(store))
    if not isinstance(docs, dict):
        raise ProviderError("providers.json must be an object keyed by provider_id")
    return docs


def _save_configs(store: str | Path, configs: dict[str, dict[str, Any]]) -> None:
    _atomic_write(
        _config_path(store),
        json.dumps(configs, ensure_ascii=False, indent=2) + "\n",
        chmod=0o644,
    )


def _save_secrets(store: str | Path, secrets: dict[str, str]) -> None:
    _atomic_write(
        _secrets_path(store),
        json.dumps(secrets, ensure_ascii=False, indent=2) + "\n",
        chmod=0o600,
    )


def list_providers(store: str | Path) -> list[dict[str, Any]]:
    """API-safe provider list: secrets masked, never returned."""
    return [mask_provider(config) for config in _load_configs(store).values()]


def get_provider(store: str | Path, provider_id: str) -> dict[str, Any]:
    configs = _load_configs(store)
    if provider_id not in configs:
        raise ProviderError(f"provider not found: {provider_id}")
    return configs[provider_id]


@_serialized
def save_provider(
    store: str | Path,
    config: dict[str, Any],
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Create or update a provider. api_key=None keeps the existing secret."""
    validate_provider_config(config)
    store = Path(store)
    configs = _load_configs(store)
    provider_id = config["provider_id"]
    existing = configs.get(provider_id, {})
    merged = {**existing, **config}
    merged["provider_id"] = provider_id
    merged["schema_version"] = SCHEMA
    # status/last_checked_at are owned by test_provider, not by manual edits
    if "status" in config and config.get("status") != existing.get("status"):
        merged["status"] = config["status"]
    merged.setdefault("status", existing.get("status", "misconfigured"))
    merged["last_checked_at"] = existing.get("last_checked_at")
    validate_provider_config(merged)
    configs[provider_id] = merged
    _save_configs(store, configs)
    if api_key is not None:
        secrets = _load_json(_secrets_path(store))
        secrets[provider_id] = api_key
        merged["secret_ref"] = "store"
        _save_secrets(store, secrets)
        configs[provider_id] = merged
        _save_configs(store, configs)
    return mask_provider(merged)


@_serialized
def delete_provider(store: str | Path, provider_id: str) -> None:
    configs = _load_configs(store)
    if provider_id not in configs:
        raise ProviderError(f"provider not found: {provider_id}")
    del configs[provider_id]
    _save_configs(store, configs)
    secrets = _load_json(_secrets_path(store))
    if provider_id in secrets:
        del secrets[provider_id]
        _save_secrets(store, secrets)


@_serialized
def _update_provider_fields(store, provider_id, *, only_if_models_empty=False, **fields):
    configs = _load_configs(store)
    if provider_id not in configs:
        raise ProviderError(f"provider not found: {provider_id}")
    if only_if_models_empty and configs[provider_id].get("models"):
        return configs[provider_id]
    configs[provider_id] = {**configs[provider_id], **fields}
    _save_configs(store, configs)
    return configs[provider_id]


def _is_local_endpoint(config: dict[str, Any]) -> bool:
    return config.get("type") == "local"


def resolve_secret(store: str | Path, provider_id: str) -> str:
    """Resolve the raw secret for internal use only (never returned by the API)."""
    config = get_provider(store, provider_id)
    secret_ref = config.get("secret_ref", "")
    if secret_ref.startswith("env:"):
        value = os.environ.get(secret_ref[4:], "")
        if not value:
            raise ProviderError(f"secret env var {secret_ref!r} is not set")
        return value
    secrets = _load_json(_secrets_path(store))
    value = secrets.get(provider_id, "")
    if not value:
        raise ProviderError(f"provider {provider_id} has no configured secret")
    return value


def resolve_api_secret(store: str | Path, provider_id: str) -> str:
    """Resolve a secret, allowing only localhost-compatible endpoints to be keyless."""
    config = get_provider(store, provider_id)
    try:
        return resolve_secret(store, provider_id)
    except ProviderError:
        if _is_local_endpoint(config):
            return "local"
        raise


def mask_provider(config: dict[str, Any]) -> dict[str, Any]:
    """API-safe copy: the secret (store/env) is never returned; only `configured`."""
    masked = json.loads(json.dumps(config))
    secret_ref = masked.get("secret_ref", "")
    masked["configured"] = bool(secret_ref) or _is_local_endpoint(masked)
    if secret_ref.startswith("env:"):
        masked["secret_ref"] = secret_ref  # env name is not a secret
    elif secret_ref == "store":
        masked["secret_ref"] = "store"
    else:
        masked["secret_ref"] = ""
    return masked


# ---------------------------------------------------------------- connectivity


def _http_get(base_url: str, path: str, headers: dict[str, str], timeout: float) -> str:
    try:
        validate_endpoint(base_url)
    except ValueError as exc:
        raise ProviderError(str(exc)) from exc
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers=headers, method="GET")
    with open_authenticated(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def test_provider(
    store: str | Path,
    provider_id: str,
    *,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Live connectivity check; updates status/last_checked_at on success/failure."""
    store = Path(store)
    config = get_provider(store, provider_id)
    started = time.monotonic()
    provider_type = config["type"]
    try:
        if provider_type in ("openai-compatible", "local"):
            secret = resolve_api_secret(store, provider_id)
            _http_get(
                config["base_url"], "/models",
                {"Authorization": f"Bearer {secret}"}, timeout,
            )
        elif provider_type == "anthropic-compatible":
            secret = resolve_secret(store, provider_id)
            _http_get(
                config["base_url"], "/models",
                {"x-api-key": secret, "anthropic-version": "2023-06-01"}, timeout,
            )
        elif provider_type == "cli":
            command = (config.get("base_url") or "").strip().split(" ")[0]
            if command and shutil.which(command) is None:
                raise ProviderError(f"command not found on PATH: {command}")
        else:  # custom
            raise ProviderError("custom providers must be verified manually")
        status, error = "connected", None
    except (ProviderError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        status, error = "unavailable", str(exc)[:200]
    updated = _update_provider_fields(store, provider_id, status=status, last_checked_at=_now())
    return {
        "provider_id": provider_id,
        "ok": status == "connected",
        "status": status,
        "error": error,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "last_checked_at": updated["last_checked_at"],
    }


def discover_models(
    store: str | Path,
    provider_id: str,
    *,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Discover models via the provider protocol; updates status on failure.

    Returns API-safe {provider_id, ok, models: [{id, ...}], error}.
    """
    config = get_provider(store, provider_id)
    provider_type = config["type"]
    if provider_type in ("openai-compatible", "local"):
        path = "/models"
    elif provider_type == "anthropic-compatible":
        path = "/models"
    else:
        return {
            "provider_id": provider_id,
            "ok": False,
            "models": [],
            "error": f"model discovery not supported for type {provider_type!r}",
        }
    try:
        if provider_type in ("openai-compatible", "local"):
            secret = resolve_api_secret(store, provider_id)
            text = _http_get(config["base_url"], path, {"Authorization": f"Bearer {secret}"}, timeout)
        else:
            secret = resolve_secret(store, provider_id)
            text = _http_get(
                config["base_url"], path,
                {"x-api-key": secret, "anthropic-version": "2023-06-01"}, timeout,
            )
        payload = json.loads(text)
        data = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(data, list):
            raise ProviderError("unexpected model list payload")
        models = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id:
                entry = {"id": model_id}
                if isinstance(item.get("display_name"), str) and item["display_name"]:
                    entry["alias"] = item["display_name"]
                models.append(entry)
        _update_provider_fields(store, provider_id, status="connected", last_checked_at=_now())
        return {"provider_id": provider_id, "ok": True, "models": models, "error": None}
    except (ProviderError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        _update_provider_fields(store, provider_id, status="unavailable", last_checked_at=_now())
        return {
            "provider_id": provider_id,
            "ok": False,
            "models": [],
            "error": str(exc)[:200],
        }


def test_and_discover(
    store: str | Path,
    provider_id: str,
    *,
    timeout: float = _TIMEOUT,
) -> dict[str, Any]:
    """Test the connection; on success, auto-discover models into the store.

    'Connection → models in' flow (user-requested): when the provider has NO
    models yet, a successful test pulls them and persists them, so the model
    picker gets populated without a second manual step. A manual model list
    (whitelist) is NEVER overwritten — mirrors Open WebUI's 'leave empty to
    auto-discover' semantics. Returns the test result + models_synced flag.
    """
    store = Path(store)
    result = test_provider(store, provider_id, timeout=timeout)
    result["models_synced"] = False
    if result["ok"]:
        config = get_provider(store, provider_id)
        if not config.get("models"):
            found = discover_models(store, provider_id, timeout=timeout)
            if found["ok"] and found["models"]:
                _update_provider_fields(store, provider_id, only_if_models_empty=True,
                                        models=found["models"], status="connected", last_checked_at=_now())
                result["models_synced"] = True
    result["provider"] = mask_provider(get_provider(store, provider_id))
    return result


def default_provider_config(
    *,
    display_name: str,
    provider_type: str,
    base_url: str = "",
    roles: list[str] | None = None,
    models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a fresh (misconfigured) config document with a new provider_id."""
    return {
        "schema_version": SCHEMA,
        "provider_id": "p-" + uuid.uuid4().hex[:12],
        "display_name": display_name,
        "type": provider_type,
        "base_url": base_url,
        "secret_ref": "",
        "models": models or [],
        "roles": list(dict.fromkeys(roles or ["general"])),
        "status": "misconfigured",
        "last_checked_at": None,
    }


# ---------------------------------------------------------------- key detection

# Provider presets (LobeChat ModelProviderCard-style): the mainstream UX is a
# provider picker that auto-fills base_url/type/name, THEN the user pastes the
# key. Key-prefix detection below is a bonus on top. `requires_key=False` for
# local backends (Ollama/LM Studio), matching LobeChat's showApiKey: false.
PROVIDER_PRESETS: list[dict[str, Any]] = [
    # ---- international cloud
    {"id": "openai", "display_name": "OpenAI", "type": "openai-compatible", "base_url": "https://api.openai.com/v1", "key_prefix": "sk-"},
    {"id": "anthropic", "display_name": "Anthropic", "type": "anthropic-compatible", "base_url": "https://api.anthropic.com/v1", "key_prefix": "sk-ant-"},
    {"id": "google", "display_name": "Google Gemini", "type": "openai-compatible", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/", "key_prefix": "AIza"},
    {"id": "mistral", "display_name": "Mistral", "type": "openai-compatible", "base_url": "https://api.mistral.ai/v1", "key_prefix": ""},
    {"id": "xai", "display_name": "xAI (Grok)", "type": "openai-compatible", "base_url": "https://api.x.ai/v1", "key_prefix": "xai-"},
    {"id": "cohere", "display_name": "Cohere", "type": "openai-compatible", "base_url": "https://api.cohere.com/v1", "key_prefix": ""},
    {"id": "perplexity", "display_name": "Perplexity", "type": "openai-compatible", "base_url": "https://api.perplexity.ai/v1", "key_prefix": "pplx-"},
    {"id": "openrouter", "display_name": "OpenRouter", "type": "openai-compatible", "base_url": "https://openrouter.ai/api/v1", "key_prefix": "sk-or-"},
    {"id": "groq", "display_name": "Groq", "type": "openai-compatible", "base_url": "https://api.groq.com/openai/v1", "key_prefix": "gsk_"},
    {"id": "together", "display_name": "Together AI", "type": "openai-compatible", "base_url": "https://api.together.xyz/v1", "key_prefix": ""},
    {"id": "fireworks", "display_name": "Fireworks", "type": "openai-compatible", "base_url": "https://api.fireworks.ai/inference/v1", "key_prefix": ""},
    # ---- Chinese cloud (split platforms stay as separate rows)
    {"id": "kimi-code", "display_name": "Kimi Code", "type": "openai-compatible", "base_url": "https://api.kimi.com/coding/v1", "key_prefix": "sk-kimi-", "family": "kimi"},
    {"id": "kimi", "display_name": "Kimi Open Platform (CN)", "type": "openai-compatible", "base_url": "https://api.moonshot.cn/v1", "key_prefix": "sk-", "family": "kimi"},
    {"id": "kimi-intl", "display_name": "Kimi Open Platform (intl)", "type": "openai-compatible", "base_url": "https://api.moonshot.ai/v1", "key_prefix": "sk-", "family": "kimi"},
    {"id": "deepseek", "display_name": "DeepSeek", "type": "openai-compatible", "base_url": "https://api.deepseek.com/v1", "key_prefix": "sk-"},
    {"id": "qwen", "display_name": "Qwen DashScope (CN)", "type": "openai-compatible", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "key_prefix": "sk-", "family": "qwen"},
    {"id": "qwen-intl", "display_name": "Qwen DashScope (intl)", "type": "openai-compatible", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "key_prefix": "sk-", "family": "qwen"},
    {"id": "qwen-coding", "display_name": "Qwen Coding Plan (intl)", "type": "openai-compatible", "base_url": "https://coding-intl.dashscope.aliyuncs.com/v1", "key_prefix": "sk-", "family": "qwen"},
    {"id": "zhipu", "display_name": "智谱 GLM", "type": "openai-compatible", "base_url": "https://open.bigmodel.cn/api/paas/v4", "key_prefix": ""},
    {"id": "baichuan", "display_name": "百川 (Baichuan)", "type": "openai-compatible", "base_url": "https://api.baichuan-ai.com/v1", "key_prefix": ""},
    {"id": "hunyuan", "display_name": "腾讯混元 (Hunyuan)", "type": "openai-compatible", "base_url": "https://api.hunyuan.cloud.tencent.com/v1", "key_prefix": ""},
    {"id": "minimax", "display_name": "MiniMax (CN)", "type": "openai-compatible", "base_url": "https://api.minimaxi.com/v1", "key_prefix": "", "family": "minimax"},
    {"id": "minimax-intl", "display_name": "MiniMax (intl)", "type": "openai-compatible", "base_url": "https://api.minimax.io/v1", "key_prefix": "", "family": "minimax"},
    {"id": "spark", "display_name": "讯飞星火 (Spark)", "type": "openai-compatible", "base_url": "https://spark-api-open.xf-yun.com/v1", "key_prefix": ""},
    {"id": "baidu", "display_name": "百度文心千帆 (ERNIE)", "type": "openai-compatible", "base_url": "https://qianfan.baidubce.com/v2", "key_prefix": ""},
    {"id": "ai360", "display_name": "360 智脑", "type": "openai-compatible", "base_url": "https://ai.360.com/v1", "key_prefix": ""},
    {"id": "lingyiwanwu", "display_name": "零一万物 (LingYiWanWu)", "type": "openai-compatible", "base_url": "https://api.lingyiwanwu.com/v1", "key_prefix": "sk-"},
    {"id": "stepfun", "display_name": "阶跃星辰 (StepFun)", "type": "openai-compatible", "base_url": "https://api.stepfun.com/v1", "key_prefix": "sk-"},
    {"id": "siliconflow", "display_name": "硅基流动 SiliconFlow", "type": "openai-compatible", "base_url": "https://api.siliconflow.cn/v1", "key_prefix": "sk-"},
    {"id": "volcengine", "display_name": "火山方舟 (Volcengine)", "type": "openai-compatible", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "key_prefix": ""},
    # ---- local / self-hosted / gateway
    {"id": "ollama", "display_name": "Ollama (local)", "type": "local", "base_url": "http://localhost:11434/v1", "key_prefix": "", "requires_key": False},
    {"id": "lmstudio", "display_name": "LM Studio (local)", "type": "local", "base_url": "http://localhost:1234/v1", "key_prefix": "", "requires_key": False},
    {"id": "vllm", "display_name": "vLLM (local)", "type": "local", "base_url": "http://localhost:8000/v1", "key_prefix": "", "requires_key": False},
    {"id": "localai", "display_name": "LocalAI (local)", "type": "local", "base_url": "http://localhost:8080/v1", "key_prefix": "", "requires_key": False},
    {"id": "litellm", "display_name": "LiteLLM Proxy (gateway)", "type": "local", "base_url": "http://localhost:4000/v1", "key_prefix": "", "requires_key": False},
]


def list_presets() -> list[dict[str, Any]]:
    """API-safe presets for protocols ICLE can execute end to end."""
    return [
        dict(item) for item in PROVIDER_PRESETS
        if item.get("type") in {"openai-compatible", "local"}
    ]


# Known provider profiles keyed by API-key prefix (plan §29 spirit: ONE provider
# protocol + a thin detection layer — no per-vendor implementations). The raw
# key is only used to match the prefix; it is never returned or persisted here.
# `sk-` is ambiguous (OpenAI / Kimi / Moonshot / DeepSeek…), so it maps to
# OpenAI by default and exposes the common alternatives as candidates.
KNOWN_PROVIDERS: list[dict[str, Any]] = [
    {
        "prefix": "sk-kimi-",
        "display_name": "Kimi Code",
        "type": "openai-compatible",
        "base_url": "https://api.kimi.com/coding/v1",
        "warning": "Kimi Code and Kimi Open Platform keys are not interchangeable",
    },
    {
        "prefix": "sk-ant-",
        "display_name": "Anthropic",
        "type": "anthropic-compatible",
        "base_url": "https://api.anthropic.com/v1",
    },
    {
        "prefix": "sk-or-",
        "display_name": "OpenRouter",
        "type": "openai-compatible",
        "base_url": "https://openrouter.ai/api/v1",
    },
    {
        "prefix": "gsk_",
        "display_name": "Groq",
        "type": "openai-compatible",
        "base_url": "https://api.groq.com/openai/v1",
    },
    {
        "prefix": "AIza",
        "display_name": "Google Gemini",
        "type": "openai-compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    {
        "prefix": "xai-",
        "display_name": "xAI (Grok)",
        "type": "openai-compatible",
        "base_url": "https://api.x.ai/v1",
    },
    {
        "prefix": "pplx-",
        "display_name": "Perplexity",
        "type": "openai-compatible",
        "base_url": "https://api.perplexity.ai/v1",
    },
    {
        "prefix": "sk-",
        "display_name": "OpenAI",
        "type": "openai-compatible",
        "base_url": "https://api.openai.com/v1",
        "candidates": [
            {"display_name": "Kimi Open Platform (CN)", "type": "openai-compatible", "base_url": "https://api.moonshot.cn/v1"},
            {"display_name": "Kimi Open Platform (intl)", "type": "openai-compatible", "base_url": "https://api.moonshot.ai/v1"},
            {"display_name": "DeepSeek", "type": "openai-compatible", "base_url": "https://api.deepseek.com/v1"},
            {"display_name": "Qwen DashScope (CN)", "type": "openai-compatible", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
            {"display_name": "Qwen DashScope (intl)", "type": "openai-compatible", "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"},
            {"display_name": "MiniMax (CN)", "type": "openai-compatible", "base_url": "https://api.minimaxi.com/v1"},
            {"display_name": "MiniMax (intl)", "type": "openai-compatible", "base_url": "https://api.minimax.io/v1"},
            {"display_name": "硅基流动 SiliconFlow", "type": "openai-compatible", "base_url": "https://api.siliconflow.cn/v1"},
        ],
    },
]


def detect_provider(api_key: str) -> dict[str, Any]:
    """Best-effort provider auto-detection from the API key format.

    Returns {detected, suggested, candidates, hint}. The key is never echoed
    back; only the matched prefix is used. Unknown keys return detected=False
    with empty suggestions so the UI falls back to manual entry.
    """
    if not api_key or not isinstance(api_key, str) or len(api_key) < 8:
        return {
            "detected": False,
            "suggested": None,
            "candidates": [],
            "hint": "key too short to identify",
        }
    ranked = sorted(KNOWN_PROVIDERS, key=lambda item: len(item["prefix"]), reverse=True)
    for profile in ranked:
        if api_key.startswith(profile["prefix"]):
            if profile["type"] not in {"openai-compatible", "local"}:
                return {
                    "detected": False,
                    "suggested": None,
                    "candidates": [],
                    "hint": f"{profile['display_name']} is not supported by the current execution adapter",
                }
            suggested = {
                "display_name": profile["display_name"],
                "type": profile["type"],
                "base_url": profile["base_url"],
            }
            candidates = [suggested] + profile.get("candidates", [])
            return {
                "detected": True,
                "suggested": suggested,
                "candidates": candidates,
                "hint": f"key prefix {profile['prefix']!r} matches {profile['display_name']}",
                "warning": profile.get("warning"),
            }
    return {
        "detected": False,
        "suggested": None,
        "candidates": [],
        "hint": "unknown key format; configure manually",
    }
