"""Multi-Agent Collaboration (P8 v0.1): fixed, auditable patterns.

Three deterministic patterns over the replay engine's executor seam:
- handoff: A works, B continues with ARTIFACT_ONLY context (never full history);
- parallel-compare: agents execute independently and preserve each result;
- author-reviewer: A authors, B reviews the artifact (verdict recorded).

Patterns are code-controlled (no autonomous agent loops), budgets apply
outside, and every step is recorded for the ledger.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .replay import restore_t0
from .episode import show_episode


class CollabError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


Executor = Callable[[Path, str, float], tuple[str, str, str, int]]


def _run_step(
    executor: Executor, workspace: Path, prompt: str, timeout: float
) -> dict[str, Any]:
    started = time.monotonic()
    result = executor(workspace, prompt, timeout)
    status, stdout, stderr, exit_code = result[:4]
    return {
        "status": status,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-1000:],
        "exit_code": exit_code,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }


def _workspace_text(workspace: Path, *, max_files: int = 20, max_chars: int = 4000) -> str:
    parts = []
    files = [
        item
        for item in sorted(workspace.rglob("*"))
        if item.is_file() and ".git" not in item.parts
    ][:max_files]
    for item in files:
        try:
            parts.append(f"--- {item.relative_to(workspace)} ---\n{item.read_text(encoding='utf-8', errors='replace')[:500]}")
        except OSError:
            continue
    return "\n".join(parts)[:max_chars]


def handoff(
    store: str | Path,
    episode_id: str,
    *,
    agent_a: str,
    agent_b: str,
    executor_a: Executor,
    executor_b: Executor,
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """A starts; B continues with ONLY A's artifact (handoff ≠ memory transfer)."""
    store = Path(store)
    episode = show_episode(store, episode_id)
    request = episode["task_start"]["original_user_request"]
    run_root = store / "collabs" / ("co-" + uuid.uuid4().hex[:8])
    ws_a = run_root / "workspace-a"
    ws_b = run_root / "workspace-b"
    restore_t0(episode, ws_a)
    shutil.copytree(ws_a, ws_b, ignore=shutil.ignore_patterns(".git"))

    step_a = _run_step(executor_a, ws_a, f"TASK\n----\n{request}", timeout_sec)
    if step_a["status"] != "completed":
        return _collab_result("handoff", episode_id, [("A", agent_a, step_a)], "failed", run_root)
    artifact = _workspace_text(ws_a)
    if step_a["stdout_tail"]:
        artifact = f"{artifact}\n\nAGENT OUTPUT\n{step_a['stdout_tail']}"
    prompt_b = (
        f"TASK\n----\n{request}\n\n"
        "WORK DONE SO FAR BY ANOTHER AGENT (artifact only; improve/continue it)\n"
        f"{artifact}"
    )
    step_b = _run_step(executor_b, ws_b, prompt_b, timeout_sec)
    status = "completed" if step_b["status"] == "completed" else "failed"
    return _collab_result(
        "handoff",
        episode_id,
        [("A", agent_a, step_a), ("B", agent_b, step_b)],
        status,
        run_root,
        context_note="B received ARTIFACT_ONLY; no history, no private memory",
    )


