"""ICLE adapter for the vendored A+B+C measurement engine.

ICLE remains the runner and control plane. This module only translates
Task / Run / accept evidence into frozen TaskSpecs and RunBundles, then
asks `experience_evaluation` for J and C. It does not emit a total score,
does not rank Agents, and does not change Router inputs.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .report import METRIC_CONTRACT, METRIC_IDS, REPORT_CONTRACT_ID, report_metrics

VENDOR_ROOT = Path(__file__).resolve().parents[2] / "vendor" / "evaluation-engine-with-evolution"
VENDOR_SRC = VENDOR_ROOT / "src"
ENGINE_COMMIT = "9d7f11bc77e3a87163db394c3099b597337ea9ef"

Q_CONTRACT = "icle-taskprofile-to-livebench-q/v0.1"
# 2.0.0 scores the AgentTaskReport metrics only. user_accept left the scored set
# so a baseline-model probe can be scored on the same TaskSpec (see report.py).
# Frozen specs keep the revision they were written with, so old bundles match.
TASK_SPEC_REVISION = "2.0.0"
MEASUREMENT_SCHEMA = "icle-abjc-measurement/v0.1"
RUN_BUNDLE_SCHEMA = "experience-evaluation-run-bundle/v0.1"
CANONICAL_AXES = (
    "reasoning",
    "coding",
    "agentic_coding",
    "mathematics",
    "data_analysis",
    "language",
    "instruction_following",
)
FORBIDDEN_TOTAL_KEYS = {
    "total",
    "total_score",
    "overall",
    "overall_score",
    "rank",
    "leaderboard_score",
}
# Exact Agent ID → engine stable family. Unknown Agents omit harness and
# receive the generic B0 prior. No brand-prefix guessing.
HARNESS_BY_AGENT = {
    "claude": "claude-code",
    "codex": "codex",
    "hermes": "hermes-agent",
    "opencode": "opencode",
    "pi": "pi",
}
MARK_METRIC = {"accept": 1.0, "edit": 0.8, "reject": 0.0, "redo": 0.0}

_ENGINE_READY = False


class EvolutionError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _ensure_engine() -> None:
    global _ENGINE_READY
    if _ENGINE_READY:
        return
    if not (VENDOR_SRC / "experience_evaluation" / "__init__.py").is_file():
        raise EvolutionError("vendored experience_evaluation package is missing")
    src = str(VENDOR_SRC)
    if src not in sys.path:
        sys.path.insert(0, src)
    _ENGINE_READY = True


def _root(store: str | Path) -> Path:
    return Path(store) / "evolution"


def _safe_subject(subject_id: str) -> str:
    return subject_id.replace("/", "_").replace("|", "__")


def capability_loadings_for(profile: dict[str, Any] | None) -> dict[str, float]:
    """Frozen Q mask. Unmapped types stay empty so J remains insufficient."""
    profile = profile or {}
    primary = str(profile.get("primary_type") or "").upper()
    subtype = str(profile.get("subtype") or "").lower()
    axes: tuple[str, ...] = ()
    if subtype in {"refactor", "refactoring"}:
        axes = ("coding",)
    elif primary == "CODING":
        axes = ("coding", "agentic_coding")
    elif primary in {"SYSTEM_OPERATION", "SYSTEM_OPERATIONS"}:
        axes = ("agentic_coding",)
    elif primary in {"RESEARCH", "ANALYSIS", "DATA"}:
        axes = ("data_analysis",)
    elif primary == "PLANNING":
        axes = ("reasoning", "instruction_following")
    elif primary in {"WRITING", "DOCUMENTATION"}:
        axes = ("language",)
    return {axis: 1.0 for axis in axes}


def subject_id_for(route: dict[str, Any]) -> str:
    agent = str(route.get("agent") or route.get("agent_id") or "")
    provider_id = str(route.get("provider_id") or "")
    model = str(route.get("model") or "")
    if not agent:
        raise EvolutionError("measurement subject requires an agent")
    return f"{agent}|{provider_id}|{model}"


def subject_identity(route: dict[str, Any]) -> dict[str, Any]:
    agent = str(route.get("agent") or route.get("agent_id") or "")
    identity: dict[str, Any] = {
        "subject_id": subject_id_for(route),
        "role": "agent",
        "model_revision": route.get("model") or None,
        "model": route.get("model") or None,
        "provider": route.get("provider") or None,
        "icle_agent": agent,
        "icle_provider_id": route.get("provider_id") or None,
    }
    if "/" in agent or route.get("execution_mode") == "provider_api":
        identity["harness"] = "generic-agent-shell"
    else:
        harness = HARNESS_BY_AGENT.get(agent)
        if harness:
            identity["harness"] = harness
    return {key: value for key, value in identity.items() if value not in (None, "")}


# ---------------------------------------------------------------- local A probe
#
# The engine only overlays locally probed A onto a subject whose model matches the
# baseline's (matrix_pipeline `local_probe_model_mismatch`). So the baseline is the
# SAME model as the agent, run bare through a provider API instead of the agent's
# harness. That makes C read as "what this harness adds over its own bare model".

BASELINE_ROLE = "baseline_model"
BASELINE_PREFIX = "baseline:"
LOCAL_PROBE_CONTRACT = {
    "contract_id": "icle-local-probe-a/v0.1",
    # The engine needs >= 2 observed tasks per axis before it trusts a probe cell,
    # and its bootstrap needs >= 2 to produce a stderr at all.
    "min_observed_tasks": 2,
    "evidence_tier": "local_probe_same_taskspec",
}


def baseline_route(route: dict[str, Any]) -> dict[str, Any]:
    """The bare-model twin of an agent route: same model, no agent harness."""
    agent = str(route.get("agent") or route.get("agent_id") or "")
    if not agent:
        raise EvolutionError("baseline route requires an agent")
    return {
        **route,
        "agent": f"{BASELINE_PREFIX}{agent}",
        "agent_id": f"{BASELINE_PREFIX}{agent}",
        "execution_mode": "provider_api",
    }


def baseline_identity(route: dict[str, Any]) -> dict[str, Any]:
    """Baseline subject identity: distinct subject, identical model/provider.

    model / provider must stay byte-identical to the agent's, because that pair
    is what the engine matches on when it decides to overlay the probed A.
    """
    identity = subject_identity(baseline_route(route))
    identity["role"] = BASELINE_ROLE
    identity["harness"] = "generic-agent-shell"
    return identity


def baseline_subject_id_for(route: dict[str, Any]) -> str:
    return subject_id_for(baseline_route(route))


def _route_from_accept(*, agent: str, store: Path, task: dict[str, Any]) -> dict[str, Any]:
    from .external_evaluation import resolve_model_identity

    metadata = {"agent_type": "provider-model"} if "/" in agent else None
    identity = resolve_model_identity(store, agent, metadata=metadata)
    if "/" in agent:
        provider_id, _, model = agent.partition("/")
        return {
            "agent": provider_id,
            "agent_id": agent,
            "model": model,
            "provider": identity.get("provider") or provider_id,
            "provider_id": provider_id,
            "execution_mode": "provider_api",
        }
    return {
        "agent": agent,
        "agent_id": agent,
        "model": identity.get("model") or "",
        "provider": identity.get("provider") or "direct_cli",
        "provider_id": "",
        "execution_mode": "direct_cli",
    }


def freeze_task_spec(store: str | Path, task: dict[str, Any]) -> dict[str, Any] | None:
    """Freeze the first TaskSpec. Later profile edits do not rewrite it."""
    loadings = capability_loadings_for(task.get("profile"))
    if not loadings:
        return None
    store = Path(store)
    path = _root(store) / "taskspecs" / f"{task['task_id']}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    spec = {
        "schema_version": "icle-evolution-task-spec/v0.1",
        "q_contract_id": Q_CONTRACT,
        "task_id": task["task_id"],
        "task_revision": TASK_SPEC_REVISION,
        "task_family": str((task.get("profile") or {}).get("primary_type") or "OTHER").lower(),
        "weight": 1.0,
        "capability_loadings": loadings,
        # Declared metrics = the fixed evaluation form. The engine rejects a run
        # whose metrics are not declared here, so the two must stay in step.
        "metrics": [dict(metric) for metric in METRIC_CONTRACT],
        "report_contract_id": REPORT_CONTRACT_ID,
        "publication_scope": "local_controlled",
        "not_claimed": [
            "livebench common-item link",
            "public capability score",
            "causal experience gain",
        ],
        "source_task": {
            "title": task.get("title") or "",
            "primary_type": (task.get("profile") or {}).get("primary_type"),
            "subtype": (task.get("profile") or {}).get("subtype"),
        },
        "frozen_at": _now(),
    }
    _atomic_write(path, json.dumps(spec, ensure_ascii=False, indent=2) + "\n")
    return spec


def _duration_s(run: dict[str, Any] | None) -> float | None:
    if not run:
        return None
    total = 0
    found = False
    for step in run.get("steps") or []:
        if step.get("duration_ms") is not None:
            total += int(step["duration_ms"])
            found = True
    if found:
        return round(total / 1000.0, 3)
    if run.get("duration_ms") is not None:
        return round(int(run["duration_ms"]) / 1000.0, 3)
    return None


def build_run_bundle(
    *,
    task: dict[str, Any],
    run: dict[str, Any] | None,
    route: dict[str, Any],
    mark: str,
    report: dict[str, Any] | None = None,
    task_revision: str | None = None,
) -> dict[str, Any]:
    _ensure_engine()
    from experience_evaluation.run_bundle import compute_content_hash, validate_run_bundle

    mark = str(mark or "accept")
    status = "completed" if mark in {"accept", "edit"} else "subject_failure"
    started = (run or {}).get("started_at") or (run or {}).get("created_at") or _now()
    ended = (run or {}).get("ended_at") or (run or {}).get("completed_at") or _now()
    run_id = str((run or {}).get("run_id") or f"accept-{task['task_id']}")
    # Only declared metrics may enter outcome.metrics — an undeclared field makes
    # the engine reject the whole run, which is what left J unscored before.
    consolidated = (report or {}).get("report") if isinstance(report, dict) else None
    metrics: dict[str, Any] = dict(report_metrics(consolidated))
    exit_codes = [
        step.get("exit_code")
        for step in (run or {}).get("steps") or []
        if step.get("exit_code") is not None
    ]
    bundle = {
        "schema_version": RUN_BUNDLE_SCHEMA,
        "run_id": run_id,
        "task_spec_id": task["task_id"],
        "task_revision": task_revision or TASK_SPEC_REVISION,
        "subject_id": subject_id_for(route),
        "started_at": started,
        "ended_at": ended,
        "status": status,
        "agent_revision_id": None,
        "events": [],
        "notes": "icle_user_accept_local_controlled",
        "outcome": {
            "metrics": metrics,
            "verifier_id": "icle-user-accept/v0.1",
            "publication_scope": "local_controlled",
            # Diagnostics are recorded but never scored. user_accept lives here:
            # the accept gates the evidence, it does not grade the work.
            "diagnostics": {
                "mark": mark,
                "user_accept": MARK_METRIC.get(mark, 0.0),
                "duration_s": _duration_s(run),
                "exit_code": int(exit_codes[-1]) if exit_codes else None,
                "report_contract_id": REPORT_CONTRACT_ID,
                "report_source": (report or {}).get("source") if isinstance(report, dict) else None,
                "report_status": (report or {}).get("report_status") if isinstance(report, dict) else "missing",
            },
        },
        "runner": {
            "runner_id": "icle-task-runner",
            "revision": "icle-evolution-v0.1",
            "engine_commit": ENGINE_COMMIT,
        },
    }
    bundle["content_hash"] = compute_content_hash(bundle)
    return validate_run_bundle(bundle)


def _probe_config(store: Path, results: list[dict[str, Any]]) -> dict[str, Any]:
    """Enable the local A probe only when one baseline actually has runs.

    The engine accepts a single `baseline_subject_id` per pipeline run, and it
    raises when the probe is enabled without baseline results. Both conditions
    are checked here so a probe-less install keeps working untouched.
    """
    baselines = [
        identity for identity in _load_identities(store)
        if identity.get("role") == BASELINE_ROLE and identity.get("subject_id")
    ]
    if len(baselines) != 1:
        return {"enabled": False}
    subject_id = baselines[0]["subject_id"]
    if not any(row.get("subject_id") == subject_id for row in results):
        return {"enabled": False}
    return {
        "enabled": True,
        "baseline_subject_id": subject_id,
        "contract": dict(LOCAL_PROBE_CONTRACT),
    }


def _pipeline_config(probe: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "generator": {
            "dimensions": list(CANONICAL_AXES),
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "source_scale_id": "normalized_0_1",
            "canonical_scale_id": "canonical_logit/v0.1",
            "attempt_reducer": "mean",
            "subject_failure_score": 0.0,
            "bootstrap_replicates": 80,
            "bootstrap_seed": 20260821,
            # J publish gate. The engine publishes a J cell when a value exists,
            # coverage clears this floor, the scope is not synthetic, and
            # comparability is verified. Coverage is the only knob ICLE owns, and
            # 0.6 silently withheld J whenever most tasks on an axis had no form.
            # Local J is evidence about this install, not a public claim, so any
            # observation publishes and coverage is reported for the reader.
            "min_coverage": 0.0,
            "comparability_verified": True,
            "comparison_contract_id": "icle-local-accept-v0.1",
            "publication_scope": "local_controlled",
            "evidence_origin": "icle_user_accept",
        },
        "calibration_contract": {
            "contract_id": "icle-local-usage-c/v0.1",
            "verified": True,
            "scale_id": "canonical_logit/v0.1",
            "independence_assumption": True,
            "publication_scope": "local_controlled",
            "evidence_origin": "icle_user_accept",
        },
        "local_probe_a": probe or {"enabled": False},
    }


def _c_state_config() -> dict[str, Any]:
    path = VENDOR_ROOT / "config" / "c_state_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _portrait_policy() -> dict[str, Any]:
    path = VENDOR_ROOT / "config" / "profile_publication_policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    policy["not_claimed"] = list(dict.fromkeys([
        *policy.get("not_claimed", []),
        "user accept as public verifier",
        "livebench common-item link",
    ]))
    return policy


def _list_json(directory: Path) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    rows = []
    for path in sorted(directory.glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _remember_identity(store: Path, identity: dict[str, Any]) -> None:
    path = _root(store) / "identities.jsonl"
    existing = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                existing.append(json.loads(line))
    subject_id = identity["subject_id"]
    if any(row.get("subject_id") == subject_id for row in existing):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(identity, ensure_ascii=False, sort_keys=True) + "\n")


def _load_identities(store: Path) -> list[dict[str, Any]]:
    path = _root(store) / "identities.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _compact_lookup(lookup: dict[str, Any] | None) -> dict[str, Any]:
    lookup = lookup or {}
    cells = []
    for cell in lookup.get("cells") or []:
        cells.append({
            "dimension_id": cell.get("dimension_id"),
            "value": cell.get("canonical_value", cell.get("value")),
            "stderr": cell.get("canonical_stderr", cell.get("stderr")),
            "status": cell.get("status") or lookup.get("status"),
            "scale_id": cell.get("canonical_scale_id") or cell.get("scale_id"),
        })
    return {
        "status": lookup.get("status") or "unavailable",
        "reasons": list(lookup.get("reasons") or []),
        "cells": cells,
    }


def _j_cells_for_subject(j_bundle: dict[str, Any], subject_id: str) -> dict[str, Any]:
    cells = []
    for cell in j_bundle.get("evaluation_matrix") or []:
        if cell.get("subject_id") != subject_id:
            continue
        cells.append({
            "dimension_id": cell.get("dimension_id") or cell.get("dimension"),
            "value": cell.get("canonical_value", cell.get("value")),
            "stderr": cell.get("canonical_stderr", cell.get("stderr")),
            "status": cell.get("status"),
            "reason": cell.get("reason") or cell.get("unavailable_reason"),
            "scale_id": cell.get("canonical_scale_id") or cell.get("scale_id"),
        })
    observed = sum(1 for cell in cells if cell.get("value") is not None)
    return {
        "status": "observed" if observed else "unavailable",
        "reasons": [],
        "cells": cells,
    }


def _c_lookup(c_matrix: list[dict[str, Any]], subject_id: str) -> dict[str, Any]:
    cells = [cell for cell in c_matrix if cell.get("subject_id") == subject_id]
    available = sum(1 for cell in cells if cell.get("status") == "available")
    return {
        "status": "available" if available else "unavailable",
        "reasons": sorted({
            reason
            for cell in cells
            for reason in (cell.get("reasons") or ([cell.get("reason")] if cell.get("reason") else []))
            if reason
        }),
        "cells": [
            {
                "dimension_id": cell.get("dimension_id"),
                "value": cell.get("value"),
                "stderr": cell.get("stderr"),
                "status": cell.get("status"),
                "reasons": cell.get("reasons") or [],
                "scale_id": cell.get("scale_id"),
            }
            for cell in cells
        ],
    }


def _run_pipeline(store: Path) -> dict[str, Any]:
    _ensure_engine()
    from experience_evaluation.c_state import CStateStore
    from experience_evaluation.matrix_pipeline import BuiltinDatasetRegistry, run_matrix_pipeline
    from experience_evaluation.run_bundle import RunBundleStore

    specs = _list_json(_root(store) / "taskspecs")
    identities = _load_identities(store)
    if not specs or not identities:
        return {"status": "skipped", "reason": "missing_specs_or_identities"}
    bundles = RunBundleStore(_root(store) / "runs")
    results = bundles.execution_results()
    if not results:
        return {"status": "skipped", "reason": "no_run_bundles"}
    registry = BuiltinDatasetRegistry(VENDOR_ROOT / "data" / "matrices")
    probe = _probe_config(store, results)

    def _pipeline(probe_config: dict[str, Any]) -> dict[str, Any]:
        return run_matrix_pipeline(
            registry=registry,
            task_specs=specs,
            execution_results=results,
            subject_identities=identities,
            pipeline_config=_pipeline_config(probe_config),
        )

    probe_error = None
    try:
        pipeline = _pipeline(probe)
    except Exception as exc:  # noqa: BLE001 — a probe miss must not lose the J update
        if not probe.get("enabled"):
            raise
        probe_error = f"{type(exc).__name__}: {exc}"[:300]
        pipeline = _pipeline({"enabled": False})
    update = CStateStore(_root(store) / "c-state", _c_state_config()).update(
        pipeline["c_matrix"],
        observed_at=_now(),
        source_bundle_id="icle-accept",
    )
    snapshots = {}
    for identity in identities:
        subject_id = identity["subject_id"]
        snapshots[subject_id] = {
            "schema_version": "icle-abjc-layer-snapshot/v0.1",
            "subject_id": subject_id,
            "a": _compact_lookup(pipeline["a_lookups"].get(subject_id)),
            "b": _compact_lookup(pipeline["b_lookups"].get(subject_id)),
            "j": _j_cells_for_subject(pipeline["j_bundle"], subject_id),
            "c": _c_lookup(pipeline["c_matrix"], subject_id),
            "identity_resolution": next(
                (
                    row for row in pipeline.get("identity_resolution") or []
                    if row.get("subject_id") == subject_id
                ),
                None,
            ),
            "updated_at": _now(),
        }
        _atomic_write(
            _root(store) / "layers" / f"{_safe_subject(subject_id)}.json",
            json.dumps(snapshots[subject_id], ensure_ascii=False, indent=2) + "\n",
        )
    probe_report = pipeline.get("local_probe_a")
    return {
        "status": "updated",
        "summary": pipeline.get("summary"),
        "local_probe_a": {
            "enabled": bool(probe.get("enabled")),
            "baseline_subject_id": probe.get("baseline_subject_id"),
            "error": probe_error,
            "approved_dimensions": (probe_report or {}).get("approved_dimensions") or [],
            "rejected_dimensions": (probe_report or {}).get("rejected_dimensions") or [],
        },
        "c_state_update": {
            "updated": update.get("updated"),
            "rejected": update.get("rejected"),
            "duplicates": update.get("duplicates"),
        },
    }


def record_accept_measurement(
    store: str | Path,
    *,
    task: dict[str, Any],
    run: dict[str, Any] | None,
    agent: str,
    mark: str = "accept",
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Export one accept as a local_controlled RunBundle and refresh C.

    Failures are raised to the caller; accept_task swallows them so a
    measurement miss never blocks the user accept path.
    """
    store = Path(store)
    spec = freeze_task_spec(store, task)
    if spec is None:
        return {"status": "skipped", "reason": "q_unmapped"}
    if run is None:
        return {"status": "skipped", "reason": "no_run"}
    _ensure_engine()
    from experience_evaluation.run_bundle import RunBundleStore

    route = _route_from_accept(agent=agent, store=store, task=task)
    identity = subject_identity(route)
    _remember_identity(store, identity)
    bundle = build_run_bundle(
        task=task,
        run=run,
        route=route,
        mark=mark,
        report=report,
        # Follow the frozen spec: a spec written before the form contract keeps
        # its own revision, so its bundles still match.
        task_revision=str(spec.get("task_revision") or TASK_SPEC_REVISION),
    )
    ingest = RunBundleStore(_root(store) / "runs").ingest([bundle])
    pipeline = _run_pipeline(store)
    return {
        "status": "recorded",
        "subject_id": identity["subject_id"],
        "task_spec_id": spec["task_id"],
        "report_status": (report or {}).get("report_status") if isinstance(report, dict) else "missing",
        "metrics": sorted(bundle["outcome"]["metrics"]),
        "ingest": ingest,
        "pipeline": pipeline,
    }


