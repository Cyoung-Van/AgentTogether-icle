"""v0.4 P9 (Batch 1) tests: local agent discovery, safe probe, doctor, linking.

Uses an injected catalog + fake executables in a temp PATH — no real agent
CLIs are touched and no credentials are read.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from icle.discovery import (  # noqa: E402
    AGENT_CATALOG,
    DiscoveryError,
    doctor_agents,
    link_agent,
    link_all_agents,
    probe_version,
    scan_agents,
    unlink_agent,
)

# A minimal injectable catalog: two agents, one with a fake executable.
TEST_CATALOG = [
    {
        "agent_type": "kimi",
        "display_name": "Kimi Code",
        "executables": ["kimi"],
        "version_probe": ["--version"],
        "native_homes": ["~/fake-kimi-home"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["shell"],
    },
    {
        "agent_type": "hermes",
        "display_name": "Hermes",
        "executables": ["hermes"],
        "version_probe": ["--version"],
        "native_homes": ["~/fake-hermes-home"],
        "connection_modes": ["direct_cli"],
        "capabilities": ["shell"],
    },
]


def make_fake_bin(root: Path, name: str, output: str = "9.9.9-test") -> Path:
    """Create an executable fake CLI that answers --version."""
    path = root / name
    path.write_text(f"#!/bin/sh\necho '{output}'\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = self.root / "store"
        self.store.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.old_path = os.environ.get("PATH", "")

    def tearDown(self) -> None:
        os.environ["PATH"] = self.old_path

    def _with_path(self, agent: str, version: str = "9.9.9-test") -> None:
        make_fake_bin(self.bin, agent, version)
        # isolate PATH to the fake bin dir so the scan never picks up REAL
        # agent CLIs installed on this machine (hermes/kimi/claude…)
        os.environ["PATH"] = str(self.bin)

    def test_scan_detects_executable_via_path(self) -> None:
        self._with_path("kimi")
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False)
        by_type = {a["agent_type"]: a for a in agents}
        self.assertEqual(by_type["kimi"]["status"], "runnable")
        self.assertEqual(Path(by_type["kimi"]["executable_path"]).name, "kimi")
        self.assertIn("PATH", by_type["kimi"]["detection_sources"])
        self.assertTrue(by_type["kimi"]["execution_supported"])
        self.assertTrue(by_type["kimi"]["capture_supported"])
        # hermes has no executable in PATH → unavailable
        self.assertEqual(by_type["hermes"]["status"], "unavailable")
        self.assertIsNone(by_type["hermes"]["executable_path"])

    def test_scan_probes_version_and_records_errors(self) -> None:
        self._with_path("kimi")
        agents = scan_agents(self.store, catalog=TEST_CATALOG)
        kimi = next(a for a in agents if a["agent_type"] == "kimi")
        self.assertEqual(kimi["version"], "9.9.9-test")
        self.assertEqual(kimi["probe_errors"], [])
        self.assertIsNotNone(kimi["last_probe"])

    def test_scan_broken_probe_marks_attention(self) -> None:
        # executable that fails its --version probe
        path = self.bin / "kimi"
        path.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        os.environ["PATH"] = str(self.bin)  # isolate from real agent CLIs
        agents = scan_agents(self.store, catalog=TEST_CATALOG)
        kimi = next(a for a in agents if a["agent_type"] == "kimi")
        self.assertIsNone(kimi["version"])
        self.assertTrue(kimi["probe_errors"])
        doctor = doctor_agents(self.store, catalog=TEST_CATALOG)
        kimi_row = next(r for r in doctor["agents"] if r["agent_type"] == "kimi")
        self.assertTrue(kimi_row["probe_errors"])
        self.assertGreaterEqual(doctor["summary"]["needs_attention"], 1)

    def test_probe_version_refuses_unsafe_probe(self) -> None:
        version, error = probe_version("/bin/echo", ["; rm -rf /"])
        self.assertIsNone(version)
        self.assertIn("unsafe", error)

    def test_link_unlink_persists(self) -> None:
        self._with_path("kimi")
        link_agent(self.store, "kimi")
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False)
        kimi = next(a for a in agents if a["agent_type"] == "kimi")
        # linked status survives rescan (discovery ≠ authorization, user chose)
        self.assertEqual(kimi["status"], "linked")
        unlink_agent(self.store, "kimi")
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False)
        kimi = next(a for a in agents if a["agent_type"] == "kimi")
        self.assertEqual(kimi["status"], "runnable")

    def test_link_unknown_agent_rejected(self) -> None:
        with self.assertRaises(DiscoveryError):
            link_agent(self.store, "not-an-agent")

    def test_doctor_summary_and_ready_count(self) -> None:
        self._with_path("kimi")
        doctor = doctor_agents(self.store, catalog=TEST_CATALOG)
        self.assertEqual(doctor["summary"]["total"], 2)
        self.assertEqual(doctor["summary"]["ready"], 1)
        kimi_row = next(r for r in doctor["agents"] if r["agent_type"] == "kimi")
        self.assertTrue(kimi_row["checks"]["executable"])
        self.assertTrue(kimi_row["checks"]["version"])
        # native home is a fake path → not present, recorded as False, no crash
        self.assertFalse(kimi_row["checks"]["native_home"])

    def test_session_capture_count(self) -> None:
        self._with_path("kimi")
        capture = self.root / "capture-store" / "kimi"
        (capture / "session-1").mkdir(parents=True)
        (capture / "session-2").mkdir(parents=True)
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False,
                             capture_store=self.root / "capture-store")
        kimi = next(a for a in agents if a["agent_type"] == "kimi")
        self.assertEqual(kimi["session_capture_count"], 2)

    def test_default_catalog_covers_gate(self) -> None:
        """§13 exit gate: Hermes/Kimi/Codex/Claude Code must be in the catalog."""
        types = {entry["agent_type"] for entry in AGENT_CATALOG}
        for required in ("hermes", "kimi", "codex", "claude", "cursor"):
            self.assertIn(required, types)

    def test_link_all_links_only_runnable(self) -> None:
        """一键链接:只链接可运行的,未安装的跳过,单个仍可 unlink。"""
        self._with_path("kimi")  # only kimi is runnable in the isolated PATH
        result = link_all_agents(self.store)
        self.assertEqual(result["linked"], ["kimi"])
        self.assertEqual(result["count"], 1)
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False)
        by_type = {a["agent_type"]: a for a in agents}
        self.assertEqual(by_type["kimi"]["status"], "linked")
        self.assertEqual(by_type["hermes"]["status"], "unavailable")
        # user can still unlink a single agent afterwards
        unlink_agent(self.store, "kimi")
        agents = scan_agents(self.store, catalog=TEST_CATALOG, probe=False)
        by_type = {a["agent_type"]: a for a in agents}
        self.assertEqual(by_type["kimi"]["status"], "runnable")


if __name__ == "__main__":
    unittest.main()
