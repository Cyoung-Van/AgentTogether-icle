"""Request-scoped LLM metering without retaining prompts or responses."""

from __future__ import annotations

import time
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

from .intelligence import IntelligenceError

_COST_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("icle_cost_context", default={})


def set_cost_context(**metadata: Any) -> Token:
    clean = {
        key: value
        for key, value in metadata.items()
        if value is not None and isinstance(value, (str, int, float, bool))
    }
    return _COST_CONTEXT.set(clean)


def reset_cost_context(token: Token) -> None:
    _COST_CONTEXT.reset(token)


def current_cost_context() -> dict[str, Any]:
    return dict(_COST_CONTEXT.get())


def _task_metadata(store: Path, context: dict[str, Any]) -> dict[str, Any]:
    task_id = context.get("task_id")
    if not task_id:
        return {}
    try:
        from .task import show_task

        task = show_task(store, str(task_id))
    except Exception:  # Cost attribution must never break an LLM result.
        return {}
    profile = task.get("profile") or {}
    return {
        "project_id": task.get("project_id") or "default",
        "task_type": profile.get("primary_type") or "UNKNOWN",
        "task_subtype": profile.get("subtype"),
    }


def settle_intelligence_call(
    store: str | Path,
    *,
    provider: str,
    provider_id: str | None,
    model: str,
    usage_raw: dict[str, Any] | None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize provider usage, calculate tariff cost, and append one record."""
    from .cost import billing_mode, record_actual
    from .tariff import compute_actual_from_tariff
    from .usage import normalize_usage

    store = Path(store)
    context = current_cost_context()
    usage = normalize_usage(usage_raw or {}, source_hint="exact_provider" if usage_raw else "unknown")
    has_usage = any(
        int(usage.get(key, 0) or 0) > 0
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
    )
    if has_usage:
        actual = compute_actual_from_tariff(
            store,
            provider=provider,
            model_id=model,
            usage=usage,
            usage_source=str(usage.get("usage_source") or "exact_provider"),
        )
    else:
        from datetime import datetime, timezone

        actual = {
            "schema_version": "icle-cost-actual/v0.1",
            "provider": provider,
            "model": model,
            "usage": usage,
            "usage_source": str(usage.get("usage_source") or "unknown"),
            "pricing_snapshot": None,
            "cash_cost": None,
            "reason": "usage_unavailable",
            "currency": "USD",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    actual.update(
        {
            "scope": "intelligence",
            "operation": context.get("operation") or "intelligence",
            "provider_id": provider_id,
            "billing_mode": billing_mode(provider, model),
            "project_id": context.get("project_id") or "system",
            "task_id": context.get("task_id"),
        }
    )
    actual.update({key: value for key, value in _task_metadata(store, context).items() if value is not None})
    actual["duration_ms"] = int(duration_ms) if duration_ms is not None else None
    actual["duration_source"] = "measured" if duration_ms is not None else "unknown"
    record_actual(store, actual)
    return actual


class MeteredProvider:
    """Transparent provider wrapper that records usage after every completion."""

    def __init__(
        self,
        provider: Any,
        *,
        store: str | Path,
        billing_provider: str,
        provider_id: str | None = None,
    ) -> None:
        self._provider = provider
        self._store = Path(store)
        self._billing_provider = billing_provider
        self.provider_id = provider_id
        self.name = getattr(provider, "name", billing_provider)
        self.model = getattr(provider, "model", "")
        self.revision = getattr(provider, "revision", "")
        # Preserve the provider's non-secret diagnostics for existing callers.
        for attribute in ("base_url", "timeout"):
            if hasattr(provider, attribute):
                setattr(self, attribute, getattr(provider, attribute))

    def __getattr__(self, name: str) -> Any:
        # Internal compatibility for callers that inspect provider diagnostics;
        # the API never serializes this wrapper or its underlying secret.
        return getattr(self._provider, name)

    def _check_budget(self) -> None:
        from .cost import assert_cost_budget_available

        try:
            assert_cost_budget_available(self._store)
        except Exception as exc:
            raise IntelligenceError(str(exc)) from exc

    def complete(self, prompt: str) -> str:
        reply, _usage = self.complete_with_usage(prompt)
        return reply

    def complete_with_usage(self, prompt: str) -> tuple[str, dict[str, Any]]:
        self._check_budget()
        started = time.monotonic()
        complete_with_usage = getattr(self._provider, "complete_with_usage", None)
        if callable(complete_with_usage):
            reply, usage = complete_with_usage(prompt)
        else:
            reply = self._provider.complete(prompt)
            usage = {}
        duration_ms = round((time.monotonic() - started) * 1000)
        settle_intelligence_call(
            self._store,
            provider=self._billing_provider,
            provider_id=self.provider_id,
            model=self.model,
            usage_raw=usage,
            duration_ms=duration_ms,
        )
        return reply, usage
