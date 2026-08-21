"""BaseTariffModel — official pricing layer (COST-01/02, plan v0.1 §3-§5).

Official pricing determines what resources cost. This module owns:

    PricingSnapshot    provider/model/effective_at/currency/pricing/source/status
    TariffComponent    generic priced line: {kind, category, unit, price} or
                       {kind: multiplier, condition, factor}
    PricingSource      4-level provenance: official_api / official_doc /
                       community_catalog / user_override
    TariffEngine       usage × components (+ multiplier rules) → CostActual

Design notes (from the plan):
- A price table is NOT three fixed fields (input/output). Providers bill
  differently: DeepSeek separates cache hit/miss, Anthropic has cache
  creation/read, Google separates cached/candidate/tool/thinking tokens.
- "Unknown price" must stay unknown (cost=None + reason), never $0.
- The plan's §3 example (deepseek-v4-pro 0.435/0.003625/0.87) is illustrative;
  the built-in snapshots below keep the project's existing catalog numbers and
  add official-style cache/reasoning components (cache_read ≈ 0.1× input,
  cache_write ≈ 1.25× input, reasoning ≈ output price). models.dev sync and
  user overrides can supersede them — provenance is always preserved.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

SCHEMA_SNAPSHOT = "icle-pricing-snapshot/v0.1"
SCHEMA_ACTUAL = "icle-cost-actual/v0.1"

SOURCE_KINDS = ("official_api", "official_doc", "community_catalog", "user_override")
SNAPSHOT_STATUSES = ("verified", "suspected", "stale", "unknown")

COMPONENT_KINDS = ("token", "tool_call", "multiplier")
# token categories: input_uncached (regular input), input_cached / cache_read
# (discounted reads), cache_write (premium writes), output, reasoning.
TOKEN_CATEGORIES = ("input_uncached", "input_cached", "cache_read", "cache_write", "output", "reasoning")
TOOL_CATEGORIES = ("web_search", "file_edit", "code_execution", "mcp", "other")

# Multiplier conditions we understand (extensible, kept explicit).
_MULTIPLIER_FIELDS = ("input_tokens", "output_tokens", "total_tokens")

MODELS_DEV_API = "https://models.dev/api.json"
MODELS_DEV_REPOSITORY = "https://github.com/anomalyco/models.dev"
MODELS_DEV_LICENSE = "MIT"
MODELS_DEV_LICENSE_URL = "https://github.com/anomalyco/models.dev/blob/dev/LICENSE"
CONFLICT_WARNING_PCT = 0.20  # >20% divergence between builtin and models.dev

# Curated from LiteLLM's versioned model price catalog.  The revision and
# content hash make the cold-start cost prior reproducible; this is a catalog
# prior, not a claim that every relay or subscription bills at API rates.
LITELLM_CATALOG_URL = (
    "https://github.com/BerriAI/litellm/blob/"
    "973329e9864154d429a7d739fb6a552fe03a9b3e/"
    "model_prices_and_context_window.json"
)
LITELLM_CATALOG_REVISION = "973329e9864154d429a7d739fb6a552fe03a9b3e"
LITELLM_CATALOG_CONTENT_HASH = (
    "sha256:b5595d7e8e039a131d6bb160e90c26678c05a0b437f72a4fe57f18a77c5f52ed"
)
LITELLM_CATALOG_RETRIEVED_AT = "2026-08-16T00:59:12Z"
LITELLM_API = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Product / CLI ids that never appear in catalogs as-is.
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "kimi": ("moonshotai", "moonshot", "kimi-code", "kimi"),
    "kimi-code": ("moonshotai", "moonshot", "kimi", "kimi-code"),
    "kimicode": ("moonshotai", "moonshot", "kimi"),
    "moonshot": ("moonshotai", "moonshot", "kimi"),
    "moonshotai": ("moonshotai", "moonshot", "kimi"),
    "deepseek": ("deepseek",),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "google": ("google", "gemini"),
    "gemini": ("google", "gemini"),
    "xai": ("xai",),
    "zhipu": ("zhipuai", "zai", "zhipu"),
    "zhipuai": ("zhipuai", "zai", "zhipu"),
    "zai": ("zhipuai", "zai", "zhipu"),
}
MODEL_ALIASES: dict[str, tuple[str, ...]] = {
    "kimi-for-coding": ("kimi-k2.7-code", "kimi-k2-7-code", "k2.7-code"),
    "kimi-for-coding-highspeed": ("kimi-k2.7-code-highspeed", "kimi-k2-7-code-highspeed"),
    "k3": ("kimi-k3", "k3"),
    "k3-256k": ("kimi-k3",),
    "kimi-k3": ("kimi-k3", "k3"),
    "deepseek-chat": ("deepseek-v4-flash", "deepseek-chat"),
    "deepseek-reasoner": ("deepseek-v4-pro", "deepseek-reasoner"),
}


def _norm_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def catalog_lookup_keys(provider: str, model_id: str) -> list[tuple[str, str]]:
    """All (provider, model) pairs to try against a community catalog."""
    providers = [_norm_id(provider)]
    for alias in PROVIDER_ALIASES.get(_norm_id(provider), ()):
        if alias not in providers:
            providers.append(alias)
    models = [_norm_id(model_id), str(model_id or "").strip()]
    for alias in MODEL_ALIASES.get(_norm_id(model_id), ()):
        models.append(alias)
        models.append(_norm_id(alias))
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for item_provider in providers:
        for item_model in models:
            if not item_provider or not item_model:
                continue
            key = (item_provider, item_model)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
    return pairs


class TariffError(ValueError):
    pass


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- built-in official snapshots
#
# Four providers per COST-01. Numbers keep the project's existing catalog
# (cost.py BUILTIN_CATALOG) and add official-style cache/reasoning components.
# These are best-effort official_doc snapshots, superseded by models.dev sync
# or user overrides — never treated as ground truth without provenance.

def _token(price: float, category: str, unit: str = "1m_tokens") -> dict[str, Any]:
    return {"kind": "token", "category": category, "unit": unit, "price": price}


def _builtin_snapshot(
    provider: str, model: str, *,
    input_p: float, output_p: float,
    cache_read_ratio: float = 0.1, cache_write_ratio: float = 1.25,
    reasoning_same_as_output: bool = True, effective_at: str = "2026-08-16",
    source_kind: str = "official_doc",
    source_url: str | None = None,
    source_revision: str | None = None,
    source_content_hash: str | None = None,
    source_retrieved_at: str | None = None,
) -> dict[str, Any]:
    components: list[dict[str, Any]] = [
        _token(input_p, "input_uncached"),
        _token(round(input_p * cache_read_ratio, 6), "cache_read"),
        _token(round(input_p * cache_write_ratio, 6), "cache_write"),
        _token(output_p, "output"),
    ]
    if reasoning_same_as_output:
        components.append(_token(output_p, "reasoning"))
    return {
        "schema_version": SCHEMA_SNAPSHOT,
        "provider": provider,
        "model": model,
        "effective_at": effective_at,
        "currency": "USD",
        "pricing": components,
        "source": {
            "kind": source_kind,
            "url": source_url or f"https://platform.{provider}.com/pricing",
            "retrieved_at": source_retrieved_at or effective_at,
            "content_hash": source_content_hash or f"builtin-{provider}-{model}",
            **({"revision": source_revision} if source_revision else {}),
        },
        "status": "verified",
    }


def builtin_snapshots() -> dict[str, dict[str, Any]]:
    """key = '<provider>/<model>' → PricingSnapshot (4 providers, COST-01)."""
    snapshots = {
        # OpenAI (GPT-4.1 family)
        "openai/gpt-4.1": _builtin_snapshot("openai", "gpt-4.1", input_p=2.0, output_p=8.0),
        "openai/gpt-4.1-mini": _builtin_snapshot("openai", "gpt-4.1-mini", input_p=0.4, output_p=1.6),
        "openai/gpt-4o": _builtin_snapshot("openai", "gpt-4o", input_p=2.5, output_p=10.0),
        "openai/gpt-4o-mini": _builtin_snapshot("openai", "gpt-4o-mini", input_p=0.15, output_p=0.6),
        "openai/o3": _builtin_snapshot("openai", "o3", input_p=2.0, output_p=8.0),
        "openai/o4-mini": _builtin_snapshot("openai", "o4-mini", input_p=1.1, output_p=4.4),
        # Anthropic (Claude 4 family)
        "anthropic/claude-sonnet-4": _builtin_snapshot(
            "anthropic", "claude-sonnet-4", input_p=3.0, output_p=15.0, effective_at="2026-08-16"),
        "anthropic/claude-haiku-4-5": _builtin_snapshot(
            "anthropic", "claude-haiku-4-5", input_p=1.0, output_p=5.0, effective_at="2026-08-16"),
        "anthropic/claude-opus-4": _builtin_snapshot(
            "anthropic", "claude-opus-4", input_p=15.0, output_p=75.0, effective_at="2026-08-16"),
        # Google (Gemini)
        "google/gemini-2.5-pro": _builtin_snapshot(
            "google", "gemini-2.5-pro", input_p=1.25, output_p=10.0, effective_at="2026-08-16"),
        "google/gemini-2.5-flash": _builtin_snapshot(
            "google", "gemini-2.5-flash", input_p=0.30, output_p=2.50, effective_at="2026-08-16"),
        # OpenAI GPT-5.6 family and Codex variants.  These entries use the
        # fixed LiteLLM catalog above so model-only agents (e.g. Codex) have a
        # calculable cold-start quote before local usage exists.
        "openai/gpt-5.6-sol": _builtin_snapshot(
            "openai", "gpt-5.6-sol", input_p=5.0, output_p=30.0,
            cache_read_ratio=0.1, cache_write_ratio=1.25,
            effective_at="2026-08-15", source_kind="community_catalog",
            source_url=LITELLM_CATALOG_URL, source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.6-luna": _builtin_snapshot(
            "openai", "gpt-5.6-luna", input_p=0.2, output_p=1.2,
            cache_read_ratio=0.1, cache_write_ratio=1.25,
            effective_at="2026-08-15", source_kind="community_catalog",
            source_url=LITELLM_CATALOG_URL, source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.6-terra": _builtin_snapshot(
            "openai", "gpt-5.6-terra", input_p=2.0, output_p=12.0,
            cache_read_ratio=0.1, cache_write_ratio=1.25,
            effective_at="2026-08-15", source_kind="community_catalog",
            source_url=LITELLM_CATALOG_URL, source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.6": _builtin_snapshot(
            "openai", "gpt-5.6", input_p=5.0, output_p=30.0,
            cache_read_ratio=0.1, cache_write_ratio=1.25,
            effective_at="2026-08-15", source_kind="community_catalog",
            source_url=LITELLM_CATALOG_URL, source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.5": _builtin_snapshot(
            "openai", "gpt-5.5", input_p=5.0, output_p=30.0,
            cache_read_ratio=0.1, cache_write_ratio=1.25,
            effective_at="2026-08-15", source_kind="community_catalog",
            source_url=LITELLM_CATALOG_URL, source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.3-codex": _builtin_snapshot(
            "openai", "gpt-5.3-codex", input_p=1.75, output_p=14.0,
            cache_read_ratio=0.1, cache_write_ratio=0.0,
            reasoning_same_as_output=True, effective_at="2026-08-15",
            source_kind="community_catalog", source_url=LITELLM_CATALOG_URL,
            source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        "openai/gpt-5.2-codex": _builtin_snapshot(
            "openai", "gpt-5.2-codex", input_p=1.75, output_p=14.0,
            cache_read_ratio=0.1, cache_write_ratio=0.0,
            reasoning_same_as_output=True, effective_at="2026-08-15",
            source_kind="community_catalog", source_url=LITELLM_CATALOG_URL,
            source_revision=LITELLM_CATALOG_REVISION,
            source_content_hash=LITELLM_CATALOG_CONTENT_HASH,
            source_retrieved_at=LITELLM_CATALOG_RETRIEVED_AT,
        ),
        # DeepSeek (V4 family; cache hit/miss separated per plan §3)
        "deepseek/deepseek-v4-flash": _builtin_snapshot(
            "deepseek", "deepseek-v4-flash", input_p=0.07, output_p=0.28, effective_at="2026-08-16"),
        "deepseek/deepseek-v4-pro": _builtin_snapshot(
            "deepseek", "deepseek-v4-pro", input_p=0.27, output_p=1.1, effective_at="2026-08-16"),
        "deepseek/deepseek-chat": _builtin_snapshot(
            "deepseek", "deepseek-chat", input_p=0.27, output_p=1.1, effective_at="2026-08-16"),
        "deepseek/deepseek-reasoner": _builtin_snapshot(
            "deepseek", "deepseek-reasoner", input_p=0.55, output_p=2.19, effective_at="2026-08-16"),
        # Kimi / Moonshot (kept for reference — never used for tests, see memory)
        "kimi/kimi-k2": _builtin_snapshot("kimi", "kimi-k2", input_p=0.6, output_p=2.5, effective_at="2026-08-16"),
        "kimi/kimi-k3": _builtin_snapshot("kimi", "kimi-k3", input_p=0.6, output_p=2.5, effective_at="2026-08-16"),
    }
    # LiteLLM is a mature, useful cross-provider catalog, but it is not the
    # provider's billing authority. Keep its status visibly suspected.
    for snapshot in snapshots.values():
        if snapshot["source"]["kind"] == "community_catalog":
            snapshot["status"] = "suspected"
    return snapshots


def validate_snapshot(snapshot: dict[str, Any]) -> None:
    if snapshot.get("schema_version") != SCHEMA_SNAPSHOT:
        raise TariffError("snapshot: schema_version mismatch")
    for field in ("provider", "model", "effective_at", "currency"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field]:
            raise TariffError(f"snapshot: {field} required")
    pricing = snapshot.get("pricing")
    if not isinstance(pricing, list) or not pricing:
        raise TariffError("snapshot: pricing must be a non-empty list of TariffComponents")
    for component in pricing:
        kind = component.get("kind")
        if kind not in COMPONENT_KINDS:
            raise TariffError(f"snapshot: unknown component kind {kind!r}")
        if kind == "multiplier":
            if not isinstance(component.get("factor"), (int, float)) or component["factor"] <= 0:
                raise TariffError("snapshot: multiplier.factor must be > 0")
            if not isinstance(component.get("condition"), str) or not component["condition"]:
                raise TariffError("snapshot: multiplier.condition required")
        else:
            category = component.get("category")
            valid_categories = TOKEN_CATEGORIES if kind == "token" else TOOL_CATEGORIES
            if category not in valid_categories:
                raise TariffError(f"snapshot: unknown {kind} category {category!r}")
            if not isinstance(component.get("price"), (int, float)) or component["price"] < 0:
                raise TariffError(f"snapshot: {kind}.price must be non-negative")
    source = snapshot.get("source")
    if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
        raise TariffError("snapshot: source.kind must be one of official_api/official_doc/community_catalog/user_override")


def validate_all_snapshots() -> list[str]:
    keys: list[str] = []
    for key, snapshot in builtin_snapshots().items():
        validate_snapshot(snapshot)
        keys.append(key)
    return keys


# ---------------------------------------------------------------- persistence (store/pricing/)

def _snapshots_path(store: str | Path) -> Path:
    return Path(store) / "pricing"


def _pricing_records(store: str | Path) -> list[dict[str, Any]]:
    """Read immutable per-model snapshots, including legacy unversioned files."""
    directory = _snapshots_path(store)
    records: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                validate_snapshot(snapshot)
                records.append(snapshot)
            except (OSError, json.JSONDecodeError, TariffError):
                continue
    return records


def _snapshot_recency(snapshot: dict[str, Any]) -> tuple[str, str]:
    source = snapshot.get("source") or {}
    return str(source.get("retrieved_at") or ""), str(snapshot.get("effective_at") or "")


def list_pricing_snapshots(store: str | Path) -> dict[str, dict[str, Any]]:
    """Newest persisted snapshot per exact Provider + Model.

    Files themselves are append-only.  This method is only a current-state
    projection and never rewrites an older rate card.
    """
    result: dict[str, dict[str, Any]] = {}
    for snapshot in _pricing_records(store):
        key = f"{snapshot['provider']}/{snapshot['model']}"
        previous = result.get(key)
        if previous is None or _snapshot_recency(snapshot) > _snapshot_recency(previous):
            result[key] = snapshot
    return result


def save_snapshot(store: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Append one immutable model rate card; identical content is idempotent."""
    validate_snapshot(snapshot)
    source = snapshot.get("source") or {}
    identity = json.dumps(
        {
            "provider": snapshot.get("provider"),
            "model": snapshot.get("model"),
            "effective_at": snapshot.get("effective_at"),
            "pricing": snapshot.get("pricing"),
            "source_kind": source.get("kind"),
            "source_hash": source.get("content_hash"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    provider = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(snapshot["provider"]))[:80]
    model = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(snapshot["model"]))[:120]
    path = _snapshots_path(store) / f"{provider}__{model}__{suffix}.json"
    if not path.is_file():
        _atomic_write(path, json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
    return snapshot


def _lookup_builtin(provider: str, model_id: str) -> dict[str, Any] | None:
    """Provider + Model lookup, case-insensitive, then known product aliases.

    A bare-model fallback across every provider is still unsafe: the same
    model can have different prices on DeepSeek, OpenRouter, a relay, or a
    subscription-backed CLI. Aliases only expand *known* product SKUs.
    """
    snapshots = builtin_snapshots()
    for item_provider, item_model in catalog_lookup_keys(provider, model_id):
        key = f"{item_provider}/{item_model}".lower()
        for candidate_key, snapshot in snapshots.items():
            if candidate_key.lower() == key:
                return snapshot
    return None


def resolve_snapshot(
    store: str | Path,
    provider: str,
    model_id: str,
) -> tuple[dict[str, Any], str]:
    """Resolve the best snapshot for provider/model with its source kind.

    Priority: user_override > community_catalog (models.dev sync) > builtin.
    Returns (snapshot, source_kind); None-like miss is signalled by raising
    TariffError('no tariff for ...') — callers must map that to cost=None.
    """
    key_lower = f"{provider}/{model_id}".lower()
    exact = [
        snapshot for snapshot in _pricing_records(store)
        if f"{snapshot.get('provider')}/{snapshot.get('model')}".lower() == key_lower
    ]

    def newest(kinds: set[str]) -> dict[str, Any] | None:
        candidates = [
            snapshot for snapshot in exact
            if (snapshot.get("source") or {}).get("kind") in kinds
        ]
        return max(candidates, key=_snapshot_recency) if candidates else None

    # Explicit local rates and source-authoritative snapshots outrank a
    # community catalog.  A freshly synced catalog outranks old community
    # per-model files and the bundled fallback.
    snapshot = newest({"user_override"}) or newest({"official_api", "official_doc"})
    if snapshot is not None:
        return snapshot, snapshot["source"]["kind"]
    catalog_snapshot = _models_dev_snapshot(store, provider, model_id)
    if catalog_snapshot is not None:
        return catalog_snapshot, "community_catalog"
    litellm_snapshot = _litellm_snapshot(store, provider, model_id)
    if litellm_snapshot is not None:
        return litellm_snapshot, "community_catalog"
    snapshot = newest({"community_catalog"})
    if snapshot is not None:
        return snapshot, "community_catalog"
    builtin = _lookup_builtin(provider, model_id)
    if builtin is not None:
        return builtin, builtin["source"]["kind"]
    raise TariffError(f"no tariff for {provider}/{model_id}")


# ---------------------------------------------------------------- TariffEngine

def compute_from_snapshot(
    snapshot: dict[str, Any],
    usage: dict[str, Any],
    *,
    multiplier_context: dict[str, int] | None = None,
) -> dict[str, Any]:
    """usage × snapshot components → {total, by_category, multipliers}.

    usage: ExecutionUsage {input_tokens, output_tokens, cache_read_tokens,
    cache_write_tokens, reasoning_tokens, tool_calls{category: count}}.
    Returns a calculation dict used by CostActual; cost is None-free here
    (caller handles "no tariff" via resolve_snapshot).
    """
    by_category: dict[str, float] = {}
    multiplier_context = multiplier_context or {}
    for component in snapshot.get("pricing", []):
        kind = component["kind"]
        if kind == "multiplier":
            continue  # applied below over the subtotal
        price = float(component["price"])
        unit = component.get("unit", "1m_tokens")
        if kind == "token":
            category = component["category"]
            if category == "input_uncached":
                count = int(usage.get("input_tokens", 0))
            elif category == "input_cached" or category == "cache_read":
                count = int(usage.get("cache_read_tokens", 0))
            elif category == "cache_write":
                count = int(usage.get("cache_write_tokens", 0))
            elif category == "reasoning":
                # Canonical output_tokens includes reasoning tokens for the
                # currently supported OpenAI/DeepSeek/Google adapters.  Keep
                # reasoning_tokens as observability metadata and only charge
                # it separately when an adapter explicitly says it is not
                # already included in output.
                count = (
                    int(usage.get("reasoning_tokens", 0))
                    if usage.get("reasoning_tokens_in_output") is False
                    else 0
                )
            else:  # output
                count = int(usage.get("output_tokens", 0))
            if count > 0:
                divisor = 1_000_000 if unit == "1m_tokens" else 1_000
                by_category[category] = round(count * price / divisor, 9)
        elif kind == "tool_call":
            category = component["category"]
            tools = usage.get("tool_calls") or {}
            count = int(tools.get(category, 0) if isinstance(tools, dict) else 0)
            if count > 0:
                divisor = 1000 if unit == "1000_requests" else 1
                by_category[f"tool_{category}"] = round(count * price / divisor, 9)

    subtotal = sum(by_category.values())
    total = subtotal
    multipliers: list[dict[str, Any]] = []
    for component in snapshot.get("pricing", []):
        if component.get("kind") != "multiplier":
            continue
        condition = str(component.get("condition", ""))
        if _condition_applies(condition, usage, multiplier_context):
            factor = float(component["factor"])
            total *= factor
            multipliers.append({"condition": condition, "factor": factor})
    return {"total": round(total, 9), "by_category": by_category, "multipliers": multipliers}


def _condition_applies(condition: str, usage: dict[str, Any], context: dict[str, int]) -> bool:
    """Very small rule engine for multiplier conditions like
    'input_tokens > 200000'. Unknown operators are ignored (never blocks cost)."""
    condition = condition.strip()
    for operator in (">=", "<=", ">", "<", "=="):
        if operator in condition:
            left, _, right = condition.partition(operator)
            field = left.strip()
            if field not in _MULTIPLIER_FIELDS:
                return False
            value = int(usage.get(field, 0) or context.get(field, 0) or 0)
            try:
                threshold = float(right.strip())
            except ValueError:
                return False
            if operator == ">=":
                return value >= threshold
            if operator == "<=":
                return value <= threshold
            if operator == ">":
                return value > threshold
            if operator == "<":
                return value < threshold
            return value == threshold
    return False


def compute_actual_from_tariff(
    store: str | Path,
    *,
    provider: str,
    model_id: str,
    usage: dict[str, Any],
    usage_source: str = "estimated",
) -> dict[str, Any]:
    """Full COST-04 pipeline: resolve snapshot → compute → CostActual.

    Unknown tariff → cost=None + reason (never $0). Returns the upgraded
    CostActual (schema v0.2): pricing_snapshot ref + cash_cost + calculation
    + original usage + usage_source, so it can be re-computed later.
    """
    if usage_source not in (
        "exact_provider", "native_agent", "proxy", "estimated", "unknown", "agent_reported",
    ):
        raise TariffError(
            "usage_source must be exact_provider/native_agent/proxy/"
            f"estimated/unknown/agent_reported, got {usage_source!r}"
        )
    try:
        snapshot, source_kind = resolve_snapshot(store, provider, model_id)
    except TariffError:
        return {
            "schema_version": SCHEMA_ACTUAL,
            "provider": provider, "model": model_id,
            "usage": usage, "usage_source": usage_source,
            "pricing_snapshot": None, "cash_cost": None,
            "reason": "no_tariff", "currency": "USD",
            "created_at": _now(),
        }
    calculation = compute_from_snapshot(snapshot, usage)
    total = calculation["total"]
    return {
        "schema_version": SCHEMA_ACTUAL,
        "provider": provider, "model": model_id,
        "usage": usage, "usage_source": usage_source,
        "pricing_snapshot": f"{snapshot['provider']}/{snapshot['model']}@{snapshot['effective_at']}",
        "pricing_source": source_kind,
        "cash_cost": round(total, 6),
        "currency": snapshot.get("currency", "USD"),
        "calculation": calculation,
        "created_at": _now(),
    }


def counterfactual_current_cost(store: str | Path, actual: dict[str, Any]) -> dict[str, Any]:
    """Recompute an old CostActual at today's tariff (plan §10/§11).

    historical_cost stays; counterfactual_current_cost = usage × current
    snapshot. Keeps experience useful even after price cuts.
    """
    usage = actual.get("usage") or {}
    provider = actual.get("provider") or ""
    model = actual.get("model") or ""
    recomputed = compute_actual_from_tariff(
        store, provider=provider, model_id=model,
        usage=usage, usage_source=actual.get("usage_source", "estimated"),
    )
    return {
        "historical_cost": actual.get("cash_cost"),
        "counterfactual_current_cost": recomputed.get("cash_cost"),
        "pricing_snapshot": recomputed.get("pricing_snapshot"),
        "usage": usage,
    }


# ---------------------------------------------------------------- models.dev sync (COST-02)


def _catalog_path(store: str | Path) -> Path:
    return Path(store) / "pricing" / "catalogs" / "models-dev"


def _catalog_manifest_path(store: str | Path) -> Path:
    return _catalog_path(store) / "manifest.json"


def _load_catalog_manifest(store: str | Path) -> dict[str, Any]:
    path = _catalog_manifest_path(store)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write_gzip(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as compressed:
            compressed.write(content.encode("utf-8"))
        temp = handle.name
    os.replace(temp, path)


@lru_cache(maxsize=8)
def _read_models_dev_catalog(path_text: str, content_hash: str) -> dict[str, Any]:
    del content_hash  # cache key only
    try:
        with gzip.open(path_text, "rt", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def pricing_catalog_status(store: str | Path) -> dict[str, Any]:
    """Safe status projection for the licensed live price catalog."""
    manifest = _load_catalog_manifest(store)
    snapshot_file = manifest.get("snapshot_file")
    available = bool(snapshot_file and (_catalog_path(store) / str(snapshot_file)).is_file())
    return {
        "source_id": "models.dev",
        "name": "models.dev",
        "repository": MODELS_DEV_REPOSITORY,
        "api_url": MODELS_DEV_API,
        "license": MODELS_DEV_LICENSE,
        "license_url": MODELS_DEV_LICENSE_URL,
        "update_schedule": "upstream_hourly; local_manual_refresh",
        "available": available,
        "retrieved_at": manifest.get("retrieved_at"),
        "content_hash": manifest.get("content_hash"),
        "etag": manifest.get("etag"),
        "providers": manifest.get("providers", 0),
        "models": manifest.get("models", 0),
        "priced_models": manifest.get("priced_models", 0),
        "last_error": manifest.get("last_error"),
        "fallback": _litellm_catalog_status(store),
    }


def _ids_equal(left: str, right: str) -> bool:
    return _norm_id(left) == _norm_id(right) or str(left).lower() == str(right).lower()


def _match_named_entry(entries: dict[str, Any], model_id: str) -> dict[str, Any] | None:
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        if _ids_equal(key, model_id) or _ids_equal(str(value.get("id") or ""), model_id):
            return value
    return None


def _match_models_dev_entry(
    catalog: dict[str, Any],
    provider: str,
    model_id: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for item_provider, item_model in catalog_lookup_keys(provider, model_id):
        provider_entry = next(
            (
                value for key, value in catalog.items()
                if isinstance(value, dict) and _ids_equal(str(key), item_provider)
            ),
            None,
        )
        if not provider_entry:
            continue
        entry = _match_named_entry(provider_entry.get("models") or {}, item_model)
        cost = (entry or {}).get("cost") or {}
        if entry and cost.get("input") is not None and cost.get("output") is not None:
            return provider_entry, entry
    hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    aliases = {_norm_id(alias) for alias in MODEL_ALIASES.get(_norm_id(model_id), ())}
    for value in catalog.values():
        if not isinstance(value, dict):
            continue
        for key, entry in (value.get("models") or {}).items():
            if not isinstance(entry, dict):
                continue
            if _norm_id(key) in aliases or _norm_id(str(entry.get("id") or "")) in aliases:
                cost = entry.get("cost") or {}
                if cost.get("input") is not None and cost.get("output") is not None:
                    hits.append((value, entry))
    if len(hits) == 1:
        return hits[0]
    return None


def _litellm_catalog_path(store: str | Path) -> Path:
    return Path(store) / "pricing" / "catalogs" / "litellm"


def _litellm_manifest_path(store: str | Path) -> Path:
    return _litellm_catalog_path(store) / "manifest.json"


def _load_litellm_manifest(store: str | Path) -> dict[str, Any]:
    path = _litellm_manifest_path(store)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _litellm_catalog_status(store: str | Path) -> dict[str, Any]:
    manifest = _load_litellm_manifest(store)
    snapshot_file = manifest.get("snapshot_file")
    available = bool(snapshot_file and (_litellm_catalog_path(store) / str(snapshot_file)).is_file())
    return {
        "source_id": "litellm",
        "name": "LiteLLM model prices",
        "api_url": LITELLM_API,
        "available": available,
        "retrieved_at": manifest.get("retrieved_at"),
        "content_hash": manifest.get("content_hash"),
        "models": manifest.get("models", 0),
        "priced_models": manifest.get("priced_models", 0),
        "last_error": manifest.get("last_error"),
    }


@lru_cache(maxsize=4)
def _read_litellm_catalog(path: str, content_hash: str) -> dict[str, Any]:
    del content_hash  # cache key only
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            catalog = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise TariffError(f"LiteLLM catalog unreadable: {exc}") from exc
    if not isinstance(catalog, dict):
        raise TariffError("LiteLLM catalog must be an object")
    return catalog


def _litellm_cost_pair(entry: dict[str, Any]) -> tuple[float, float] | None:
    try:
        input_cost = float(entry.get("input_cost_per_token"))
        output_cost = float(entry.get("output_cost_per_token"))
    except (TypeError, ValueError):
        return None
    if input_cost < 0 or output_cost < 0:
        return None
    return input_cost * 1_000_000, output_cost * 1_000_000


def _match_litellm_entry(
    catalog: dict[str, Any],
    provider: str,
    model_id: str,
) -> tuple[str, str, dict[str, Any]] | None:
    wanted_models = {_norm_id(model_id), *(_norm_id(alias) for alias in MODEL_ALIASES.get(_norm_id(model_id), ()))}
    wanted_providers = {_norm_id(provider), *PROVIDER_ALIASES.get(_norm_id(provider), ())}
    exact: list[tuple[str, str, dict[str, Any]]] = []
    model_only: list[tuple[str, str, dict[str, Any]]] = []
    for key, entry in catalog.items():
        if not isinstance(entry, dict) or _litellm_cost_pair(entry) is None:
            continue
        raw_key = str(key)
        if "/" in raw_key:
            key_provider, _, key_model = raw_key.partition("/")
        else:
            key_provider, key_model = str(entry.get("litellm_provider") or ""), raw_key
        if _norm_id(key_model) not in wanted_models:
            continue
        catalog_provider = _norm_id(key_provider or str(entry.get("litellm_provider") or ""))
        hit = (catalog_provider or provider, key_model or model_id, entry)
        if catalog_provider and catalog_provider in wanted_providers:
            exact.append(hit)
        else:
            model_only.append(hit)
    if exact:
        return exact[0]
    if len(model_only) == 1:
        return model_only[0]
    return None


def _litellm_snapshot(
    store: str | Path,
    provider: str,
    model_id: str,
) -> dict[str, Any] | None:
    manifest = _load_litellm_manifest(store)
    snapshot_file = manifest.get("snapshot_file")
    content_hash = str(manifest.get("content_hash") or "")
    if not snapshot_file or not content_hash:
        return None
    path = _litellm_catalog_path(store) / str(snapshot_file)
    if not path.is_file():
        return None
    try:
        catalog = _read_litellm_catalog(str(path), content_hash)
    except (OSError, json.JSONDecodeError, TariffError):
        return None
    matched = _match_litellm_entry(catalog, provider, model_id)
    if matched is None:
        return None
    matched_provider, matched_model, entry = matched
    pair = _litellm_cost_pair(entry)
    if pair is None:
        return None
    input_p, output_p = pair
    cache_read = entry.get("cache_read_input_token_cost")
    cache_write = entry.get("cache_creation_input_token_cost")
    pricing = [_token(round(input_p, 6), "input_uncached"), _token(round(output_p, 6), "output")]
    if cache_read is not None:
        pricing.append(_token(round(float(cache_read) * 1_000_000, 6), "cache_read"))
    if cache_write is not None:
        pricing.append(_token(round(float(cache_write) * 1_000_000, 6), "cache_write"))
    snapshot = {
        "schema_version": SCHEMA_SNAPSHOT,
        "provider": matched_provider or provider,
        "model": matched_model or model_id,
        "effective_at": str(manifest.get("retrieved_at") or _now())[:10],
        "currency": "USD",
        "pricing": pricing,
        "source": {
            "kind": "community_catalog",
            "url": LITELLM_API,
            "retrieved_at": manifest.get("retrieved_at"),
            "content_hash": content_hash,
        },
        "status": "suspected",
    }
    try:
        validate_snapshot(snapshot)
    except TariffError:
        return None
    return snapshot


def _validate_litellm_catalog(catalog: Any) -> tuple[int, int]:
    if not isinstance(catalog, dict) or not catalog:
        raise TariffError("LiteLLM catalog must be a non-empty object")
    models = priced = 0
    for key, entry in catalog.items():
        if key in {"sample_spec"} or not isinstance(entry, dict):
            continue
        models += 1
        if _litellm_cost_pair(entry) is not None:
            priced += 1
    if models < 100 or priced < 50:
        raise TariffError(f"LiteLLM catalog failed sanity check: models={models}, priced={priced}")
    return models, priced


def sync_litellm(
    store: str | Path,
    *,
    timeout: float = 20.0,
    fetcher: Callable[[dict[str, str]], tuple[str, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Refresh the community LiteLLM price table as a secondary catalog."""
    store = Path(store)
    previous = _load_litellm_manifest(store)
    headers = {
        "Accept": "application/json",
        "User-Agent": "ICLE pricing-catalog/0.1 (+https://github.com/BerriAI/litellm)",
    }
    try:
        if fetcher is not None:
            raw, response_headers = fetcher(headers)
            response_headers = {str(key).lower(): str(value) for key, value in response_headers.items()}
        else:
            request = urllib.request.Request(LITELLM_API, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                response_headers = {key.lower(): value for key, value in response.headers.items()}
    except Exception as exc:  # noqa: BLE001 — catalog refresh is best-effort
        error = f"network: {exc}"
    else:
        try:
            catalog = json.loads(raw)
            models, priced = _validate_litellm_catalog(catalog)
        except (json.JSONDecodeError, TariffError) as exc:
            error = f"invalid_catalog: {exc}"
        else:
            retrieved_at = _now()
            content_hash = _content_hash(raw)
            changed = content_hash != previous.get("content_hash")
            if changed:
                stamp = retrieved_at.replace(":", "").replace("-", "").replace("+", "_").replace(".", "")
                filename = f"litellm__{stamp}__{content_hash.removeprefix('sha256:')[:16]}.json.gz"
                _atomic_write_gzip(_litellm_catalog_path(store) / filename, raw)
            else:
                filename = str(previous.get("snapshot_file") or "")
            manifest = {
                "schema_version": "icle-pricing-catalog-manifest/v0.1",
                "source_id": "litellm",
                "source_url": LITELLM_API,
                "retrieved_at": retrieved_at,
                "content_hash": content_hash,
                "etag": response_headers.get("etag"),
                "snapshot_file": filename,
                "models": models,
                "priced_models": priced,
                "last_error": None,
            }
            _atomic_write(
                _litellm_manifest_path(store),
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            _read_litellm_catalog.cache_clear()
            return {**_litellm_catalog_status(store), "synced": True, "changed": changed, "reason": "ok"}
    if previous:
        failed = {**previous, "last_error": error}
        _atomic_write(
            _litellm_manifest_path(store),
            json.dumps(failed, ensure_ascii=False, indent=2) + "\n",
        )
    return {**_litellm_catalog_status(store), "synced": False, "changed": False, "reason": error}


def _models_dev_snapshot(
    store: str | Path,
    provider: str,
    model_id: str,
) -> dict[str, Any] | None:
    manifest = _load_catalog_manifest(store)
    snapshot_file = manifest.get("snapshot_file")
    content_hash = str(manifest.get("content_hash") or "")
    if not snapshot_file or not content_hash:
        return None
    path = _catalog_path(store) / str(snapshot_file)
    if not path.is_file():
        return None
    catalog = _read_models_dev_catalog(str(path), content_hash)
    matched = _match_models_dev_entry(catalog, provider, model_id)
    if matched is None:
        return None
    provider_entry, entry = matched
    cost = (entry or {}).get("cost") or {}
    if cost.get("input") is None or cost.get("output") is None:
        return None
    categories = (
        ("input", "input_uncached"),
        ("cache_read", "cache_read"),
        ("cache_write", "cache_write"),
        ("output", "output"),
        ("reasoning", "reasoning"),
    )
    pricing = [
        _token(float(cost[field]), category)
        for field, category in categories
        if cost.get(field) is not None
    ]
    snapshot = {
        "schema_version": SCHEMA_SNAPSHOT,
        "provider": str((provider_entry or {}).get("id") or provider),
        "model": str((entry or {}).get("id") or model_id),
        "effective_at": str((entry or {}).get("last_updated") or manifest.get("retrieved_at") or _now())[:10],
        "currency": "USD",
        "pricing": pricing,
        "source": {
            "kind": "community_catalog",
            "url": MODELS_DEV_API,
            "repository": MODELS_DEV_REPOSITORY,
            "license": MODELS_DEV_LICENSE,
            "license_url": MODELS_DEV_LICENSE_URL,
            "retrieved_at": manifest.get("retrieved_at"),
            "content_hash": content_hash,
            "etag": manifest.get("etag"),
            "model_source_url": (provider_entry or {}).get("doc"),
        },
        "status": "suspected",
    }
    try:
        validate_snapshot(snapshot)
    except TariffError:
        return None
    return snapshot


def _validate_models_dev_catalog(catalog: Any) -> tuple[int, int, int]:
    if not isinstance(catalog, dict) or not catalog:
        raise TariffError("models.dev catalog must be a non-empty object")
    providers = models = priced = 0
    for provider in catalog.values():
        entries = (provider or {}).get("models") if isinstance(provider, dict) else None
        if not isinstance(entries, dict):
            continue
        providers += 1
        models += len(entries)
        priced += sum(
            1 for entry in entries.values()
            if isinstance(entry, dict)
            and isinstance(entry.get("cost"), dict)
            and entry["cost"].get("input") is not None
            and entry["cost"].get("output") is not None
        )
    if providers < 10 or models < 100 or priced < 50:
        raise TariffError(
            f"models.dev catalog failed sanity check: providers={providers}, models={models}, priced={priced}"
        )
    return providers, models, priced


def sync_models_dev(
    store: str | Path,
    *,
    timeout: float = 15.0,
    fetcher: Callable[[dict[str, str]], tuple[str, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Refresh the MIT-licensed models.dev catalog without losing old data.

    Upstream refreshes hourly.  ICLE refresh is explicit and uses ETag when
    available.  A changed response is stored as an immutable gzip snapshot;
    a failed or invalid response leaves the previous valid snapshot active.
    """
    store = Path(store)
    previous = _load_catalog_manifest(store)
    headers = {
        "Accept": "application/json",
        "User-Agent": "ICLE pricing-catalog/0.1 (+https://github.com/anomalyco/models.dev)",
    }
    if previous.get("etag"):
        headers["If-None-Match"] = str(previous["etag"])
    try:
        if fetcher is not None:
            raw, response_headers = fetcher(headers)
            response_headers = {
                str(key).lower(): str(value) for key, value in response_headers.items()
            }
        else:
            request = urllib.request.Request(MODELS_DEV_API, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and previous:
            return {**pricing_catalog_status(store), "synced": True, "changed": False, "reason": "not_modified"}
        error = f"network: HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — catalog refresh is best-effort
        error = f"network: {exc}"
    else:
        try:
            catalog = json.loads(raw)
            providers, models, priced = _validate_models_dev_catalog(catalog)
        except (json.JSONDecodeError, TariffError) as exc:
            error = f"invalid_catalog: {exc}"
        else:
            retrieved_at = _now()
            content_hash = _content_hash(raw)
            changed = content_hash != previous.get("content_hash")
            if changed:
                stamp = retrieved_at.replace(":", "").replace("-", "").replace("+", "_").replace(".", "")
                filename = f"models-dev__{stamp}__{content_hash.removeprefix('sha256:')[:16]}.json.gz"
                _atomic_write_gzip(_catalog_path(store) / filename, raw)
            else:
                filename = str(previous.get("snapshot_file") or "")
            manifest = {
                "schema_version": "icle-pricing-catalog-manifest/v0.1",
                "source_id": "models.dev",
                "source_url": MODELS_DEV_API,
                "repository": MODELS_DEV_REPOSITORY,
                "license": MODELS_DEV_LICENSE,
                "license_url": MODELS_DEV_LICENSE_URL,
                "retrieved_at": retrieved_at,
                "content_hash": content_hash,
                "etag": response_headers.get("etag"),
                "last_modified": response_headers.get("last-modified"),
                "snapshot_file": filename,
                "providers": providers,
                "models": models,
                "priced_models": priced,
                "last_error": None,
            }
            _atomic_write(
                _catalog_manifest_path(store),
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            _read_models_dev_catalog.cache_clear()
            return {**pricing_catalog_status(store), "synced": True, "changed": changed, "reason": "ok"}
    if previous:
        failed = {**previous, "last_error": error}
        _atomic_write(
            _catalog_manifest_path(store),
            json.dumps(failed, ensure_ascii=False, indent=2) + "\n",
        )
    return {**pricing_catalog_status(store), "synced": False, "changed": False, "reason": error}


def _component_price(snapshot: dict[str, Any], category: str) -> float | None:
    for component in snapshot.get("pricing", []):
        if component.get("kind") == "token" and component.get("category") == category:
            return float(component["price"])
    return None
