#!/usr/bin/env python3
"""Fetch pinned public sources and build a normalized dataset snapshot."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from experience_evaluation.public_dataset import (  # noqa: E402
    build_dataset,
    merge_repository_metadata,
    sha256_file,
)


AGENT_PSYCHOMETRICS_REPO = "https://github.com/dariakryvosheieva/agent-psychometrics.git"
TB21_REPO = "https://github.com/harbor-framework/terminal-bench-2-1.git"
AP_FILES = {
    "swebench-verified": "data/swebench_verified/responses.jsonl",
    "swebench-pro": "data/swebench_pro/responses.jsonl",
    "gso": "data/gso/responses.jsonl",
    "terminal-bench-2.0": "data/terminalbench/responses.jsonl",
}


def _network_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY",
        "all_proxy", "https_proxy", "http_proxy",
    ):
        env.pop(key, None)
    return env


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "experience-evaluation/0.1"})
    try:
        with _opener().open(request, timeout=60) as response:
            content = response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"download failed: {url}: {exc}") from exc
    target.write_bytes(content)


def _git_head(repo: str) -> str:
    completed = subprocess.run(
        ["git", "ls-remote", repo, "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        env=_network_env(),
    )
    return completed.stdout.split()[0]


def _clone_tb21(commit: str, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="experience-eval-tb21-") as tmp:
        checkout = Path(tmp) / "repo"
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                TB21_REPO, str(checkout),
            ],
            check=True,
            capture_output=True,
            env=_network_env(),
        )
        subprocess.run(
            ["git", "-C", str(checkout), "sparse-checkout", "set", "leaderboard/submissions"],
            check=True,
            capture_output=True,
            env=_network_env(),
        )
        actual = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env=_network_env(),
        ).stdout.strip()
        if actual != commit:
            raise RuntimeError(f"TB2.1 changed during fetch: expected {commit}, got {actual}")
        source = checkout / "leaderboard" / "submissions"
        destination.mkdir(parents=True, exist_ok=False)
        for path in sorted(source.glob("*.json")):
            shutil.copy2(path, destination / path.name)


def _build_upstream_identity_map(
    *,
    parser_path: Path,
    verified_path: Path,
    pro_path: Path,
    terminal_path: Path,
    gso_path: Path,
    output_path: Path,
) -> dict[str, dict[str, str]]:
    csv_path = output_path.with_suffix(".csv")
    unsplittable = output_path.with_name("identity_unsplittable.txt")
    subprocess.run(
        [
            sys.executable,
            str(parser_path),
            "--results_jsonl", str(verified_path),
            "--pro_results_jsonl", str(pro_path),
            "--terminal_bench_results_jsonl", str(terminal_path),
            "--output_csv", str(csv_path),
            "--unsplittable_txt", str(unsplittable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    identity_map: dict[str, dict[str, str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            identity_map[row["agent"]] = {
                "model": row["model"],
                "harness": row["scaffold"],
            }
    for line in gso_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        subject = str(row["subject_id"])
        identity_map.setdefault(
            subject,
            {"model": subject, "harness": "OpenHands"},
        )
    output_path.write_text(
        json.dumps(identity_map, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return identity_map


def _github_api_metadata(source_url: str) -> Optional[dict[str, Any]]:
    prefix = "https://github.com/"
    if not source_url.startswith(prefix):
        return None
    path = source_url[len(prefix):].strip("/")
    if path.count("/") != 1:
        return None
    api_url = f"https://api.github.com/repos/{path}"
    request = urllib.request.Request(
        api_url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "experience-evaluation/0.1",
        },
    )
    try:
        with _opener().open(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError):
        return None


def _enrich_catalog(catalog_path: Path, target: Path) -> dict[str, Any]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    metadata: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for harness in catalog.get("harnesses") or []:
        source_url = str(harness.get("source_url") or "")
        if not source_url.startswith("https://github.com/"):
            continue
        value = _github_api_metadata(source_url)
        if value is None:
            errors.append(source_url)
        else:
            metadata[source_url] = value
    catalog["harnesses"] = merge_repository_metadata(catalog.get("harnesses") or [], metadata)
    catalog["repository_metadata_errors"] = errors
    catalog["repository_metadata_retrieved_at"] = datetime.now(timezone.utc).isoformat()
    target.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=date.today().isoformat())
    args = parser.parse_args()

    raw_root = PROJECT_ROOT / "data" / "raw" / args.as_of
    snapshot_root = PROJECT_ROOT / "data" / "snapshots" / args.as_of
    if raw_root.exists() or snapshot_root.exists():
        raise SystemExit(
            f"snapshot already exists for {args.as_of}; use a new date or remove only the generated snapshot explicitly"
        )
    raw_root.mkdir(parents=True)

    ap_commit = _git_head(AGENT_PSYCHOMETRICS_REPO)
    tb_commit = _git_head(TB21_REPO)
    ap_sources: list[dict[str, Any]] = []
    for benchmark_id, relative in AP_FILES.items():
        target = raw_root / "agent-psychometrics" / benchmark_id / "responses.jsonl"
        url = (
            "https://raw.githubusercontent.com/dariakryvosheieva/"
            f"agent-psychometrics/{ap_commit}/{relative}"
        )
        _download(url, target)
        ap_sources.append(
            {
                "path": target,
                "source_id": f"agent-psychometrics-{benchmark_id}-{ap_commit[:12]}",
                "benchmark_id": benchmark_id,
            }
        )

    ap_by_benchmark = {source["benchmark_id"]: Path(source["path"]) for source in ap_sources}
    parser_path = raw_root / "agent-psychometrics" / "split_agents_model_scaffold.py"
    _download(
        "https://raw.githubusercontent.com/dariakryvosheieva/"
        f"agent-psychometrics/{ap_commit}/swebench_irt/split_agents_model_scaffold.py",
        parser_path,
    )
    identity_map_path = raw_root / "agent-psychometrics" / "identity_map.json"
    identity_map = _build_upstream_identity_map(
        parser_path=parser_path,
        verified_path=ap_by_benchmark["swebench-verified"],
        pro_path=ap_by_benchmark["swebench-pro"],
        terminal_path=ap_by_benchmark["terminal-bench-2.0"],
        gso_path=ap_by_benchmark["gso"],
        output_path=identity_map_path,
    )
    for source in ap_sources:
        source["identity_map"] = identity_map

    tb_submissions = raw_root / "terminal-bench-2.1" / "submissions"
    _clone_tb21(tb_commit, tb_submissions)

    enriched_catalog = raw_root / "catalog_enriched.json"
    catalog = _enrich_catalog(PROJECT_ROOT / "config" / "catalog_seed.json", enriched_catalog)

    source_registry = {
        "schema_version": "experience-evaluation-source-registry/v0.1",
        "as_of": args.as_of,
        "sources": [
            {
                "source_id": "agent-psychometrics",
                "title": "Agent Psychometrics public response matrices",
                "repository": "https://github.com/dariakryvosheieva/agent-psychometrics",
                "commit": ap_commit,
                "license": "MIT",
                "status": "downloaded_task_level",
                "files": [
                    {
                        "benchmark_id": source["benchmark_id"],
                        "path": str(source["path"].relative_to(PROJECT_ROOT)),
                        "sha256": sha256_file(Path(source["path"])),
                    }
                    for source in ap_sources
                ],
                "identity_mapping": {
                    "path": str(identity_map_path.relative_to(PROJECT_ROOT)),
                    "sha256": sha256_file(identity_map_path),
                    "upstream_parser_path": str(parser_path.relative_to(PROJECT_ROOT)),
                    "upstream_parser_sha256": sha256_file(parser_path),
                    "resolved_subjects": len(identity_map),
                },
            },
            {
                "source_id": "terminal-bench-2.1-official-submissions",
                "title": "Terminal-Bench 2.1 official verified submissions",
                "repository": "https://github.com/harbor-framework/terminal-bench-2-1",
                "commit": tb_commit,
                "license": "Apache-2.0",
                "status": "downloaded_submission_summary",
                "submission_count": len(list(tb_submissions.glob("*.json"))),
            },
            {
                "source_id": "agent-reward-bench",
                "title": "AgentRewardBench expert-labelled trajectory dataset",
                "repository": "https://huggingface.co/datasets/McGill-NLP/agent-reward-bench",
                "license": "custom_terms_and_third_party_sources",
                "status": "registered_not_downloaded_38gb",
                "purpose": "judge calibration; not a base model/harness response matrix",
            },
            {
                "source_id": "devai-agent-as-a-judge",
                "title": "DevAI tasks, requirements, workspaces and trajectories",
                "repository": "https://github.com/metauto-ai/agent-as-a-judge",
                "status": "registered_not_downloaded",
                "purpose": "trajectory and Evidence-Criterion research",
            },
        ],
        "catalog": {
            "models": len(catalog.get("models") or []),
            "harnesses": len(catalog.get("harnesses") or []),
            "repository_metadata_errors": catalog.get("repository_metadata_errors") or [],
        },
    }
    (raw_root / "source_registry.json").write_text(
        json.dumps(source_registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = build_dataset(
        catalog_path=enriched_catalog,
        agent_psychometrics_sources=ap_sources,
        tb21_submission_dir=tb_submissions,
        output_dir=snapshot_root,
    )
    (snapshot_root / "source_registry.json").write_text(
        json.dumps(source_registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    latest = {
        "schema_version": "experience-evaluation-latest-pointer/v0.1",
        "as_of": args.as_of,
        "snapshot": str(snapshot_root.relative_to(PROJECT_ROOT)),
        "manifest_sha256": sha256_file(snapshot_root / "manifest.json"),
    }
    (PROJECT_ROOT / "data" / "derived" / "latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["coverage"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
