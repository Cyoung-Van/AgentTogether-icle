#!/usr/bin/env python3
"""Local usage loop: ingest runs, write seven-axis C when A exists, render portraits."""

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
from experience_evaluation.profile import render_profiles  # noqa: E402
from experience_evaluation.public_dataset import DatasetError, sha256_file  # noqa: E402
from experience_evaluation.run_bundle import RunBundleStore, load_run_bundles, to_execution_result  # noqa: E402


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


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--datasets", type=Path, default=ROOT / "data/matrices")
    parser.add_argument("--tasks", type=Path, default=ROOT / "examples/local_usage/task_specs.jsonl")
    parser.add_argument("--results", type=Path, default=ROOT / "examples/local_usage/execution_results.jsonl")
    parser.add_argument("--identities", type=Path, default=ROOT / "examples/local_usage/identities.jsonl")
    parser.add_argument("--config", type=Path, default=ROOT / "examples/local_usage/pipeline_config.json")
    parser.add_argument("--bundles", type=Path)
    parser.add_argument("--bundle-store", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--c-state-config", type=Path, default=ROOT / "config/c_state_config.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/profile_publication_policy.json")
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--source-bundle-id")
    args = parser.parse_args()
    try:
        if args.bundles is not None:
            bundles = load_run_bundles(args.bundles)
            if args.bundle_store is not None:
                RunBundleStore(args.bundle_store).ingest(bundles)
            results = [to_execution_result(bundle) for bundle in bundles]
            write_jsonl(args.results, results)
        else:
            results = read_jsonl(args.results)
        config = json.loads(args.config.read_text(encoding="utf-8"))
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(args.datasets),
            task_specs=read_jsonl(args.tasks),
            execution_results=results,
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
        portraits = render_profiles(
            store,
            json.loads(args.policy.read_text(encoding="utf-8")),
        )
        write_json(args.profile, portraits)
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"local usage failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "store": str(args.store),
        "profile": str(args.profile),
        "summary": pipeline["summary"],
        "c_state_update": {
            "updated": update["updated"],
            "rejected": update["rejected"],
            "duplicates": update["duplicates"],
        },
        "portraits": {
            "subject_count": portraits["subject_count"],
            "publication_statuses": [row["publication_status"] for row in portraits["portraits"]],
        },
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
