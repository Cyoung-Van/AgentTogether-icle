#!/usr/bin/env python3
"""Run a local usage A/B/J/C pass and absorb available C cells into CStateStore."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.c_state import CStateStore  # noqa: E402
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
        row = json.loads(line)
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
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--c-state-config", type=Path, default=ROOT / "config/c_state_config.json")
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--source-bundle-id")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(args.datasets),
            task_specs=read_jsonl(args.tasks),
            execution_results=read_jsonl(args.results),
            subject_identities=read_jsonl(args.identities),
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
        store_config = json.loads(args.c_state_config.read_text(encoding="utf-8"))
        store = CStateStore(args.store, store_config)
        update = store.update(
            pipeline["c_matrix"],
            observed_at=args.observed_at,
            source_bundle_id=args.source_bundle_id or args.output.name,
        )
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"usage update failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "store": str(args.store),
        "summary": pipeline["summary"],
        "c_state_update": {
            "updated": update["updated"],
            "rejected": update["rejected"],
            "duplicates": update["duplicates"],
        },
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
