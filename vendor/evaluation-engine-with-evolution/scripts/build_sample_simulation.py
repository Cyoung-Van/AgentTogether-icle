#!/usr/bin/env python3
"""Build the first physically separated observed-sample/simulation experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.sample_simulation import (  # noqa: E402
    build_typical_sample_dataset,
    write_sample_simulation_experiment,
)
from experience_evaluation.public_dataset import sha256_file  # noqa: E402


DEFAULT_HARNESSES = [
    "claude-code",
    "camel-ai",
    "mini-swe-agent",
    "openhands",
    "goose",
    "terminus-2",
]


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def snapshot_content_hash(snapshot: Path) -> str:
    digest = hashlib.sha256()
    for name in ("agent_model_observations.jsonl", "task_outcomes.jsonl"):
        digest.update(name.encode("utf-8"))
        digest.update(sha256_file(snapshot / name).encode("ascii"))
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--experiment-id", default="tb20-claude-sonnet-4-5-six-harness-v2")
    parser.add_argument("--benchmark-id", default="terminal-bench-2.0")
    parser.add_argument("--model-id", default="anthropic-claude-sonnet-4-5")
    parser.add_argument("--harness", action="append", dest="harnesses")
    parser.add_argument("--max-tasks", type=int)
    parser.add_argument("--bootstrap-replicates", type=int, default=100)
    parser.add_argument("--parametric-replicates", type=int, default=100)
    parser.add_argument("--known-parameter-replicates", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260819)
    args = parser.parse_args()

    snapshot = ROOT / "data" / "snapshots" / args.as_of
    output_root = ROOT / "data" / "experiments" / args.as_of / args.experiment_id
    observations = read_jsonl(snapshot / "agent_model_observations.jsonl")
    outcomes = read_jsonl(snapshot / "task_outcomes.jsonl")
    harnesses = args.harnesses or DEFAULT_HARNESSES
    sample = build_typical_sample_dataset(
        observations,
        outcomes,
        benchmark_id=args.benchmark_id,
        model_id=args.model_id,
        harness_ids=harnesses,
        max_tasks=args.max_tasks,
        source_snapshot_sha256=snapshot_content_hash(snapshot),
    )
    result = write_sample_simulation_experiment(
        output_root,
        sample,
        bootstrap_replicates=args.bootstrap_replicates,
        parametric_replicates=args.parametric_replicates,
        known_parameter_replicates=args.known_parameter_replicates,
        seed=args.seed,
    )
    print(json.dumps({
        **result,
        "benchmark_id": args.benchmark_id,
        "model_id": args.model_id,
        "harnesses": harnesses,
        "common_task_count": sample["statistics"]["common_task_count"],
        "fit_status": sample["statistics"]["fit_status"],
        "selection_rule": "all unique task-level observations matching benchmark_id and model_id",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
