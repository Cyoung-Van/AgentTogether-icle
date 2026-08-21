#!/usr/bin/env python3
"""Generate an auditable evaluation matrix from TaskSpecs and execution results."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.evaluation_matrix import (  # noqa: E402
    generate_evaluation_matrix,
    write_evaluation_bundle,
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
    parser.add_argument("--tasks", type=Path, required=True, help="Versioned TaskSpec JSONL")
    parser.add_argument("--results", type=Path, required=True, help="ExecutionResult JSONL")
    parser.add_argument("--config", type=Path, required=True, help="Generator config JSON")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()

    try:
        task_specs = read_jsonl(args.tasks)
        execution_results = read_jsonl(args.results)
        generator_config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(generator_config, dict):
            raise DatasetError("generator config must be a JSON object")
        bundle = generate_evaluation_matrix(task_specs, execution_results, generator_config)
        manifest = write_evaluation_bundle(
            args.output,
            bundle,
            generator_config=generator_config,
            input_files={
                "tasks": {"path": str(args.tasks), "sha256": sha256_file(args.tasks)},
                "results": {"path": str(args.results), "sha256": sha256_file(args.results)},
                "config": {"path": str(args.config), "sha256": sha256_file(args.config)},
            },
        )
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"matrix generation failed: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "output": str(args.output),
        "summary": bundle["summary"],
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