def rebuild_c_state(
    store: str | Path, *, reason: str, confirm: bool = False
) -> dict[str, Any]:
    """Retire the C belief ledger and rebuild it from current evidence.

    C-state is a hash-chained append-only ledger, so a bad observation cannot be
    removed — the chain would no longer verify. Recovery is therefore: archive
    the whole ledger (it stays readable as audit history), start a fresh chain,
    and replay the current run bundles into it.

    This is a maintenance operation for data incidents (e.g. a probe that failed
    for infrastructure reasons and was recorded as the subject failing). It
    cannot target a single subject: one chain covers every subject.
    """
    store = Path(store)
    if not reason.strip():
        raise EvolutionError("rebuilding C-state requires a reason")
    if not confirm:
        raise EvolutionError("rebuilding C-state requires confirm=True")
    c_root = _root(store) / "c-state"
    archive = None
    if c_root.is_dir():
        _ensure_engine()
        from experience_evaluation.c_state import CStateStore

        retired: dict[str, Any] = {}
        try:
            retired = CStateStore(c_root, _c_state_config(), read_only=True).verify_and_rebuild(
                rebuild_projection=False
            )
        except Exception as exc:  # noqa: BLE001 — an unverifiable chain is exactly what we retire
            retired = {"verify_error": f"{type(exc).__name__}: {exc}"[:200]}
        stamp = _now().replace(":", "").replace("-", "")
        archive_root = _root(store) / "c-state-archive" / stamp
        archive_root.parent.mkdir(parents=True, exist_ok=True)
        c_root.rename(archive_root)
        (archive_root / "retirement.json").write_text(
            json.dumps(
                {
                    "schema_version": "icle-c-state-retirement/v0.1",
                    "reason": reason,
                    "retired_at": _now(),
                    "retired_chain": retired,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        archive = {"path": str(archive_root), "retired_chain": retired}
    pipeline = _run_pipeline(store)
    return {
        "status": "rebuilt",
        "reason": reason,
        "archived": archive,
        "pipeline": pipeline,
    }


def cmd_rebuild_c_state(store: str | Path, *, reason: str, confirm: bool) -> int:
    """CLI for `icle measure rebuild-c-state`."""
    try:
        result = rebuild_c_state(store, reason=reason, confirm=confirm)
    except EvolutionError as exc:
        print(f"rebuild refused: {exc}")
        return 2
    archived = result.get("archived")
    if archived:
        print(f"retired chain -> {archived['path']}")
        print(f"  {archived['retired_chain']}")
    else:
        print("no existing C-state to retire")
    pipeline = result.get("pipeline") or {}
    print(f"rebuilt: {pipeline.get('status')} | c_state {pipeline.get('c_state_update')}")
    return 0


def record_baseline_measurement(
    store: str | Path,
    *,
    task: dict[str, Any],
    run: dict[str, Any] | None,
    agent: str,
    report: dict[str, Any] | None = None,
    status: str = "completed",
) -> dict[str, Any]:
    """Ingest one bare-model probe run against an already frozen TaskSpec.

    The probe never freezes a spec of its own: A is only comparable to J when
    both were scored on the same TaskSpec, so a task the agent never produced
    evidence for is skipped instead of creating a spec the agent cannot match.
    """
    store = Path(store)
    spec_path = _root(store) / "taskspecs" / f"{task['task_id']}.json"
    if not spec_path.is_file():
        return {"status": "skipped", "reason": "task_spec_not_frozen"}
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    # A spec frozen before the form-only contract still requires user_accept,
    # which a probe has no way to supply. Say so instead of silently producing
    # a run the engine will reject for a missing required metric.
    unsatisfiable = sorted(
        str(metric.get("metric_id"))
        for metric in spec.get("metrics") or []
        if metric.get("required") and str(metric.get("metric_id")) not in METRIC_IDS
    )
    if unsatisfiable:
        return {
            "status": "skipped",
            "reason": "task_spec_not_probe_comparable",
            "task_revision": spec.get("task_revision"),
            "unsatisfiable_metrics": unsatisfiable,
        }
    if run is None:
        return {"status": "skipped", "reason": "no_run"}
    _ensure_engine()
    from experience_evaluation.run_bundle import RunBundleStore

    route = baseline_route(_route_from_accept(agent=agent, store=store, task=task))
    identity = baseline_identity(_route_from_accept(agent=agent, store=store, task=task))
    _remember_identity(store, identity)
    bundle = build_run_bundle(
        task=task,
        run=run,
        route=route,
        mark="accept" if status == "completed" else "reject",
        report=report,
        task_revision=str(spec.get("task_revision") or TASK_SPEC_REVISION),
    )
    ingest = RunBundleStore(_root(store) / "runs").ingest([bundle])
    pipeline = _run_pipeline(store)
    return {
        "status": "recorded",
        "subject_id": identity["subject_id"],
        "task_spec_id": task["task_id"],
        "metrics": sorted(bundle["outcome"]["metrics"]),
        "ingest": ingest,
        "pipeline": pipeline,
    }


def _empty_axis(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "mean": None,
        "sd": None,
        "ci_low": None,
        "ci_high": None,
        "evidence_count": 0,
        "reason": reason,
        "a": None,
        "b": None,
        "j": None,
        "c": None,
    }


def _cell_value(lookup: dict[str, Any] | None, axis: str) -> float | None:
    for cell in (lookup or {}).get("cells") or []:
        if cell.get("dimension_id") == axis:
            value = cell.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _assert_no_totals(payload: dict[str, Any]) -> None:
    leaked = FORBIDDEN_TOTAL_KEYS & set(payload)
    if leaked:
        raise EvolutionError(f"measurement leaked forbidden aggregate keys: {sorted(leaked)}")


def load_measurement(store: str | Path, route: dict[str, Any]) -> dict[str, Any]:
    """Read-only A+B+J+C portrait. Missing axes stay unavailable, never 0."""
    store = Path(store)
    try:
        subject_id = subject_id_for(route)
    except EvolutionError as exc:
        payload = {
            "schema_version": MEASUREMENT_SCHEMA,
            "engine": "experience_evaluation",
            "engine_commit": ENGINE_COMMIT,
            "axis_contract_id": "livebench-capability-seven/v0.1",
            "scale_id": "canonical_logit/v0.1",
            "publication_status": "insufficient_evidence",
            "ranking": False,
            "subject_id": None,
            "observed_axis_count": 0,
            "unavailable_axis_count": len(CANONICAL_AXES),
            "not_claimed": [
                "public leaderboard",
                "agent total score",
                "user accept as public verifier",
            ],
            "axes": {axis: _empty_axis(str(exc)) for axis in CANONICAL_AXES},
            "reason": str(exc),
        }
        _assert_no_totals(payload)
        return payload

    portrait_axes: dict[str, dict[str, Any]] = {
        axis: _empty_axis("no_c_state") for axis in CANONICAL_AXES
    }
    publication_status = "insufficient_evidence"
    identity_resolution = None
    try:
        _ensure_engine()
        from experience_evaluation.c_state import CStateStore
        from experience_evaluation.profile import render_subject_profile

        c_root = _root(store) / "c-state"
        if c_root.is_dir() and (c_root / "config.json").is_file():
            portrait = render_subject_profile(
                CStateStore(c_root, _c_state_config(), read_only=True),
                subject_id,
                _portrait_policy(),
            )
            publication_status = portrait.get("publication_status") or publication_status
            for axis, row in (portrait.get("axes") or {}).items():
                if axis in portrait_axes:
                    portrait_axes[axis] = {
                        **portrait_axes[axis],
                        **row,
                        "c": row.get("mean"),
                    }
    except Exception as exc:  # measurement miss must not break the Agent page
        publication_status = "unavailable"
        for axis in portrait_axes:
            portrait_axes[axis]["reason"] = f"c_state_unreadable:{exc}"

    layer_path = _root(store) / "layers" / f"{_safe_subject(subject_id)}.json"
    layers = {}
    if layer_path.is_file():
        try:
            layers = json.loads(layer_path.read_text(encoding="utf-8"))
            identity_resolution = layers.get("identity_resolution")
        except (OSError, json.JSONDecodeError):
            layers = {}
    for axis in CANONICAL_AXES:
        portrait_axes[axis]["a"] = _cell_value(layers.get("a"), axis)
        portrait_axes[axis]["b"] = _cell_value(layers.get("b"), axis)
        portrait_axes[axis]["j"] = _cell_value(layers.get("j"), axis)
        if portrait_axes[axis].get("c") is None:
            portrait_axes[axis]["c"] = _cell_value(layers.get("c"), axis)
        if portrait_axes[axis]["status"] == "unavailable" and not portrait_axes[axis].get("reason"):
            portrait_axes[axis]["reason"] = "unavailable"

    observed = sum(1 for row in portrait_axes.values() if row.get("status") == "observed")
    payload = {
        "schema_version": MEASUREMENT_SCHEMA,
        "engine": "experience_evaluation",
        "engine_commit": ENGINE_COMMIT,
        "axis_contract_id": "livebench-capability-seven/v0.1",
        "scale_id": "canonical_logit/v0.1",
        "q_contract_id": Q_CONTRACT,
        "publication_status": publication_status,
        "ranking": False,
        "subject_id": subject_id,
        "identity": subject_identity(route),
        "identity_resolution": identity_resolution,
        "observed_axis_count": observed,
        "unavailable_axis_count": len(CANONICAL_AXES) - observed,
        "not_claimed": [
            "public leaderboard",
            "agent total score",
            "user accept as public verifier",
            "livebench common-item link",
        ],
        "axes": portrait_axes,
        "layers": {
            "a": (layers.get("a") or {}).get("status"),
            "b": (layers.get("b") or {}).get("status"),
            "j": (layers.get("j") or {}).get("status"),
            "c": (layers.get("c") or {}).get("status"),
        },
    }
    _assert_no_totals(payload)
    return payload
