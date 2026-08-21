"""Baseline-model probe: the only approved way to obtain A locally.

A is "what the model can do"; J is "what this agent achieved". Public A only
exists for models present in the vendored dataset, so most local CLIs have no A
and therefore no C. The engine's sanctioned alternative is a same-TaskSpec probe:
run the *same model the agent wraps*, bare over a provider API, on the *same*
frozen TaskSpecs, and calibrate A from that baseline's J.

Two constraints drive the whole design:

- The engine overlays probed A only onto a subject whose model matches the
  baseline's, so the probe must target the agent's own model. C then reads as
  "what this harness adds over its own bare model", which is the honest claim.
- A probe cell needs >= 2 observed tasks on an axis (contract) and >= 2 for the
  bootstrap stderr. One task can never produce A, no matter how it scores.

The probe fabricates nothing: no model match, no probe; no form, no metrics.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .evolution import baseline_subject_id_for, record_baseline_measurement
from .report import apply_measured_consumption, consolidation, parse_report, usage_from_report

PROBE_SCHEMA = "icle-baseline-probe/v0.1"


class BaselineError(ValueError):
    pass


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def baseline_target(store: str | Path, agent: str) -> dict[str, Any]:
    """Find a configured provider model identical to the model the agent wraps.

    Returns a dict with `available` plus the reason when it is False, so the UI
    can explain why an agent cannot be probed instead of silently offering it.
    """
    from .external_evaluation import resolve_model_identity
    from .provider import list_providers

    store = Path(store)
    if "/" in agent:
        return {
            "available": False,
            "reason": "agent_is_already_a_bare_model",
            "agent": agent,
        }
    identity = resolve_model_identity(store, agent)
    model = str(identity.get("model") or "")
    if not model or model in {"unknown", "captured-session"}:
        return {
            "available": False,
            "reason": "agent_model_unknown",
            "agent": agent,
            "hint": "bind the agent to a concrete model before probing A",
        }
    for config in list_providers(store):
        if config.get("status") != "connected":
            continue
        if any(item.get("id") == model for item in config.get("models") or []):
            return {
                "available": True,
                "agent": agent,
                "model": model,
                "provider": identity.get("provider") or "",
                "provider_id": config["provider_id"],
                "execution_target": f"{config['provider_id']}/{model}",
                "baseline_subject_id": baseline_subject_id_for({
                    "agent": agent,
                    "provider_id": "",
                    "model": model,
                    "provider": identity.get("provider") or "",
                }),
            }
    return {
        "available": False,
        "reason": "no_connected_provider_serves_this_model",
        "agent": agent,
        "model": model,
    }


def run_baseline_probe(
    store: str | Path,
    task_id: str,
    *,
    agent: str,
    timeout_sec: float = 900,
    executor_factory: Any = None,
) -> dict[str, Any]:
    """Re-run one task's approved steps on the bare model and record A evidence.

    The probe starts from a fresh workspace copy of the project, so it sees the
    same starting state the agent saw rather than the agent's output.
    """
    from . import task as task_core

    store = Path(store)
    task = task_core.show_task(store, task_id)
    target = baseline_target(store, agent)
    if not target.get("available"):
        raise BaselineError(f"cannot probe {agent}: {target.get('reason')}")
    plan = task.get("plan") or {}
    if plan.get("status") != "approved":
        raise BaselineError(f"task {task_id} plan is not approved")
    steps = task_core._topo_order([dict(step) for step in plan.get("steps") or []])
    if not steps:
        raise BaselineError(f"task {task_id} has no steps to probe")

    if executor_factory is not None:
        executor = executor_factory(target["execution_target"])
    else:
        executor = task_core._provider_executor_for(store, target["execution_target"])
    if executor is None:
        raise BaselineError(f"no provider executor for {target['execution_target']}")

    run_id = "probe-" + uuid.uuid4().hex[:8]
    run_root = store / "tasks" / task_id / "runs" / run_id
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workspace_init = task_core._init_workspace(task.get("project_path") or "", workspace)

    records: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    overall = "completed"
    for step in steps:
        prompt = task_core._step_prompt(task, step, workspace)
        started = time.monotonic()
        from .usage import normalize_usage

        try:
            result = executor(workspace, prompt, timeout_sec)
            outcome, stdout, stderr, exit_code = result[0], result[1], result[2], result[3]
            usage = normalize_usage(result[4] if len(result) == 5 else {}, source_hint="exact_provider")
            usage_source = usage.get("usage_source", "exact_provider" if len(result) == 5 else "unknown")
        except Exception as exc:  # noqa: BLE001 — record the probe failure
            outcome, stdout, stderr, exit_code = "failed", "", str(exc)[:200], -1
            usage = normalize_usage({}, source_hint="unknown")
            usage_source = "unknown"
        duration_ms = round((time.monotonic() - started) * 1000)
        report, report_status = parse_report(stdout)
        report = apply_measured_consumption(report, usage=usage, duration_ms=duration_ms)
        reported = usage_from_report(report)
        if reported and not any(usage.get(key, 0) for key in ("input_tokens", "output_tokens")):
            usage = normalize_usage(reported, source_hint="agent_reported")
            usage_source = "agent_reported"
        cost_actual = task_core._settle_step_cost(
            store,
            agent=target["execution_target"],
            usage=usage,
            usage_source=usage_source,
            duration_ms=duration_ms,
            duration_source="measured",
            metadata={
                "scope": "baseline_probe",
                "operation": "probe_step",
                "agent": target["execution_target"],
                "project_id": task.get("project_id") or "default",
                "task_id": task_id,
                "task_type": (task.get("profile") or {}).get("primary_type") or "UNKNOWN",
                "run_id": run_id,
                "step_id": step.get("step_id"),
            },
        )
        records.append({
            "step_id": step["step_id"],
            "agent": target["execution_target"],
            "status": outcome,
            "stdout_tail": stdout[-2000:],
            "stderr_tail": stderr[-1000:],
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "cost": cost_actual,
            "report": report,
            "report_status": report_status,
            "created_at": _now(),
        })
        entries.append({
            "task_id": task_id,
            "step_id": step["step_id"],
            "agent": target["execution_target"],
            "order": len(entries) + 1,
            "report_status": report_status,
            "report_origin": "baseline",
            "report": report,
        })
        if outcome != "completed":
            overall = "failed"
            break

    run = {
        "schema_version": PROBE_SCHEMA,
        "run_id": run_id,
        "task_id": task_id,
        "status": overall,
        "steps": records,
        "workspace_init": workspace_init,
        "created_at": _now(),
    }
    steps_dir = run_root / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        (steps_dir / f"{record['step_id']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (run_root / "probe.json").write_text(
        json.dumps({**run, "target": target}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # The probe has no user to consolidate for: the finishing step's form wins.
    from .report import finishing_consolidation

    report = finishing_consolidation(entries) if entries else consolidation(
        [], report=None, source="none", reason="no_probe_step"
    )
    if overall != "completed":
        # A transport-level failure (bad request, timeout, no credit) says nothing
        # about the model, so it must not be recorded as the subject failing the
        # task — that would drag A down and inflate C. A model that does the work
        # badly still answers, and its own form reports the shortfall.
        measurement = {
            "status": "skipped",
            "reason": "probe_execution_failed",
            "detail": next(
                (r["stderr_tail"] for r in reversed(records) if r["stderr_tail"]), ""
            )[:200],
        }
    else:
        measurement = record_baseline_measurement(
            store,
            task=task,
            run=run,
            agent=agent,
            report=report,
            status=overall,
        )
    return {
        "schema_version": PROBE_SCHEMA,
        "task_id": task_id,
        "agent": agent,
        "target": target,
        "run_id": run_id,
        "status": overall,
        "report": report,
        "measurement": measurement,
    }


def probe_status(store: str | Path, agent: str) -> dict[str, Any]:
    """How far this install is from having a usable A for one agent."""
    from .evolution import LOCAL_PROBE_CONTRACT, _root

    store = Path(store)
    target = baseline_target(store, agent)
    subject_id = target.get("baseline_subject_id")
    bundles_dir = _root(store) / "runs" / "bundles"
    probed: set[str] = set()
    if subject_id and bundles_dir.is_dir():
        for path in bundles_dir.glob("*.json"):
            try:
                bundle = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if bundle.get("subject_id") == subject_id and bundle.get("outcome", {}).get("metrics"):
                probed.add(str(bundle.get("task_spec_id")))
    required = int(LOCAL_PROBE_CONTRACT["min_observed_tasks"])
    return {
        "agent": agent,
        "target": target,
        "baseline_subject_id": subject_id,
        "probed_task_count": len(probed),
        "required_task_count": required,
        "ready": len(probed) >= required,
        "probed_task_ids": sorted(probed),
    }
