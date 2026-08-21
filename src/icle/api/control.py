"""Control Center API (v0.4 P15 / Batch 6): overview aggregation + cost tables.

Thin HTTP layer: aggregates existing core modules (discovery / task / cost /
recommendation). No business logic here.
"""

from __future__ import annotations

from datetime import date, timedelta, tzinfo
from pathlib import Path

from fastapi import APIRouter, Request

from ..cost import actual_cash_cost, cost_budget_status, list_actuals
from ..discovery import doctor_agents
from ..localtime import LocalTimeError, local_date
from ..task import is_open_task, list_tasks

router = APIRouter()


def _store(request: Request) -> Path:
    return Path(request.app.state.store)


def _calendar_cost_totals(
    actuals: list[dict],
    *,
    today: date | None = None,
    local_timezone: tzinfo | None = None,
) -> tuple[float, float]:
    """Known cash cost grouped by the user's local calendar boundaries."""
    today = today or local_date(local_timezone=local_timezone)
    week_ago = today - timedelta(days=7)
    today_total = 0.0
    week_total = 0.0
    for actual in actuals:
        cost = actual_cash_cost(actual)
        if cost is None:
            continue
        try:
            created = local_date(actual["created_at"], local_timezone=local_timezone)
        except (KeyError, LocalTimeError):
            continue
        if created == today:
            today_total += cost
        if created >= week_ago:
            week_total += cost
    return today_total, week_total


@router.get("/overview")
def overview(request: Request) -> dict:
    """Home Control Center aggregates (§43): Local Agents / Active / Needs You /
    Cost / Recommendations."""
    store = _store(request)
    capture_store = request.app.state.capture_store

    # Local agents summary
    doctor = doctor_agents(store, capture_store=capture_store)
    agents = doctor["summary"]

    # Keep the home-page "Active" projection consistent with Task Studio:
    # every unfinished, unarchived Task belongs to the current workspace.
    # "Needs You" is an overlapping action queue, not a lifecycle partition.
    tasks = list_tasks(store)
    active = [task for task in tasks if is_open_task(task)]
    needs_you = [
        task for task in tasks
        if is_open_task(task) and task["status"] in ("review", "needs_input", "planned")
    ]

    # Cost today / this week (from cost_actuals timestamps)
    actuals = list_actuals(store, limit=1000)
    today_total, week_total = _calendar_cost_totals(actuals)

    # Recommendations (top routes from recommendation engine)
    from ..discovery import scan_agents
    from ..recommendation import recommend_routes

    try:
        linked = [a["agent_type"] for a in scan_agents(store) if a["status"] == "linked"]
        rec = recommend_routes(
            store, title="", description="", difficulty="D3", risk="R1",
            available=linked or ["kimi", "hermes"], include_cold_start=True,
        )
        top = rec["routes"][:3]
    except Exception:  # noqa: BLE001 — recommendations are best-effort
        top = []

    return {
        "local_agents": agents,
        "active": active,
        "needs_you": needs_you,
        "cost": {"today": round(today_total, 4), "this_week": round(week_total, 4)},
        "recommendations": top,
    }


