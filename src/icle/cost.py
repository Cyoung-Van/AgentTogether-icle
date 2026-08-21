"""CostEngine (v0.4 P11 / Batch 2): cost intelligence.

计划书 §22-29 核心:
- Estimated Cost ≠ Actual Cost(CostQuote / CostActual)
- BillingMode 区分 API_METERED / SUBSCRIPTION / LOCAL_COMPUTE / UNKNOWN —— 订阅
  不是免费,未知成本绝不是 $0(§23/§25)
- ModelCatalog: 内置常见模型价格表 + local overrides(models.dev 同步留接口,
  需网络时降级 builtin,§24)
- CostQuote: 历史 episode 统计(median/P25/P75)优先,cold start 用难度粗估(§26/§27)
- 多步成本: 每步 StepCostEstimate → PlanCostQuote(§28)
- Cost Prediction Error: 记录 estimated vs actual(§29)

无 LLM 依赖,纯确定性计算。
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any

from .localtime import LocalTimeError, local_month_key

BILLING_MODES = ("API_METERED", "SUBSCRIPTION", "LOCAL_COMPUTE", "UNKNOWN")

# 内置价格表(美元/1M tokens),source=builtin。只覆盖常见模型;未知模型
# 返回 None → cost=null + reason(绝不静默按 $0 算,§25)。
# 本地覆盖: store/catalog_overrides.json { "provider/model": {"input": .., "output": ..} }
BUILTIN_CATALOG: dict[str, dict[str, float]] = {
    # OpenAI
    "openai/gpt-4o": {"input": 2.5, "output": 10.0},
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.6},
    "openai/gpt-4.1": {"input": 2.0, "output": 8.0},
    "openai/o3": {"input": 2.0, "output": 8.0},
    "openai/o4-mini": {"input": 1.1, "output": 4.4},
    # Anthropic
    "anthropic/claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "anthropic/claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "anthropic/claude-opus-4": {"input": 15.0, "output": 75.0},
    # DeepSeek
    "deepseek/deepseek-v4-flash": {"input": 0.07, "output": 0.28},
    "deepseek/deepseek-v4-pro": {"input": 0.27, "output": 1.1},
    "deepseek/deepseek-chat": {"input": 0.27, "output": 1.1},
    "deepseek/deepseek-reasoner": {"input": 0.55, "output": 2.19},
    # Kimi / Moonshot
    "kimi/kimi-k2": {"input": 0.6, "output": 2.5},
    "kimi/kimi-k3": {"input": 0.6, "output": 2.5},
    # 通义 / 智谱 / Mistral / Groq / xAI
    "qwen/qwen-max": {"input": 1.2, "output": 6.0},
    "zhipu/glm-4-plus": {"input": 0.6, "output": 2.4},
    "mistral/mistral-large": {"input": 2.0, "output": 6.0},
    "groq/llama-3.3-70b": {"input": 0.59, "output": 0.79},
    "xai/grok-4": {"input": 3.0, "output": 15.0},
}


class CostError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


# ---------------------------------------------------------------- ModelCatalog (§24)


def _overrides_path(store: str | Path) -> Path:
    return Path(store) / "catalog_overrides.json"


def model_price(
    store: str | Path,
    model_id: str,
    *,
    provider: str = "",
) -> dict[str, float] | None:
    """Look up (input, output) $/1M tokens for a model.

    provider/model 归一化(LiteLLM 风格):查询同时尝试原样 / 全小写 /
    裸 model 键——真实结算传入 provider.display_name(如 "DeepSeek"),
    而价格表 key 是小写厂商 id("deepseek/deepseek-v4-flash"),必须归一化
    才能命中(修复:此前大小写不匹配 → 真实执行永远 cost=null)。本地
    override 优先于 builtin;未知返回 None(调用方必须把 cost 记为 null,
    绝不当作 0)。
    """
    raw_key = f"{provider}/{model_id}" if provider else model_id
    candidates = [raw_key, raw_key.lower()]
    if "/" in raw_key:
        bare = raw_key.split("/", 1)[1]
        candidates += [bare, bare.lower()]
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for cand in candidates:
        if cand not in seen:
            seen.add(cand)
            unique.append(cand)
    path = _overrides_path(store)
    overrides: dict[str, dict[str, float]] = {}
    if path.is_file():
        overrides = json.loads(path.read_text(encoding="utf-8"))
    for key in unique:
        if key in overrides:
            entry = overrides[key]
            return {"input": float(entry["input"]), "output": float(entry["output"]), "source": "override"}
    for key in unique:
        if key in BUILTIN_CATALOG:
            entry = BUILTIN_CATALOG[key]
            return {"input": entry["input"], "output": entry["output"], "source": "builtin"}
    return None


def set_price_override(
    store: str | Path,
    model_id: str,
    *,
    input_per_m: float,
    output_per_m: float,
    provider: str = "",
) -> dict[str, Any]:
    """Local price override(企业价/代理价,§24)。"""
    if input_per_m < 0 or output_per_m < 0:
        raise CostError("prices must be non-negative")
    key = f"{provider}/{model_id}" if provider else model_id
    path = _overrides_path(store)
    overrides = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    overrides[key] = {"input": input_per_m, "output": output_per_m}
    _atomic_write(path, json.dumps(overrides, ensure_ascii=False, indent=2) + "\n")
    return {"model": key, "input": input_per_m, "output": output_per_m, "source": "override"}


def billing_mode(provider: str, model_id: str) -> str:
    """Billing mode(§23): 本地/网关 → LOCAL_COMPUTE;订阅类 → SUBSCRIPTION;其余按 API。

    绝不能把 SUBSCRIPTION 的 $0 解释为"免费"——本函数只返回模式,金额由
    CostQuote/CostActual 按模式语义处理。
    """
    local_hints = ("ollama", "lmstudio", "vllm", "localai", "localhost", "127.0.0.1")
    if any(hint in provider.lower() for hint in local_hints):
        return "LOCAL_COMPUTE"
    subscription_hints = ("claude-code", "codex", "kimi-code", "subscription")
    if any(hint in model_id.lower() or hint in provider.lower() for hint in subscription_hints):
        return "SUBSCRIPTION"
    return "API_METERED"


# ---------------------------------------------------------------- CostActual (§25)


def compute_actual(
    store: str | Path,
    *,
    provider: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_tokens: int = 0,
) -> dict[str, Any]:
    """结算一次调用的真实成本。

    优先用 usage(input/output/cache tokens)× 价格;拿不到价格或 tokens 时
    cost=None + reason(绝不静默 0,§25)。返回 CostActual 文档。
    """
    if input_tokens < 0 or output_tokens < 0 or cache_tokens < 0:
        raise CostError("token counts must be non-negative")
    price = model_price(store, model_id, provider=provider)
    if price is None:
        return {
            "schema_version": "icle-cost-actual/v0.1",
            "provider": provider, "model": model_id,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_tokens": cache_tokens,
            "cost": None, "reason": "usage_unavailable", "billing_mode": billing_mode(provider, model_id),
            "created_at": _now(),
        }
    # cache tokens 打 1/10(常见折扣),输入按输入价、输出按输出价
    cost = (
        (input_tokens + cache_tokens * 0.1) * price["input"] / 1_000_000
        + output_tokens * price["output"] / 1_000_000
    )
    return {
        "schema_version": "icle-cost-actual/v0.1",
        "provider": provider, "model": model_id,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_tokens": cache_tokens,
        "cost": round(cost, 6),
        "price_source": price["source"],
        "billing_mode": billing_mode(provider, model_id),
        "created_at": _now(),
    }


# ---------------------------------------------------------------- CostQuote (§26-28)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def quote_from_history(
    store: str | Path,
    *,
    task_type: str,
    difficulty: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """基于相似历史任务的成本/耗时/Token 统计(§26)。

    history: [{task_type, difficulty, cost, duration_ms, input_tokens, output_tokens}]
    统计 median + P25/P75。样本 < 3 时 confidence=low(§27)。
    """
    history = history or []
    values = {
        "cost": [float(h["cost"]) for h in history if h.get("cost") is not None],
        "duration_ms": [float(h["duration_ms"]) for h in history if h.get("duration_ms") is not None],
        "input_tokens": [float(h["input_tokens"]) for h in history if h.get("input_tokens") is not None],
        "output_tokens": [float(h["output_tokens"]) for h in history if h.get("output_tokens") is not None],
    }

    def stats(key: str) -> dict[str, Any]:
        ordered = sorted(values[key])
        if not ordered:
            return {"median": None, "p25": None, "p75": None}
        n = len(ordered)
        return {
            "median": ordered[n // 2],
            "p25": ordered[max(0, n // 4)],
            "p75": ordered[min(n - 1, n * 3 // 4)],
        }

    n = len(history)
    return {
        "schema_version": "icle-cost-quote/v0.1",
        "basis": "history",
        "task_type": task_type, "difficulty": difficulty,
        "sample_size": n,
        "confidence": "low" if n < 3 else ("medium" if n < 10 else "high"),
        "cost": stats("cost"),
        "duration_ms": stats("duration_ms"),
        "input_tokens": stats("input_tokens"),
        "output_tokens": stats("output_tokens"),
    }


def quote_cold_start(
    *,
    difficulty: str,
    plan_steps: int,
    context_tokens: int = 4000,
) -> dict[str, Any]:
    """Cold start 粗估(§27): 无历史时按难度/步数/上下文估算,confidence=low。"""
    # 每难度估计总输出 token(启发式:难度越高产出越多)
    output_guess = {"D1": 800, "D2": 1500, "D3": 3000, "D4": 6000, "D5": 12000}.get(difficulty, 3000)
    input_guess = context_tokens + output_guess * plan_steps * 0.5
    return {
        "schema_version": "icle-cost-quote/v0.1",
        "basis": "cold_start",
        "difficulty": difficulty, "plan_steps": plan_steps,
        "sample_size": 0,
        "confidence": "low",
        "estimated_input_tokens": int(input_guess),
        "estimated_output_tokens": int(output_guess),
    }


def plan_cost_quote(
    store: str | Path,
    *,
    steps: list[dict[str, Any]],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PlanCostQuote(§28): 每步 StepCostEstimate → 汇总 Expected/Range。

    step: {model: "deepseek/deepseek-v4-pro" 或 {provider, model}, estimated_input,
           estimated_output, billing_mode?}
    """
    step_quotes = []
    expected_total = 0.0
    low_total = 0.0
    high_total = 0.0
    unknown = []
    non_cash = []
    known_step_count = 0
    for index, step in enumerate(steps, start=1):
        provider = step.get("provider", "")
        model_id = step.get("model", "")
        price = model_price(store, model_id, provider=provider)
        mode = step.get("billing_mode") or billing_mode(provider, model_id)
        if mode in ("SUBSCRIPTION", "LOCAL_COMPUTE"):
            step_quotes.append({"step": f"S{index}", "model": model_id or "?",
                                "billing_mode": mode, "cost": None,
                                "reason": "non_cash_billing_mode"})
            non_cash.append(f"S{index}")
            continue
        if price is None:
            step_quotes.append({"step": f"S{index}", "model": model_id or "?",
                                "billing_mode": mode, "cost": None,
                                "reason": "no_tariff"})
            unknown.append(f"S{index}")
            continue
        input_tokens = float(step.get("estimated_input", 2000))
        output_tokens = float(step.get("estimated_output", 1000))
        expected = (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
        # range: ±50%(启发式)
        low, high = expected * 0.5, expected * 1.5
        expected_total += expected
        low_total += low
        high_total += high
        known_step_count += 1
        step_quotes.append({
            "step": f"S{index}", "model": model_id, "billing_mode": mode,
            "expected": round(expected, 4), "range": [round(low, 4), round(high, 4)],
        })
    complete = not unknown and not non_cash
    has_known_cash = known_step_count > 0
    return {
        "schema_version": "icle-plan-cost-quote/v0.1",
        "steps": step_quotes,
        # A mixed plan exposes the known API subtotal, but labels it partial.
        # A wholly subscription/local plan stays null instead of becoming $0.
        "expected": round(expected_total, 4) if not unknown and has_known_cash else None,
        "range": [round(low_total, 4), round(high_total, 4)] if not unknown and has_known_cash else None,
        "cash_cost_status": "known" if complete and has_known_cash else "partial" if has_known_cash else "unknown",
        "unknown_steps": unknown,
        "non_cash_steps": non_cash,
        "created_at": _now(),
    }


# ---------------------------------------------------------------- persistence (actuals)


def _actuals_path(store: str | Path) -> Path:
    return Path(store) / "cost_actuals.jsonl"


def actual_cash_cost(actual: dict[str, Any]) -> float | None:
    """H1: 统一读取 CostActual 的现金成本。

    历史 v0.1 形状写 `cost`,生产路径(v0.2, tariff.py)写 `cash_cost`;
    两个字段同时存在时以 cash_cost 为准。杜绝消费方只认一个键导致
    成本聚合静默丢数据。返回 None 表示未知(不是 0)。
    """
    value = actual.get("cash_cost")
    if value is None:
        value = actual.get("cost")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def record_actual(store: str | Path, actual: dict[str, Any]) -> dict[str, Any]:
    """Append one CostActual under a file lock; existing evidence is immutable."""
    import fcntl

    store = Path(store)
    path = _actuals_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = store / "cost_actuals.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(actual, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return actual


def list_actuals(store: str | Path, *, limit: int = 100) -> list[dict[str, Any]]:
    store = Path(store)
    path = _actuals_path(store)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[-limit:]


def current_month_cost(
    store: str | Path,
    *,
    now: str | datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> float:
    """Settled API cash cost for the computer's current local calendar month."""
    period = local_month_key(now, local_timezone=local_timezone)
    total = 0.0
    for actual in list_actuals(store, limit=100_000):
        try:
            actual_period = local_month_key(
                actual.get("created_at"), local_timezone=local_timezone
            )
        except LocalTimeError:
            continue
        if actual_period != period:
            continue
        cost = actual_cash_cost(actual)
        if cost is not None:
            total += cost
    return round(total, 6)


def cost_budget_status(store: str | Path) -> dict[str, Any]:
    from .settings import get_system_settings

    settings = get_system_settings(store)
    budget = settings.get("monthly_cost_budget_usd")
    spent = current_month_cost(store)
    ratio = round(spent / budget, 6) if budget else None
    warning_at = int(settings.get("cost_warning_percent") or 80) / 100
    return {
        "period": local_month_key(),
        "spent": spent,
        "budget": budget,
        "remaining": round(max(0.0, budget - spent), 6) if budget else None,
        "ratio": ratio,
        "warning": bool(ratio is not None and ratio >= warning_at),
        "exhausted": bool(ratio is not None and ratio >= 1),
        "warning_percent": int(settings.get("cost_warning_percent") or 80),
    }


def assert_cost_budget_available(store: str | Path) -> None:
    status = cost_budget_status(store)
    if status["exhausted"]:
        raise CostError(
            f"monthly LLM cost budget exhausted (${status['spent']:.2f} / ${status['budget']:.2f}); "
            "increase the budget in Settings before starting another metered request"
        )


# ---------------------------------------------------------------- COST-07/08: experience-driven quote
#
# 计划书 §17/§18:ExperiencePrediction(usage 分布)+ 当前 BaseTariff → CostQuote;
# 五维预测(Quality/Cost/Time/Intervention/Risk)供 Value Engine(COST-09)使用。

def cost_quote(
    store: str | Path,
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Experience-predicted usage × CURRENT tariff → Expected/Low/High.

    Usage distribution comes from ExperienceModel (learns usage, not dollars);
    price comes from today's PricingSnapshot — so the quote stays valid even
    after price cuts (plan §11/§17).
    """
    from .experience import usage_prediction
    from .tariff import compute_from_snapshot, resolve_snapshot

    prediction = usage_prediction(store, profile=profile, route=route)
    usage = prediction["expected_usage"]

    def _cost_for(level: str) -> float | None:
        snapshot_usage = {
            "input_tokens": usage["input_tokens"].get(level, 0),
            "output_tokens": usage["output_tokens"].get(level, 0),
            "cache_read_tokens": usage["cache_read_tokens"].get(level, 0),
            "cache_write_tokens": usage["cache_write_tokens"].get(level, 0),
            "reasoning_tokens": usage["reasoning_tokens"].get(level, 0),
            "tool_calls": {},
        }
        try:
            snapshot, _ = resolve_snapshot(store, provider, model)
        except Exception:  # noqa: BLE001 — no tariff → unknown cost
            return None
        return round(compute_from_snapshot(snapshot, snapshot_usage)["total"], 6)

    low = _cost_for("p25")
    expected = _cost_for("p50")
    high = _cost_for("p75")
    if prediction["sample_count"] == 0:
        # 无经验 → 用量未知 → 成本 unknown(绝不报 $0,计划书 §24 纪律)
        low = expected = high = None
    return {
        "agent": route.get("agent"),
        "expected_cost": {"low": low, "expected": expected, "high": high},
        "currency": "USD",
        "duration_s": prediction["expected_duration_s"],
        "confidence": prediction["confidence"],
        "evidence": {"sample_count": prediction["sample_count"],
                     "effective_sample_size": prediction["effective_sample_size"]},
        "model_version": prediction.get("model_version"),
    }


def route_prediction(
    store: str | Path,
    *,
    profile: dict[str, Any],
    route: dict[str, Any],
    provider: str,
    model: str,
) -> dict[str, Any]:
    """五维预测(COST-09 输入):Quality/Cost/Time/Intervention(+ evidence)。

    Quality 与 Intervention 来自 ExperienceModel 的 outcome 统计。
    """
    from .experience import outcome_prediction

    quote = cost_quote(store, profile=profile, route=route, provider=provider, model=model)
    outcome = outcome_prediction(store, profile=profile, route=route)
    return {
        "agent": route.get("agent"),
        "model": model,
        "provider": provider,
        "quality": outcome.get("accept_without_rework"),
        "cost": quote["expected_cost"],
        "time_s": quote["duration_s"],
        "intervention": outcome.get("expected_intervention"),
        "confidence": quote["confidence"],
        "evidence": quote["evidence"],
        "model_version": quote.get("model_version"),
    }
