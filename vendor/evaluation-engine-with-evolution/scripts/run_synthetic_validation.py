#!/usr/bin/env python3
"""Run the synthetic known-parameter A/B/J/C validation path."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.public_dataset import DatasetError, sha256_file  # noqa: E402
from experience_evaluation.synthetic_validation import (  # noqa: E402
    load_known_parameter_responses,
    run_synthetic_abjc_validation,
    write_synthetic_validation_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument("--known-parameters", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--replicate", type=int, default=0,
        help="Known-parameter replicate to validate; use -1 to pool all replicates as repeated attempts",
    )
    parser.add_argument("--bootstrap-replicates", type=int, default=500)
    parser.add_argument("--bootstrap-seed", type=int, default=20260820)
    args = parser.parse_args()
    try:
        responses = load_known_parameter_responses(
            args.responses,
            simulation_replicate=None if args.replicate < 0 else args.replicate,
        )
        known_parameters = json.loads(
            args.known_parameters.read_text(encoding="utf-8")
        )
        if not isinstance(known_parameters, dict):
            raise DatasetError("known parameters must be a JSON object")
        validation = run_synthetic_abjc_validation(
            response_rows=responses,
            known_parameters=known_parameters,
            bootstrap_replicates=args.bootstrap_replicates,
            bootstrap_seed=args.bootstrap_seed,
        )
        manifest = write_synthetic_validation_bundle(
            args.output,
            validation,
            input_files={
                "responses": {
                    "path": str(args.responses),
                    "sha256": sha256_file(args.responses),
                    "simulation_replicate": "all" if args.replicate < 0 else str(args.replicate),
                },
                "known_parameters": {
                    "path": str(args.known_parameters),
                    "sha256": sha256_file(args.known_parameters),
                },
            },
        )
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"synthetic validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "summary": validation["validation_report"],
        "manifest": manifest,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