def _legacy_unknown_actuals(store: Path, actuals: list[dict]) -> list[dict]:
    """Project old run-step null costs for visibility without rewriting evidence."""
    recorded = {(item.get("run_id"), item.get("step_id")) for item in actuals}
    projected: list[dict] = []
    tasks_dir = store / "tasks"
    if not tasks_dir.is_dir():
        return projected
    import json

    for task_path in tasks_dir.glob("t-*.json"):
        try:
            task = json.loads(task_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        profile = task.get("profile") or {}
        for step_path in tasks_dir.joinpath(task_path.stem, "runs").glob("*/steps/S*.json"):
            try:
                step = json.loads(step_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = (step.get("run_id"), step.get("step_id"))
            step_cost = step.get("cost")
            if key in recorded or (isinstance(step_cost, dict) and actual_cash_cost(step_cost) is not None):
                continue
            projected.append({
                "created_at": step.get("created_at") or task.get("updated_at"),
                "scope": "legacy_task_execution",
                "operation": "execute_step",
                "provider": step.get("agent") or "unknown",
                "model": "unknown",
                "agent": step.get("agent") or "unknown",
                "project_id": task.get("project_id") or "default",
                "task_id": task.get("task_id"),
                "task_type": profile.get("primary_type") or "UNKNOWN",
                "run_id": step.get("run_id"),
                "step_id": step.get("step_id"),
                "usage_source": "unknown",
                "cash_cost": None,
                "reason": "legacy_run_before_metering",
                "billing_mode": "UNKNOWN",
            })
    return projected


def _cost_bucket(actuals: list[dict], key_fn) -> dict[str, dict]:
    buckets: dict[str, dict] = {}
    for actual in actuals:
        key = str(key_fn(actual) or "Unknown")
        entry = buckets.setdefault(
            key,
            {"count": 0, "known_count": 0, "unknown_count": 0, "total": 0.0, "duration_ms": 0},
        )
        entry["count"] += 1
        duration = actual.get("duration_ms")
        if (
            actual.get("operation") != "report_consumption"
            and isinstance(duration, (int, float))
            and duration > 0
        ):
            entry["duration_ms"] = int(entry["duration_ms"] + duration)
        cost = actual_cash_cost(actual)
        if cost is None:
            entry["unknown_count"] += 1
        else:
            entry["known_count"] += 1
            entry["total"] = round(entry["total"] + cost, 6)
    return buckets


@router.get("/cost")
def cost_center(request: Request) -> dict:
    """Plan §46: actual spend, attribution, forecast accuracy, and budget."""
    store = _store(request)
    actuals = list_actuals(store, limit=100_000)
    legacy_unknown = _legacy_unknown_actuals(store, actuals)
    actuals = actuals + legacy_unknown
    known = [actual_cash_cost(item) for item in actuals]
    comparisons = []
    for actual in actuals:
        estimated = actual.get("estimated_cost")
        cost = actual_cash_cost(actual)
        if not isinstance(estimated, (int, float)) or cost is None:
            continue
        comparisons.append({
            "task_id": actual.get("task_id"),
            "run_id": actual.get("run_id"),
            "step_id": actual.get("step_id"),
            "estimated": estimated,
            "actual": cost,
            "error": actual.get("prediction_error"),
        })
    total_spend = round(sum(value for value in known if value is not None), 6)
    total_duration_ms = sum(
        int(item.get("duration_ms") or 0)
        for item in actuals
        if item.get("operation") != "report_consumption"
        and isinstance(item.get("duration_ms"), (int, float))
    )
    by_agent = _cost_bucket(
        actuals,
        lambda item: item.get("agent") or ("AgentTogether Intelligence" if item.get("scope") == "intelligence" else item.get("provider")),
    )
    by_model = _cost_bucket(actuals, lambda item: f"{item.get('provider', '?')}/{item.get('model', '?')}")
    by_project = _cost_bucket(actuals, lambda item: item.get("project_id") or "Unattributed")
    by_task_type = _cost_bucket(actuals, lambda item: item.get("task_type") or "Unattributed")
    by_operation = _cost_bucket(actuals, lambda item: item.get("operation") or "Unattributed")
    return {
        "status": "active",
        "by_agent": by_agent,
        "by_model": by_model,
        "by_project": by_project,
        "by_task_type": by_task_type,
        "by_operation": by_operation,
        "total_actuals": len(actuals),
        "known_cost_count": sum(1 for value in known if value is not None),
        "unknown_cost_count": sum(1 for value in known if value is None),
        "total_spend": total_spend,
        "total_duration_ms": total_duration_ms,
        "estimated_vs_actual": {
            "count": len(comparisons),
            "estimated_total": round(sum(item["estimated"] for item in comparisons), 6),
            "actual_total": round(sum(item["actual"] for item in comparisons), 6),
            "records": comparisons[-20:],
        },
        "budget": cost_budget_status(store),
        "recent": [
            {
                "created_at": item.get("created_at"),
                "scope": item.get("scope") or "legacy",
                "operation": item.get("operation"),
                "provider": item.get("provider"),
                "model": item.get("model"),
                "project_id": item.get("project_id"),
                "task_id": item.get("task_id"),
                "usage_source": item.get("usage_source"),
                "cash_cost": actual_cash_cost(item),
                "reason": item.get("reason"),
                "billing_mode": item.get("billing_mode"),
                "duration_ms": item.get("duration_ms"),
                "duration_source": item.get("duration_source"),
            }
            for item in actuals[-20:][::-1]
        ],
    }
