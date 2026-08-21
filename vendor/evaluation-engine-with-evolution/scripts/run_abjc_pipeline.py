#!/usr/bin/env python3
"""Auto-discover built-in A/B, generate J, and infer C when gates permit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.matrix_pipeline import (  # noqa: E402
    BuiltinDatasetRegistry,
    run_matrix_pipeline,
    write_pipeline_bundle,
)
from experience_evaluation.public_dataset import DatasetError, sha256_file  # noqa: E402


def read_jsonl(path: Path):
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSONL {path}:{lineno}: {exc}") from exc
        if not isinstance(row, dict):
            raise DatasetError(f"JSONL row must be an object: {path}:{lineno}")
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, default=ROOT / "data/matrices")
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--identities", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        tasks = read_jsonl(args.tasks)
        results = read_jsonl(args.results)
        identities = read_jsonl(args.identities)
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise DatasetError("pipeline config must be a JSON object")
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(args.datasets),
            task_specs=tasks,
            execution_results=results,
            subject_identities=identities,
            pipeline_config=config,
        )
        manifest = write_pipeline_bundle(
            args.output,
            pipeline,
            pipeline_config=config,
            input_files={
                "tasks": {"path": str(args.tasks), "sha256": sha256_file(args.tasks)},
                "results": {"path": str(args.results), "sha256": sha256_file(args.results)},
                "identities": {"path": str(args.identities), "sha256": sha256_file(args.identities)},
                "config": {"path": str(args.config), "sha256": sha256_file(args.config)},
            },
        )
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"A/B/J/C pipeline failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "summary": pipeline["summary"],
        "identity_resolution": pipeline["identity_resolution"],
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
