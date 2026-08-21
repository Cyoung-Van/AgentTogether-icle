#!/usr/bin/env python3
"""Build the version-agnostic mainstream Agent specialization dataset."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.b_specialization import build_agent_specializations  # noqa: E402
from experience_evaluation.public_dataset import DatasetError, sha256_file  # noqa: E402


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _github_head(url: str) -> tuple[str | None, str | None]:
    if not url.startswith("https://github.com/"):
        return None, "not_github"
    repo_url = url.rstrip("/") + ".git"
    try:
        completed = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return None, f"git_head_failed:{type(exc).__name__}"
    parts = completed.stdout.split()
    return (parts[0], None) if parts else (None, "empty_git_head")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--agents", type=Path, default=ROOT / "config/mainstream_agents.json")
    parser.add_argument("--rubric", type=Path, default=ROOT / "config/b_specialization_rubric.json")
    parser.add_argument("--replace-output", action="store_true")
    args = parser.parse_args()
    output = ROOT / "data/builtin_b" / args.as_of
    if output.exists() and not args.replace_output:
        raise SystemExit(f"B specialization dataset already exists: {output}")
    agent_seed = json.loads(args.agents.read_text(encoding="utf-8"))
    rubric = json.loads(args.rubric.read_text(encoding="utf-8"))
    checked_at = datetime.now(timezone.utc).isoformat()
    enriched_agents = []
    source_health = []
    for agent in agent_seed.get("agents") or []:
        sources = []
        for source in agent.get("sources") or []:
            commit, error = _github_head(source["url"])
            enriched = {
                **source,
                "checked_at": checked_at,
                "source_commit": commit,
                "source_status": "verified_head" if commit else (
                    "registered_non_git" if error == "not_github" else "check_failed"
                ),
            }
            if error not in (None, "not_github"):
                enriched["source_error"] = error
            sources.append(enriched)
            source_health.append({
                "agent_id": agent["agent_id"],
                "url": source["url"],
                "source_commit": commit,
                "status": enriched["source_status"],
                "error": enriched.get("source_error"),
            })
        enriched_agents.append({**agent, "sources": sources})
    dataset = build_agent_specializations(enriched_agents, rubric)
    output.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output / "agent_families.jsonl", dataset["agents"])
    _write_jsonl(output / "aliases.jsonl", dataset["aliases"])
    _write_jsonl(output / "capability_baselines.jsonl", dataset["cells"])
    _write_json(output / "rubric.json", rubric)
    _write_json(output / "source_health.json", {
        "schema_version": "experience-evaluation-b-source-health/v0.1",
        "as_of": args.as_of,
        "checked_at": checked_at,
        "sources": source_health,
    })
    files = {
        item.name: sha256_file(item)
        for item in sorted(output.iterdir())
        if item.is_file() and item.name != "manifest.json"
    }
    manifest = {
        "schema_version": "experience-evaluation-b-specialization-manifest/v0.1",
        "as_of": args.as_of,
        "generator_version": dataset["generator_version"],
        "agent_family_count": len(dataset["agents"]),
        "capability_cell_count": len(dataset["cells"]),
        "files": files,
        "input_files": {
            "agents": {"path": str(args.agents), "sha256": sha256_file(args.agents)},
            "rubric": {"path": str(args.rubric), "sha256": sha256_file(args.rubric)},
        },
    }
    _write_json(output / "manifest.json", manifest)
    latest = {
        "schema_version": "experience-evaluation-b-specialization-latest/v0.1",
        "as_of": args.as_of,
        "dataset": str(output.relative_to(ROOT)),
        "manifest_sha256": sha256_file(output / "manifest.json"),
    }
    _write_json(ROOT / "data/builtin_b/latest.json", latest)
    print(json.dumps({
        "output": str(output),
        "agent_families": len(dataset["agents"]),
        "aliases": len(dataset["aliases"]),
        "capability_cells": len(dataset["cells"]),
        "verified_git_heads": sum(row["status"] == "verified_head" for row in source_health),
        "source_check_failures": sum(row["status"] == "check_failed" for row in source_health),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
