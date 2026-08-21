#!/usr/bin/env python3
"""Run atom-scored tasks through J→C and write one portrait per agent."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.c_state import CStateStore
from experience_evaluation.combo_simulation import (
    combo_identities,
    combo_task_specs,
    simulate_combo_tasks,
    write_combo_simulation,
)
from experience_evaluation.matrix_pipeline import (
    BuiltinDatasetRegistry,
    run_matrix_pipeline,
    write_pipeline_bundle,
)
from experience_evaluation.profile import render_profiles
from experience_evaluation.public_dataset import DatasetError, sha256_file


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
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--pipeline-config", type=Path, default=ROOT / "examples/atom_runtime/pipeline_config.json")
    parser.add_argument("--datasets", type=Path, default=ROOT / "data/matrices")
    parser.add_argument("--output", type=Path, default=ROOT / "data/generated/2026-08-21/atom-runtime")
    parser.add_argument("--store", type=Path, default=ROOT / "data/generated/2026-08-21/atom-runtime-c-state")
    parser.add_argument("--observed-at", default="2026-08-21T04:32:00+00:00")
    args = parser.parse_args()
    try:
        report = simulate_combo_tasks(
            config_dir=args.config_dir,
            a_path=ROOT / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl",
            b_path=ROOT / "data/builtin_b/2026-08-21/capability_baselines.jsonl",
        )
        work = args.output
        if work.exists():
            raise DatasetError(f"atom runtime bundle already exists: {work}")
        work.mkdir(parents=True)
        write_combo_simulation(report, work / "simulation")
        tasks = combo_task_specs()
        identities = combo_identities()
        write_jsonl(work / "task_specs.jsonl", tasks)
        write_jsonl(work / "identities.jsonl", identities)
        write_jsonl(work / "execution_results.jsonl", report["execution_results"])
        config = json.loads(args.pipeline_config.read_text(encoding="utf-8"))
        existing = config["generator"].get("atom_projection") or {}
        config["generator"]["atom_projection"] = {
            **existing,
            "enabled": True,
            "config_dir": str(args.config_dir),
        }
        pipeline = run_matrix_pipeline(
            registry=BuiltinDatasetRegistry(args.datasets),
            task_specs=tasks,
            execution_results=report["execution_results"],
            subject_identities=identities,
            pipeline_config=config,
        )
        manifest = write_pipeline_bundle(
            work / "pipeline",
            pipeline,
            pipeline_config=config,
            input_files={
                "tasks": {"path": str(work / "task_specs.jsonl"), "sha256": sha256_file(work / "task_specs.jsonl")},
                "results": {
                    "path": str(work / "execution_results.jsonl"),
                    "sha256": sha256_file(work / "execution_results.jsonl"),
                },
                "identities": {
                    "path": str(work / "identities.jsonl"),
                    "sha256": sha256_file(work / "identities.jsonl"),
                },
            },
        )
        store = CStateStore(
            args.store,
            json.loads((ROOT / "config/c_state_config.json").read_text(encoding="utf-8")),
        )
        update = store.update(
            pipeline["c_matrix"],
            observed_at=args.observed_at,
            source_bundle_id=work.name,
        )
        portraits = render_profiles(
            store,
            json.loads((ROOT / "config/profile_publication_policy.json").read_text(encoding="utf-8")),
        )
        write_json(work / "portraits.json", portraits)
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"atom runtime failed: {exc}", file=sys.stderr)
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
        "portraits": {
            "subject_count": portraits["subject_count"],
            "subjects": [row["subject_id"] for row in portraits["portraits"]],
            "publication_statuses": [row["publication_status"] for row in portraits["portraits"]],
            "observed_axis_counts": [row["observed_axis_count"] for row in portraits["portraits"]],
        },
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    portraits_ready = (
        pipeline["summary"]["c_available_cells"] == 42
        and update["updated"] == 42
        and portraits["subject_count"] == 6
        and all(row["observed_axis_count"] == 7 for row in portraits["portraits"])
        and all(row["publication_status"] == "publishable_local_portrait" for row in portraits["portraits"])
    )
    return 0 if portraits_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