def parallel_compare(
    store: str | Path,
    episode_id: str,
    *,
    agents: list[tuple[str, Executor]],
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """All agents execute independently and preserve each result for comparison."""
    store = Path(store)
    episode = show_episode(store, episode_id)
    request = episode["task_start"]["original_user_request"]
    run_root = store / "collabs" / ("co-" + uuid.uuid4().hex[:8])
    outputs: dict[str, str] = {}
    steps = []
    for index, (agent, executor) in enumerate(agents):
        workspace = run_root / f"workspace-{index}"
        restore_t0(episode, workspace)
        step = _run_step(executor, workspace, f"TASK\n----\n{request}", timeout_sec)
        steps.append((f"agent-{index}", agent, step))
        workspace_output = _workspace_text(workspace)
        outputs[agent] = f"{workspace_output}\n\nAGENT OUTPUT\n{step['stdout_tail']}".strip()
    comparison = "\n\n".join(f"== {name} ==\n{text}" for name, text in outputs.items())
    (run_root / "comparison.txt").write_text(comparison, encoding="utf-8")
    status = "completed" if all(s[2]["status"] == "completed" for s in steps) else "partial"
    return _collab_result(
        "parallel-compare", episode_id, steps, status, run_root,
        summary=f"{len(steps)} independent results preserved for comparison",
    )


def parallel_integrate(
    store: str | Path,
    episode_id: str,
    *,
    agents: list[tuple[str, Executor]],
    integrator: Callable[[str, dict[str, str]], str] | None = None,
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """Compatibility alias; integrations are now represented as comparisons."""
    return parallel_compare(
        store, episode_id, agents=agents, timeout_sec=timeout_sec
    )


def validate_template_workflow(
    template_id: str,
    assignments: list[dict[str, str]],
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], str]]]:
    """Validate a template workflow without filesystem writes or budget use."""
    from .templates import get_template

    template = get_template(template_id)
    if template is None:
        raise CollabError(f"unknown collaboration template: {template_id}")
    if not isinstance(assignments, list) or not assignments:
        raise CollabError("template workflow requires step assignments")
    by_key = {child["key"]: child for child in template["children"]}
    seen: set[str] = set()
    normalized: list[tuple[dict[str, Any], str]] = []
    for index, assignment in enumerate(assignments, start=1):
        if not isinstance(assignment, dict):
            raise CollabError(f"assignments[{index}] must be an object")
        key = str(assignment.get("key") or "")
        agent = str(assignment.get("agent") or "")
        if key not in by_key:
            raise CollabError(f"assignments[{index}] has unknown template key: {key!r}")
        if key in seen:
            raise CollabError(f"duplicate template assignment: {key}")
        if not agent:
            raise CollabError(f"template phase {key} has no agent")
        seen.add(key)
        normalized.append((by_key[key], agent))
    missing = [
        child["key"] for child in template["children"]
        if not child.get("optional") and child["key"] not in seen
    ]
    if missing:
        raise CollabError("required template phases omitted: " + ", ".join(missing))
    if len({agent for _, agent in normalized}) < 2:
        raise CollabError("template workflow requires at least 2 distinct agents/models")
    return template, normalized


