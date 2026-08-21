#!/usr/bin/env python3
"""Calibrate current Matrix A onto canonical logit and refresh snapshot hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.a_calibration import calibrate_a_baselines  # noqa: E402
from experience_evaluation.public_dataset import DatasetError  # noqa: E402


def read_jsonl(path: Path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def remanifest(root: Path) -> dict:
    manifest = {
        "schema_version": "experience-evaluation-ab-manifest/v0.1",
        "as_of": root.name,
        "files": {},
    }
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][str(path.relative_to(root))] = file_hash(path)
    write_json(root / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--contract",
        type=Path,
        default=ROOT / "config/a_calibration_contract.json",
    )
    args = parser.parse_args()
    matrix_root = ROOT / "data" / "matrices" / args.as_of
    observations = read_jsonl(
        matrix_root / "A_models/current/model_benchmark_observations.jsonl"
    )
    if not observations:
        raise DatasetError(f"no current A observations under {matrix_root}")
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    report = calibrate_a_baselines(observations, contract, as_of=args.as_of)
    write_jsonl(matrix_root / "A_models/current/capability_baselines.jsonl", report["cells"])
    write_json(matrix_root / "A_models/current/calibration_report.json", {
        key: value for key, value in report.items() if key != "cells"
    })
    freshness_path = matrix_root / "freshness_summary.json"
    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    freshness.setdefault("A", {})
    freshness["A"]["current_capability_baselines"] = report["cell_count"]
    freshness["A"]["calibration_contract_id"] = report["contract_id"]
    write_json(freshness_path, freshness)
    remanifest(matrix_root)
    print(json.dumps({
        "as_of": args.as_of,
        "contract_id": report["contract_id"],
        "model_config_count": report["model_config_count"],
        "cell_count": report["cell_count"],
        "rejected_groups": len(report["rejected_groups"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
