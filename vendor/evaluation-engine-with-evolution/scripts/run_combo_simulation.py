#!/usr/bin/env python3
"""Simulate common LLM+agent task results and project J from evaluation atoms."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.combo_simulation import simulate_combo_tasks, write_combo_simulation


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument(
        "--a",
        type=Path,
        default=ROOT / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl",
    )
    parser.add_argument(
        "--b",
        type=Path,
        default=ROOT / "data/builtin_b/2026-08-21/capability_baselines.jsonl",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "data/generated/2026-08-21/combo-task-sim")
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--noise-sd", type=float, default=0.12)
    args = parser.parse_args()
    report = simulate_combo_tasks(
        config_dir=args.config_dir,
        a_path=args.a,
        b_path=args.b,
        seed=args.seed,
        noise_sd=args.noise_sd,
    )
    paths = write_combo_simulation(report, args.output)
    print(json.dumps({
        "output": str(args.output),
        "combo_count": report["combo_count"],
        "task_count": report["task_count"],
        "checks": report["checks"],
        "files": {key: str(path) for key, path in paths.items()},
    }, ensure_ascii=False, indent=2))
    return 0 if report["checks"]["expected_effects_hold"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
