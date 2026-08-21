"""Local Agent adapters: discovery signatures + verified oneshot CLIs.

Discovery is not authorization. Execution templates are print/exec modes
only — never login, install, or interactive TTY. Capture remains a separate
read-only importer where a session format is already verified.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

_CURSOR_APP = "/Applications/Cursor.app/Contents/Resources/app/bin/cursor"

AGENT_ADAPTERS: list[dict[str, Any]] = [
    {
        "agent_type": "claude",
        "display_name": "Claude Code",
        "executables": ["claude"],
        "version_probe": ["--version"],
        "native_homes": ["~/.claude"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Anthropic coding agent; sessions can be imported.", "Anthropic 编码 Agent；可导入会话并 oneshot 执行。"),
        "execution": {"binaries": ["claude"], "args": ["-p", "--output-format", "text", "{prompt}"]},
        "capture": True,
    },
    {
        "agent_type": "codex",
        "display_name": "Codex",
        "executables": ["codex"],
        "version_probe": ["--version"],
        "native_homes": ["~/.codex"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("OpenAI Codex CLI; sessions can be imported.", "OpenAI Codex CLI；可导入会话并 oneshot 执行。"),
        "execution": {"binaries": ["codex"], "args": ["exec", "{prompt}"]},
        "capture": True,
    },
    {
        "agent_type": "cursor",
        "display_name": "Cursor",
        "executables": ["cursor-agent", "agent"],
        "extra_paths": [_CURSOR_APP],
        "execution_binaries": ["cursor-agent", "agent"],
        "version_probe": ["--version"],
        "native_homes": ["~/.cursor"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["shell", "filesystem", "tools"],
        "description": (
            "Cursor Agent CLI (cursor-agent / agent -p). The editor binary alone cannot run tasks.",
            "Cursor Agent CLI（cursor-agent / agent -p）。仅有编辑器不能执行任务。",
        ),
        "execution": {"binaries": ["cursor-agent", "agent"], "args": ["-p", "--force", "{prompt}"]},
        "capture": False,
        "identity_hint": "cursor",
    },
    {
        "agent_type": "gemini",
        "display_name": "Gemini CLI",
        "executables": ["gemini"],
        "version_probe": ["--version"],
        "native_homes": ["~/.gemini"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Google Gemini command-line agent.", "Google Gemini 命令行 Agent。"),
        "execution": {"binaries": ["gemini"], "args": ["-p", "{prompt}"]},
        "capture": False,
    },
    {
        "agent_type": "kimi",
        "display_name": "Kimi Code",
        "executables": ["kimi", "kimi-code"],
        "version_probe": ["--version"],
        "native_homes": ["~/.kimi-code", "~/.kimi"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Kimi coding agent with task execution and session capture.", "Kimi 编码 Agent；支持任务执行与会话捕获。"),
        "execution": {"binaries": ["kimi", "kimi-code"], "args": ["-p", "{prompt}"]},
        "capture": True,
    },
    {
        "agent_type": "hermes",
        "display_name": "Hermes",
        "executables": ["hermes"],
        "version_probe": ["--version"],
        "native_homes": ["~/.hermes"],
        "connection_modes": ["direct_cli", "harness"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Hermes agent with task execution and session capture.", "Hermes Agent；支持任务执行与会话捕获。"),
        "execution": {
            "binaries": ["hermes"],
            "args": [
                "--safe-mode", "--in", "{workspace}",
                "--model", "gpt-5.6-luna", "--provider", "openai-api",
                "--oneshot", "{prompt}",
            ],
        },
        "capture": True,
    },
    {
        "agent_type": "opencode",
        "display_name": "OpenCode",
        "executables": ["opencode"],
        "version_probe": ["--version"],
        "native_homes": ["~/.opencode", "~/.local/share/opencode"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("OpenCode coding agent; sessions can be imported.", "OpenCode 编码 Agent；可导入会话并 oneshot 执行。"),
        "execution": {"binaries": ["opencode"], "args": ["run", "{prompt}"]},
        "capture": True,
    },
    {
        "agent_type": "openclaw",
        "display_name": "OpenClaw",
        "executables": ["openclaw"],
        "version_probe": ["--version"],
        "native_homes": ["~/.openclaw"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["shell", "filesystem", "tools"],
        "description": ("OpenClaw local agent CLI.", "OpenClaw 本地 Agent CLI。"),
        "execution": {"binaries": ["openclaw"], "args": ["run", "{prompt}"]},
        "capture": False,
    },
    {
        "agent_type": "aider",
        "display_name": "Aider",
        "executables": ["aider"],
        "version_probe": ["--version"],
        "native_homes": ["~/.aider"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["shell", "filesystem", "tools"],
        "description": ("Aider pair-programming CLI.", "Aider 结对编程 CLI。"),
        "execution": {"binaries": ["aider"], "args": ["--yes", "--message", "{prompt}"]},
        "capture": False,
    },
    {
        "agent_type": "pi",
        "display_name": "Pi",
        "executables": ["pi"],
        "version_probe": ["--version"],
        "native_homes": ["~/.pi", "~/.config/pi"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Pi coding agent; sessions can be imported.", "Pi 编码 Agent；可导入会话并 oneshot 执行。"),
        "execution": {"binaries": ["pi"], "args": ["-p", "{prompt}"]},
        "capture": True,
    },
    {
        "agent_type": "qwen",
        "display_name": "Qwen Code",
        "executables": ["qwen", "qwen-code"],
        "version_probe": ["--version"],
        "native_homes": ["~/.qwen", "~/.config/qwen"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["session_import", "shell", "filesystem", "tools"],
        "description": ("Alibaba Qwen Code command-line agent.", "通义千问 Qwen Code 命令行 Agent。"),
        "execution": {"binaries": ["qwen", "qwen-code"], "args": ["-p", "{prompt}"]},
        "capture": False,
    },
]


def adapter_for(agent_type: str) -> dict[str, Any] | None:
    return next((item for item in AGENT_ADAPTERS if item["agent_type"] == agent_type), None)


def catalog_for_discovery() -> list[dict[str, Any]]:
    """Discovery-facing rows (no secrets, no execution argv)."""
    rows = []
    for item in AGENT_ADAPTERS:
        spec = item.get("execution") or {}
        rows.append({
            "agent_type": item["agent_type"],
            "display_name": item["display_name"],
            "executables": list(item["executables"]),
            "extra_paths": list(item.get("extra_paths") or []),
            "execution_binaries": list(item.get("execution_binaries") or spec.get("binaries") or []),
            "version_probe": list(item.get("version_probe") or ["--version"]),
            "native_homes": list(item.get("native_homes") or []),
            "connection_modes": list(item.get("connection_modes") or ["direct_cli"]),
            "capabilities": list(item.get("capabilities") or []),
            "identity_hint": item.get("identity_hint"),
        })
    return rows


def descriptions() -> dict[str, tuple[str, str]]:
    return {item["agent_type"]: item["description"] for item in AGENT_ADAPTERS}


def capture_supported(agent_type: str) -> bool:
    item = adapter_for(agent_type)
    return bool(item and item.get("capture"))


def execution_binaries(agent_type: str) -> list[str]:
    item = adapter_for(agent_type)
    if not item or not item.get("execution"):
        return []
    return list(item.get("execution_binaries") or item["execution"]["binaries"])


def execution_supported(agent_type: str, executable_path: str | None = None) -> bool:
    """True when this agent has a oneshot adapter and the resolved binary can run it."""
    item = adapter_for(agent_type)
    if not item or not item.get("execution"):
        return False
    allowed = set(execution_binaries(agent_type))
    if not allowed:
        return False
    if not executable_path:
        return False
    return Path(executable_path).name in allowed


def command_template(agent_type: str) -> list[str] | None:
    """Argv template. First token is a binary name resolved at execute time."""
    item = adapter_for(agent_type)
    spec = (item or {}).get("execution")
    if not spec:
        return None
    binaries = spec.get("binaries") or []
    if not binaries:
        return None
    return ["{binary}", *list(spec.get("args") or [])]


def resolve_binary(agent_type: str, preferred: str | None = None) -> str | None:
    item = adapter_for(agent_type)
    if not item:
        return None
    names = execution_binaries(agent_type)
    if preferred and Path(preferred).name in names and Path(preferred).is_file():
        return preferred
    for name in names:
        found = shutil.which(name)
        if found and _binary_matches(item, found):
            return found
    return None


def locate_executable(entry: dict[str, Any]) -> str | None:
    """PATH first, then catalog extra_paths. Bare `agent` must look like Cursor."""
    for name in entry.get("executables") or []:
        found = shutil.which(name)
        if found and _binary_matches(entry, found):
            return found
    for raw in entry.get("extra_paths") or []:
        path = Path(raw).expanduser()
        if path.is_file():
            return str(path)
    return None


def _binary_matches(entry: dict[str, Any], found: str) -> bool:
    hint = str(entry.get("identity_hint") or "")
    if Path(found).name != "agent" or hint != "cursor":
        return True
    try:
        completed = subprocess.run(
            [found, "--version"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "cursor" in f"{completed.stdout} {completed.stderr}".lower()
