"""AgentDiscoveryService (v0.4 P9 / Batch 1): auto-detect local agent CLIs.

参考 CC Switch / ReevesAgents 的做法(计划书 §2.1、§10):
- Level A  executable discovery: CLI signature catalog + PATH 定位(只读)
- Level B  safe version probe: 只执行 catalog 写死的白名单命令(--version)
- Level C  native home detection: 只记录目录存在性,绝不读 secret 明文
- Level D  protocol probe: ACP 本轮未启用,统一标记 not_probed

铁律(§11): 发现 ≠ 自动授权。detected 之后用户点击 Link 才写入 linked
状态;本模块绝不 login/chat/install/update,也绝不修改任何 agent 配置。
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .adapters import (
    adapter_for,
    capture_supported as adapter_capture_supported,
    catalog_for_discovery,
    descriptions,
    execution_supported as adapter_execution_supported,
    locate_executable,
)

# Signature catalog lives in adapters.py (single source of truth).
AGENT_CATALOG: list[dict[str, Any]] = catalog_for_discovery()
AGENT_DESCRIPTIONS: dict[str, tuple[str, str]] = descriptions()

DETECTION_SOURCES = ("PATH", "native_home", "config", "ACP")
EXECUTION_SUPPORTED_AGENTS = {
    item["agent_type"] for item in AGENT_CATALOG if item.get("execution_binaries")
}
CAPTURE_SUPPORTED_AGENTS = {
    item["agent_type"] for item in AGENT_CATALOG if adapter_capture_supported(item["agent_type"])
}
AGENT_STATUSES = ("detected", "runnable", "linked", "unavailable", "broken")

_PROBE_TIMEOUT = 5.0


class DiscoveryError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, delete=False, mode="w", encoding="utf-8"
    ) as handle:
        handle.write(content)
        temp = handle.name
    os.replace(temp, path)


# ---------------------------------------------------------------- persistence (linked)


def _agents_path(store: str | Path) -> Path:
    return Path(store) / "agents.json"


def _load_linked(store: str | Path) -> dict[str, Any]:
    path = _agents_path(store)
    if not path.is_file():
        return {"linked": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def link_agent(store: str | Path, agent_type: str) -> dict[str, Any]:
    """User explicitly links a detected agent (§11: discovery ≠ authorization).

    Records linked state only; never modifies the agent's own configuration.
    """
    known = {entry["agent_type"] for entry in AGENT_CATALOG}
    if agent_type not in known:
        raise DiscoveryError(f"unknown agent_type: {agent_type}")
    data = _load_linked(store)
    data["linked"][agent_type] = {"linked_at": _now()}
    _atomic_write(_agents_path(store), json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data["linked"][agent_type]


def unlink_agent(store: str | Path, agent_type: str) -> None:
    data = _load_linked(store)
    data["linked"].pop(agent_type, None)
    _atomic_write(_agents_path(store), json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _is_linked(store: str | Path, agent_type: str) -> bool:
    return agent_type in _load_linked(store)["linked"]


def link_all_agents(store: str | Path) -> dict[str, Any]:
    """一键链接所有可运行 agent(计划书 §11「Link all detected」)。

    只链接 scan 到 runnable/linked 的 agent;未安装的跳过。用户仍可单个
    unlink(发现/授权由用户控制,绝不自动修改 agent 配置)。
    """
    store = Path(store)
    agents = scan_agents(store, probe=False)
    linked: list[str] = []
    for agent in agents:
        if agent["status"] in ("runnable", "linked") and (
            agent.get("execution_supported") or agent.get("capture_supported")
        ):
            link_agent(store, agent["agent_type"])
            linked.append(agent["agent_type"])
    return {"linked": sorted(linked), "count": len(linked)}


# ---------------------------------------------------------------- discovery layers


def _locate_executable(entry: dict[str, Any]) -> str | None:
    """Level A: PATH, then catalog extra_paths (app-bundled CLIs)."""
    return locate_executable(entry)


def _detect_native_home(entry: dict[str, Any]) -> str | None:
    """Level C: record existence of known native config dirs; never read them."""
    for pattern in entry.get("native_homes", []):
        candidate = Path(pattern).expanduser()
        if candidate.is_dir():
            return str(candidate)
    return None


def probe_version(executable: str, probe: list[str]) -> tuple[str | None, str | None]:
    """Level B: run ONLY the catalog-defined safe probe (e.g. --version).

    Returns (version, error). Anything other than the whitelisted probe is
    refused — Discovery must never run login/chat/install/update.
    """
    if not probe or any(not str(part).startswith("-") for part in probe):
        # probe must be a whitelisted flag list from the catalog (no shell words)
        return None, "unsafe probe refused"
    try:
        completed = subprocess.run(
            [executable, *probe],
            capture_output=True,
            text=True,
            check=False,
            timeout=_PROBE_TIMEOUT,
            env=dict(os.environ),
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"probe failed: {type(exc).__name__}"
    if completed.returncode != 0:
        return None, (completed.stderr or completed.stdout or "non-zero exit").strip()[:200]
    version = (completed.stdout or completed.stderr).strip().splitlines()
    return (version[0][:80] if version else None), None


def _session_capture_count(agent_type: str, capture_store: str | Path | None) -> int:
    """Count captured native sessions for an agent (existence only)."""
    if capture_store is None:
        return 0
    root = Path(capture_store) / agent_type
    if not root.is_dir():
        return 0
    return sum(1 for path in root.iterdir() if path.is_dir())


def scan_agents(
    store: str | Path,
    *,
    capture_store: str | Path | None = None,
    catalog: list[dict[str, Any]] | None = None,
    probe: bool = True,
) -> list[dict[str, Any]]:
    """Scan local agents (Level A+C) and merge linked state.

    Returns DetectedAgent list (plan §9). `catalog` is injectable for tests;
    `probe=False` skips --version (fast path for the UI list).
    """
    store = Path(store)
    catalog = catalog if catalog is not None else AGENT_CATALOG
    detected: list[dict[str, Any]] = []
    for entry in catalog:
        agent_type = entry["agent_type"]
        executable = _locate_executable(entry)
        native_home = _detect_native_home(entry)
        linked = _is_linked(store, agent_type)
        if executable is not None:
            status = "linked" if linked else "runnable"
        elif native_home is not None:
            status = "linked" if linked else "detected"
        else:
            status = "unavailable"
        version: str | None = None
        probe_errors: list[str] = []
        if executable and probe:
            version, error = probe_version(executable, entry.get("version_probe", ["--version"]))
            if error:
                probe_errors.append(error)
        can_execute = adapter_execution_supported(agent_type, executable)
        if executable and not can_execute and adapter_for(agent_type):
            probe_errors.append(
                "CLI located but oneshot adapter needs the Agent binary "
                f"({', '.join(entry.get('execution_binaries') or [])})"
            )
        detected.append(
            {
                "detection_id": agent_type,
                "execution_supported": can_execute,
                "capture_supported": adapter_capture_supported(agent_type),
                "agent_type": agent_type,
                "display_name": entry["display_name"],
                "description": AGENT_DESCRIPTIONS.get(agent_type, (entry["display_name"], entry["display_name"]))[0],
                "description_zh": AGENT_DESCRIPTIONS.get(agent_type, (entry["display_name"], entry["display_name"]))[1],
                "executable_path": executable,
                "version": version,
                "detection_sources": ["PATH"] if executable else [],
                "native_home": native_home,
                "connection_modes": entry.get("connection_modes", ["direct_cli"]),
                "status": status,
                "capabilities": entry.get("capabilities", []),
                "session_capture_count": _session_capture_count(agent_type, capture_store),
                "last_probe": _now() if executable else None,
                "probe_errors": probe_errors,
            }
        )
    return detected


def doctor_agents(
    store: str | Path,
    *,
    capture_store: str | Path | None = None,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Agent Doctor (§13): per-agent health rows + summary.

    Checks: Executable / Version / Native home / Session capture / ACP(not
    probed this round). Used by `icle doctor agents` and the Local Agents UI.
    """
    agents = scan_agents(store, capture_store=capture_store, catalog=catalog)
    rows = []
    needs_attention = 0
    for agent in agents:
        checks = {
            "executable": bool(agent.get("executable_path")),
            "version": bool(agent.get("version")),
            "native_home": bool(agent.get("native_home")),
            "session_capture": agent.get("session_capture_count", 0) > 0,
            "acp": None,  # Level D not enabled this round (v0.4 P16)
        }
        if agent["status"] == "unavailable" or agent.get("probe_errors"):
            needs_attention += 1
        rows.append({"agent_type": agent["agent_type"], "display_name": agent["display_name"],
                     "status": agent["status"], "checks": checks,
                     "probe_errors": agent.get("probe_errors", [])})
    ready = sum(1 for r in rows if r["status"] in ("runnable", "linked"))
    return {
        "schema_version": "icle-agent-doctor/v0.1",
        "agents": rows,
        "summary": {
            "total": len(rows),
            "ready": ready,
            "needs_attention": needs_attention,
        },
        "created_at": _now(),
    }


