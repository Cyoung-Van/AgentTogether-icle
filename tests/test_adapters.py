"""Local Agent adapter catalog: discovery + oneshot execution templates."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.adapters import (  # noqa: E402
    AGENT_ADAPTERS,
    command_template,
    execution_supported,
    locate_executable,
)
from icle.discovery import AGENT_CATALOG, scan_agents  # noqa: E402
from icle.replay import DIRECT_CLI_AGENTS, make_direct_executor  # noqa: E402


def _exec(path: Path, output: str = "9.9.9-test") -> Path:
    path.write_text(f"#!/bin/sh\necho '{output}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class AdapterCatalogTests(unittest.TestCase):
    def test_catalog_covers_gate_and_cursor(self) -> None:
        types = {item["agent_type"] for item in AGENT_ADAPTERS}
        for required in ("hermes", "kimi", "codex", "claude", "cursor"):
            self.assertIn(required, types)
        self.assertEqual(
            {item["agent_type"] for item in AGENT_CATALOG},
            types,
        )

    def test_every_adapter_has_oneshot_template(self) -> None:
        for item in AGENT_ADAPTERS:
            template = command_template(item["agent_type"])
            self.assertIsNotNone(template, item["agent_type"])
            self.assertEqual(template[0], "{binary}")
            self.assertIn("{prompt}", template)
            self.assertIn(item["agent_type"], DIRECT_CLI_AGENTS)
            make_direct_executor(item["agent_type"])

    def test_editor_cursor_is_detected_but_not_executable(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        editor = _exec(root / "cursor", "Cursor 3.17.8")
        catalog = [
            {
                "agent_type": "cursor",
                "display_name": "Cursor",
                "executables": ["cursor-agent", "agent"],
                "extra_paths": [str(editor)],
                "execution_binaries": ["cursor-agent", "agent"],
                "version_probe": ["--version"],
                "native_homes": [],
                "connection_modes": ["direct_cli"],
                "capabilities": [],
            }
        ]
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(root / "empty-bin")
            (root / "empty-bin").mkdir()
            agents = scan_agents(store, catalog=catalog, probe=False)
        finally:
            os.environ["PATH"] = old
        row = agents[0]
        self.assertEqual(row["executable_path"], str(editor))
        self.assertEqual(row["status"], "runnable")
        self.assertFalse(row["execution_supported"])
        self.assertFalse(execution_supported("cursor", str(editor)))

    def test_cursor_agent_cli_marks_execution_supported(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        bindir = root / "bin"
        bindir.mkdir()
        _exec(bindir / "cursor-agent", "2026.01.28-cursor")
        catalog = [
            {
                "agent_type": "cursor",
                "display_name": "Cursor",
                "executables": ["cursor-agent"],
                "extra_paths": [],
                "execution_binaries": ["cursor-agent", "agent"],
                "version_probe": ["--version"],
                "native_homes": [],
                "connection_modes": ["direct_cli"],
                "capabilities": [],
            }
        ]
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(bindir)
            found = locate_executable(catalog[0])
            agents = scan_agents(store, catalog=catalog, probe=False)
        finally:
            os.environ["PATH"] = old
        self.assertTrue(found and found.endswith("cursor-agent"))
        self.assertTrue(agents[0]["execution_supported"])

    def test_native_home_without_binary_is_detected(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = root / "store"
        store.mkdir()
        home = root / "cursor-home"
        home.mkdir()
        catalog = [
            {
                "agent_type": "cursor",
                "display_name": "Cursor",
                "executables": ["cursor-agent"],
                "extra_paths": [],
                "execution_binaries": ["cursor-agent"],
                "version_probe": ["--version"],
                "native_homes": [str(home)],
                "connection_modes": ["direct_cli"],
                "capabilities": [],
            }
        ]
        old = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = str(root / "empty-bin")
            (root / "empty-bin").mkdir()
            agents = scan_agents(store, catalog=catalog, probe=False)
        finally:
            os.environ["PATH"] = old
        self.assertEqual(agents[0]["status"], "detected")
        self.assertFalse(agents[0]["execution_supported"])


if __name__ == "__main__":
    unittest.main()