def template_workflow(
    store: str | Path,
    episode_id: str,
    *,
    template_id: str,
    assignments: list[dict[str, str]],
    executor_factory: Callable[[str], Executor],
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """Run a fixed task-template skeleton as an auditable multi-agent workflow.

    Required template phases cannot be omitted. Optional phases may be
    unchecked by the user. Agents share only the isolated T0 workspace and
    prior phase outputs; private session history/memory is never transferred.
    """
    template, normalized = validate_template_workflow(template_id, assignments)
    participating = {agent for _, agent in normalized}

    store = Path(store)
    episode = show_episode(store, episode_id)
    request = episode["task_start"]["original_user_request"]
    run_root = store / "collabs" / ("co-" + uuid.uuid4().hex[:8])
    workspace = run_root / "workspace"
    restore_t0(episode, workspace)
    steps: list[tuple[str, str, dict[str, Any]]] = []
    prior_outputs: list[str] = []
    status = "completed"
    for index, (phase, agent) in enumerate(normalized, start=1):
        prior = "\n\n".join(prior_outputs)[-4000:] or "No earlier phase output."
        prompt = (
            f"TASK\n----\n{request}\n\n"
            f"FIXED WORKFLOW TEMPLATE\n----\n{template['name']}\n\n"
            f"CURRENT PHASE {index}/{len(normalized)}: {phase['title']}\n"
            f"{phase['description']}\n\n"
            "Complete only this phase. Work in the shared isolated workspace. "
            "Use earlier phase outputs as context, verify your result, and leave "
            "artifacts for the next phase.\n\n"
            f"EARLIER PHASE OUTPUTS\n----\n{prior}"
        )
        step = _run_step(executor_factory(agent), workspace, prompt, timeout_sec)
        steps.append((phase["key"], agent, step))
        prior_outputs.append(
            f"[{phase['key']} by {agent}]\n{step['stdout_tail']}\n{step['stderr_tail']}"
        )
        if step["status"] != "completed":
            status = "failed"
            break
    return _collab_result(
        "template-workflow",
        episode_id,
        steps,
        status,
        run_root,
        template={
            "template_id": template["template_id"],
            "name": template["name"],
            "version": template["version"],
            "selected_phases": [phase["key"] for phase, _ in normalized],
        },
        summary=f"{template['name']}: {len(steps)}/{len(normalized)} phases executed by {len(participating)} targets",
        context_note="Agents shared the isolated workspace and prior phase outputs only; no private history",
    )


def author_reviewer(
    store: str | Path,
    episode_id: str,
    *,
    author: str,
    reviewer: str,
    executor_author: Executor,
    executor_reviewer: Executor,
    timeout_sec: float = 600,
) -> dict[str, Any]:
    """Author produces an artifact; the selected reviewer agent evaluates it."""
    store = Path(store)
    episode = show_episode(store, episode_id)
    request = episode["task_start"]["original_user_request"]
    run_root = store / "collabs" / ("co-" + uuid.uuid4().hex[:8])
    workspace = run_root / "workspace"
    restore_t0(episode, workspace)
    step_a = _run_step(executor_author, workspace, f"TASK\n----\n{request}", timeout_sec)
    if step_a["status"] != "completed":
        return _collab_result("author-reviewer", episode_id, [("author", author, step_a)], "failed", run_root)
    artifact = _workspace_text(workspace)
    if step_a["stdout_tail"]:
        artifact = f"{artifact}\n\nAGENT OUTPUT\n{step_a['stdout_tail']}"
    review_workspace = run_root / "review-workspace"
    restore_t0(episode, review_workspace)
    review_prompt = (
        f"TASK\n----\n{request}\n\n"
        "ARTIFACT TO REVIEW\n----\n"
        f"{artifact}\n\n"
        "Review this artifact. End with JSON containing verdict "
        "(accept, revise, reject, or inconclusive) and notes."
    )
    step_b = _run_step(executor_reviewer, review_workspace, review_prompt, timeout_sec)
    review = _review_from_output(step_b["stdout_tail"])
    (run_root / "review.json").write_text(
        json.dumps({"reviewer": reviewer, **review}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    status = "completed" if step_b["status"] == "completed" else "failed"
    return _collab_result(
        "author-reviewer",
        episode_id,
        [("author", author, step_a), ("reviewer", reviewer, step_b)],
        status,
        run_root,
        summary=f"{reviewer} reviewed {author}'s artifact",
        review={"reviewer": reviewer, **review},
        context_note="Reviewer received the task and author artifact only; no private history",
    )


def _review_from_output(output: str) -> dict[str, str]:
    verdicts = {"accept", "revise", "reject", "inconclusive"}
    try:
        start, end = output.find("{"), output.rfind("}")
        parsed = json.loads(output[start:end + 1]) if start >= 0 and end > start else {}
    except (json.JSONDecodeError, TypeError):
        parsed = {}
    verdict = str(parsed.get("verdict") or "inconclusive").lower()
    if verdict not in verdicts:
        verdict = "inconclusive"
    notes = str(parsed.get("notes") or parsed.get("note") or output).strip()[-1000:]
    return {"verdict": verdict, "notes": notes}


def _collab_result(
    pattern: str,
    episode_id: str,
    steps: list[tuple[str, str, dict[str, Any]]],
    status: str,
    run_root: Path,
    **extra: Any,
) -> dict[str, Any]:
    from .coordination import team_revision_id

    members = [agent for _role, agent, _step in steps]
    topology = {
        "handoff": "sequential",
        "parallel-compare": "parallel",
        "author-reviewer": "hierarchical",
    }.get(pattern, f"custom:{pattern}")
    result = {
        "schema_version": "icle-collab-run/v0.2",
        "collab_id": run_root.name,
        "pattern": pattern,
        "episode_id": episode_id,
        "team_revision_id": team_revision_id(members=members, topology=topology),
        "status": status,
        "steps": [
            {"role": role, "agent": agent, **step} for role, agent, step in steps
        ],
        "run_dir": str(run_root),
        "created_at": _now(),
        **extra,
    }
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "collab.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result
