"""ExecutionUsage capture (COST-03) — provider usage normalization.

Normalizes provider-specific usage payloads into one ExecutionUsage shape:

    {
      "input_tokens": int,
      "output_tokens": int,
      "cache_read_tokens": int,
      "cache_write_tokens": int,
      "reasoning_tokens": int,
      "tool_calls": {"<category>": int},
      "usage_source": "exact_provider" | "native_agent" | "proxy" | "estimated" | "unknown"
    }

Priority (plan §8): provider response > agent native log > harness event >
local proxy observation > estimated token count. estimated must never be
mixed with provider truth — usage_source is carried everywhere.
"""

from __future__ import annotations

from typing import Any

USAGE_SOURCES = ("exact_provider", "native_agent", "proxy", "estimated", "unknown", "agent_reported")

# ExecutionUsage canonical key → default
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens",
              "cache_write_tokens", "reasoning_tokens")


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _usage(raw: dict[str, Any], *, source: str, **tokens: int) -> dict[str, Any]:
    usage: dict[str, Any] = {"tool_calls": {}, "usage_source": source}
    for key in USAGE_KEYS:
        usage[key] = _int(tokens.get(key, 0))
    return usage


# ---------------------------------------------------------------- adapters (COST-03)

def openai_compatible_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    """OpenAI-compatible: prompt_tokens/completion_tokens (+ cached details)."""
    if not raw or "prompt_tokens" not in raw and "completion_tokens" not in raw:
        return None
    prompt = _int(raw.get("prompt_tokens"))
    completion = _int(raw.get("completion_tokens"))
    details = raw.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens"))
    return _usage(raw, source="exact_provider",
                  input_tokens=prompt, output_tokens=completion,
                  cache_read_tokens=cached)


def anthropic_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Anthropic: input/output + cache_creation_input_tokens / cache_read_input_tokens."""
    if not raw or "input_tokens" not in raw and "output_tokens" not in raw:
        return None
    return _usage(raw, source="exact_provider",
                  input_tokens=_int(raw.get("input_tokens")),
                  output_tokens=_int(raw.get("output_tokens")),
                  cache_write_tokens=_int(raw.get("cache_creation_input_tokens")),
                  cache_read_tokens=_int(raw.get("cache_read_input_tokens")))


def google_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Google usageMetadata: prompt/candidate/cached/tool/thinking token counts."""
    metadata = raw.get("usageMetadata") if isinstance(raw, dict) else raw
    if not isinstance(metadata, dict):
        return None
    prompt = _int(metadata.get("promptTokenCount"))
    candidates = _int(metadata.get("candidatesTokenCount"))
    cached = _int(metadata.get("cachedContentTokenCount"))
    thoughts = _int(metadata.get("thoughtsTokenCount"))
    tools = _int(metadata.get("toolsTokenCount"))
    if not any((prompt, candidates, cached, thoughts, tools)):
        return None
    usage = _usage(metadata, source="exact_provider",
                   input_tokens=prompt, output_tokens=candidates + thoughts,
                   cache_read_tokens=cached, reasoning_tokens=thoughts)
    if tools:
        usage["tool_calls"]["other"] = tools
    return usage


def native_agent_usage(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Agent native logs (e.g. replay turn journal, session events).

    Best-effort: accept 'input_tokens'/'output_tokens' keys if present, else
    None so the caller falls back to estimated.
    """
    if not raw:
        return None
    if "input_tokens" in raw or "output_tokens" in raw:
        return _usage(raw, source="native_agent",
                      input_tokens=_int(raw.get("input_tokens")),
                      output_tokens=_int(raw.get("output_tokens")),
                      cache_read_tokens=_int(raw.get("cache_read_tokens")),
                      reasoning_tokens=_int(raw.get("reasoning_tokens")))
    return None


def estimate_usage(text: str, *, chars_per_token: float = 4.0) -> dict[str, Any]:
    """Rough token estimate when no provider truth exists (plan §8, lowest tier).

    output is unknown → output_tokens stays 0; input is estimated from text
    length. usage_source='estimated' so it is never confused with truth.
    """
    estimated_input = max(1, int(len(text) / chars_per_token))
    return _usage({}, source="estimated", input_tokens=estimated_input)


def normalize_usage(raw: dict[str, Any] | None, *, source_hint: str = "") -> dict[str, Any]:
    """Best-effort normalization with the plan's priority order.

    source_hint lets callers force a tier ('native_agent' / 'estimated');
    otherwise auto-detects OpenAI/Anthropic/Google shapes.
    """
    raw = raw or {}
    if source_hint in ("estimated", "unknown"):
        if source_hint == "estimated" and not raw:
            return _usage({}, source="estimated")
        return _usage(raw, source=source_hint)
    if source_hint == "agent_reported":
        return _usage(
            raw,
            source="agent_reported",
            input_tokens=_int(raw.get("input_tokens")),
            output_tokens=_int(raw.get("output_tokens")),
            cache_read_tokens=_int(raw.get("cache_read_tokens")),
            cache_write_tokens=_int(raw.get("cache_write_tokens")),
            reasoning_tokens=_int(raw.get("reasoning_tokens")),
        )
    adapter = None
    if source_hint == "native_agent":
        adapter = native_agent_usage
    else:
        for candidate in (openai_compatible_usage, anthropic_usage, google_usage, native_agent_usage):
            normalized = candidate(raw)
            if normalized is not None:
                adapter = None
                return normalized
    if adapter is not None:
        normalized = adapter(raw)
        if normalized is not None:
            return normalized
    if raw:
        # unknown shape but non-empty → keep as unknown rather than fabricate
        return _usage(raw, source="unknown",
                      input_tokens=_int(raw.get("input_tokens") or raw.get("prompt_tokens")),
                      output_tokens=_int(raw.get("output_tokens") or raw.get("completion_tokens")))
    return _usage({}, source="unknown")
