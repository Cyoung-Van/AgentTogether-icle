"""Replay Engine (P3): re-execute a TaskEpisode from T0 with another agent.

Fairness rules (from the plan): the replay agent must NOT see the source
agent's answer, trajectory, artifacts, user evaluations, or other replays.
ContextBundle modes: CLEAN (request only), PROJECT_STATE (request + snapshot
summary), SELECTED_HISTORY (request + user-role events from the marked turn
range — user clarifications only, never the source agent's outputs).

MVP limitation: T0 content restore requires a git revision; non-git episodes
replay against a fresh empty workspace and record the limitation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import command_template as adapter_command_template, resolve_binary
from .episode import show_episode
from .schema import validate_replay_run

_REPLAY_ID_RE = re.compile(r"^rp-[A-Za-z0-9_-]+$")


def assert_replay_id(replay_id: Any) -> str:
    """S2: replay_id 路径穿越防护 —— 只允许白名单字符,拒绝 / 与 .. 段。"""
    if not isinstance(replay_id, str) or not _REPLAY_ID_RE.match(replay_id):
        raise ReplayError(f"invalid replay_id: {replay_id!r}")
    return replay_id


class ReplayError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# agent_id -> verified direct-CLI command template ({workspace}, {prompt} placeholders)
# home policy: agents run under their REAL native home (auth/persona/memory are
# part of the agent's identity and symmetric across agents); the ContextBundle
# alone controls task-specific information. Staged-home policy comes later.
# agent_id → argv template ({binary}, {workspace}, {prompt})
DIRECT_CLI_AGENTS = {
    item: adapter_command_template(item)
    for item in (
        "claude", "codex", "cursor", "gemini", "kimi", "hermes",
        "opencode", "openclaw", "aider", "pi", "qwen",
    )
    if adapter_command_template(item)
}


def restore_t0(episode: dict[str, Any], destination: Path) -> dict[str, Any]:
    """Restore the task-start project state into an isolated workspace."""
    snapshot = episode["task_start"]["project_snapshot"]
    project_path = Path(snapshot["project_path"])
    destination.mkdir(parents=True, exist_ok=True)
    if "git_revision" in snapshot:
        if not project_path.is_dir():
            raise ReplayError(f"original project missing: {project_path}")
        clone = subprocess.run(
            ["git", "clone", "-q", str(project_path), str(destination)],
            capture_output=True, text=True, check=False,
        )
        if clone.returncode != 0:
            raise ReplayError(f"git clone failed: {clone.stderr[:200]}")
        checkout = subprocess.run(
            ["git", "checkout", "-q", snapshot["git_revision"]],
            cwd=destination, capture_output=True, text=True, check=False,
        )
        if checkout.returncode != 0:
            raise ReplayError(f"git checkout failed: {checkout.stderr[:200]}")
        return {"restored": "git", "revision": snapshot["git_revision"]}
    # non-git: MVP cannot restore content (only the hash was recorded)
    return {
        "restored": "empty",
        "limitation": "non-git project: content restore unavailable at MVP; "
        "replaying against an empty workspace",
        "workspace_sha256": snapshot.get("workspace_sha256"),
    }


def build_context_bundle(
    episode: dict[str, Any],
    mode: str,
    *,
    capture_store: str | Path | None = None,
) -> dict[str, Any]:
    """Construct the (fairness-checked) context the replay agent may see."""
    request = episode["task_start"]["original_user_request"]
    parts = [f"TASK\n----\n{request}"]
    refs: list[str] = []
    if mode == "CLEAN":
        pass
    elif mode == "PROJECT_STATE":
        snapshot = episode["task_start"]["project_snapshot"]
        parts.append(
            "\nPROJECT STATE AT TASK START\n"
            f"- git revision: {snapshot.get('git_revision', 'n/a')}\n"
            f"- workspace hash: {snapshot.get('workspace_sha256', 'n/a')}"
        )
    elif mode == "SELECTED_HISTORY":
        turn_range = episode["task_start"].get("turn_range")
        if not turn_range:
            raise ReplayError("SELECTED_HISTORY requires an episode turn_range")
        if capture_store is None:
            raise ReplayError("SELECTED_HISTORY requires capture_store")
        agent_id = episode["source_agent_revision"]["agent_id"]
        # S3: capture 路径安全 —— 白名单校验,拒绝 ../ 逃逸
        if not re.match(r"^[A-Za-z0-9_-]+$", agent_id):
            raise ReplayError(f"invalid agent_id in episode: {agent_id!r}")
        session_id = episode["source_session"]
        if not re.match(r"^[A-Za-z0-9._-]+$", session_id):
            raise ReplayError(f"invalid source_session in episode: {session_id!r}")
        events_path = Path(capture_store) / agent_id / session_id / "events.jsonl"
        if not events_path.is_file():
            raise ReplayError(f"captured session missing: {events_path}")
        user_lines = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if (
                turn_range["from_seq"] <= event["seq"] <= turn_range["to_seq"]
                and event["kind"] == "user"  # NEVER source-agent outputs
            ):
                user_lines.append(event["content"])
        refs.append(
            f"{agent_id}/{episode['source_session']}/events.jsonl"
            f"#{turn_range['from_seq']}-{turn_range['to_seq']} (user only)"
        )
        parts.append("\nUSER CONTEXT FROM THE ORIGINAL SESSION\n" + "\n".join(user_lines))
    else:
        raise ReplayError(f"unsupported context mode for MVP: {mode}")
    text = "\n".join(parts)
    return {
        "schema_version": "icle-context-bundle/v0.1",
        "mode": mode,
        "refs": refs,
        "sha256": _sha_text(text),
        "created_at": _now(),
        "text": text,
    }


def _render_command(template: list[str], workspace: Path, prompt: str, binary: str) -> list[str]:
    return [
        part.replace("{binary}", binary)
        .replace("{workspace}", str(workspace))
        .replace("{prompt}", prompt)
        for part in template
    ]


def make_direct_executor(agent_id: str, timeout_sec: float = 600) -> Any:
    """Build an Executor for the collab layer from DIRECT_CLI_AGENTS."""
    template = DIRECT_CLI_AGENTS.get(agent_id)
    if template is None:
        raise ReplayError(f"no direct_cli command registered for agent: {agent_id}")

    def executor(workspace: Path, prompt: str, timeout: float):
        binary = resolve_binary(agent_id)
        if binary is None:
            return ("failed", "", f"agent CLI not found: {agent_id}", -1)
        command = _render_command(template, workspace, prompt, binary)
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=dict(os.environ),
            )
            return (
                "completed" if completed.returncode == 0 else "failed",
                completed.stdout,
                completed.stderr,
                completed.returncode,
            )
        except subprocess.TimeoutExpired:
            return ("failed", "", "timeout", -1)

    return executor


def replay(
    store: str | Path,
    episode_id: str,
    *,
    target_agent_revision: dict[str, Any],
    mode: str = "CLEAN",
    capture_store: str | Path | None = None,
    agents: dict[str, list[str]] | None = None,
    timeout_sec: float = 900,
    executor: Any | None = None,
    replay_id: str | None = None,  # M1: API 预分配时透传,返回 ID 与落盘一致
) -> dict[str, Any]:
    """Restore T0, build the bundle, run the target agent, save the ReplayRun."""
    store = Path(store)
    episode = show_episode(store, episode_id)
    bundle = build_context_bundle(episode, mode, capture_store=capture_store)
    replays_dir = store / "replays"
    replays_dir.mkdir(parents=True, exist_ok=True)
    if replay_id is not None:
        assert_replay_id(replay_id)  # S2
        replay_root = replays_dir / replay_id
        if replay_root.exists():
            # API 已通过 O_EXCL 预创建(空目录)或历史残留 —— 空目录才可复用
            if any(replay_root.iterdir()):
                raise ReplayError(f"replay id already in use: {replay_id}")
        else:
            replay_root.mkdir(parents=False, exist_ok=False)
    else:
        # M1: 原子分配 —— O_EXCL 目录创建,并发/删除复用不再覆盖
        n = len(sorted(replays_dir.glob("rp-*/")))
        while True:
            n += 1
            replay_id = f"rp-{n:04d}"
            replay_root = replays_dir / replay_id
            try:
                replay_root.mkdir(parents=False, exist_ok=False)
                break
            except FileExistsError:
                continue
    workspace = replay_root / "workspace"
    restore_info = restore_t0(episode, workspace)

    agent_id = target_agent_revision["agent_id"]
    started = time.monotonic()
    if executor is not None:
        result = executor(workspace, bundle["text"], timeout_sec)
        outcome, stdout, stderr, exit_code = result[0], result[1], result[2], result[3]
    else:
        command_template = (agents or DIRECT_CLI_AGENTS).get(agent_id)
        if command_template is None:
            raise ReplayError(f"no direct_cli command registered for agent: {agent_id}")
        binary = resolve_binary(agent_id) or command_template[0]
        command = _render_command(command_template, workspace, bundle["text"], binary)
        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
                env=dict(os.environ),  # native home policy (see DIRECT_CLI_AGENTS note)
            )
            outcome = "completed" if completed.returncode == 0 else "failed"
            stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired:
            outcome, stdout, stderr, exit_code = "failed", "", "timeout", None
    duration_ms = round((time.monotonic() - started) * 1000)

    (replay_root / "home").mkdir(exist_ok=True)
    (replay_root / "stdout.log").write_text(stdout, encoding="utf-8")
    (replay_root / "stderr.log").write_text(stderr, encoding="utf-8")
    run = {
        "schema_version": "icle-replay-run/v0.1",
        "replay_id": replay_id,
        "episode_id": episode_id,
        "target_agent_revision": target_agent_revision,
        "context_bundle": {k: v for k, v in bundle.items() if k != "text"},
        "status": outcome,
        "restore": restore_info,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "created_at": _now(),
    }
    validate_replay_run(run)
    (replay_root / "replay.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return run


def record_failed_replay(
    store: str | Path,
    *,
    replay_id: str,
    episode_id: str,
    target_agent_revision: dict[str, Any],
    mode: str,
    error: str,
) -> dict[str, Any]:
    """Persist an orchestration failure so it remains visible after refresh."""
    assert_replay_id(replay_id)
    replay_root = Path(store) / "replays" / replay_id
    replay_root.mkdir(parents=True, exist_ok=True)
    message = str(error)[:1000]
    (replay_root / "stderr.log").write_text(message, encoding="utf-8")
    (replay_root / "stdout.log").write_text("", encoding="utf-8")
    run = {
        "schema_version": "icle-replay-run/v0.1",
        "replay_id": replay_id,
        "episode_id": episode_id,
        "target_agent_revision": target_agent_revision,
        "context_bundle": {"mode": mode, "refs": [], "created_at": _now()},
        "status": "failed",
        "restore": {"error": message},
        "duration_ms": 0,
        "exit_code": -1,
        "created_at": _now(),
    }
    validate_replay_run(run)
    (replay_root / "replay.json").write_text(
        json.dumps(run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return run


def list_replays(store: str | Path, episode_id: str | None = None) -> list[dict[str, Any]]:
    replays_dir = Path(store) / "replays"
    if not replays_dir.is_dir():
        return []
    out = []
    for path in sorted(replays_dir.glob("rp-*/replay.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if episode_id and doc["episode_id"] != episode_id:
            continue
        out.append(
            {
                "replay_id": doc["replay_id"],
                "episode_id": doc["episode_id"],
                "agent": doc["target_agent_revision"]["agent_id"],
                "status": doc["status"],
                "mode": doc["context_bundle"]["mode"],
                "duration_ms": doc["duration_ms"],
            }
        )
    return out