def _print_table(rows: list[dict[str, Any]]) -> None:
    header = f"{'agent':<10} {'status':<12} {'version':<24} {'path'}"
    print(header)
    print("-" * len(header))
    for agent in rows:
        print(
            f"{agent['agent_type']:<10} {agent['status']:<12} "
            f"{(agent.get('version') or '—')[:24]:<24} {agent.get('executable_path') or '—'}"
        )


def cmd_scan(store: str | Path, capture_store: str | Path | None = None) -> int:
    agents = scan_agents(store, capture_store=capture_store)
    _print_table(agents)
    return 0


def cmd_doctor(store: str | Path, capture_store: str | Path | None = None) -> int:
    result = doctor_agents(store, capture_store=capture_store)
    for row in result["agents"]:
        status = "✓" if row["status"] in ("runnable", "linked") else "○"
        checks = row["checks"]
        print(
            f"{status} {row['display_name']:<14} {row['status']:<12} "
            f"exe:{'✓' if checks['executable'] else '✗'} ver:{'✓' if checks['version'] else '✗'} "
            f"home:{'✓' if checks['native_home'] else '—'} cap:{'✓' if checks['session_capture'] else '—'} acp:—"
        )
        for error in row.get("probe_errors", []):
            print(f"    ⚠ {error}")
    summary = result["summary"]
    print(f"\n{summary['ready']}/{summary['total']} ready, {summary['needs_attention']} need attention")
    return 0
