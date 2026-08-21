#!/usr/bin/env python3
"""Incrementally update or verify a persistent C correction-state store."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.c_state import CStateStore  # noqa: E402
from experience_evaluation.public_dataset import DatasetError  # noqa: E402


def read_jsonl(path: Path):
    rows = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSONL {path}:{lineno}") from exc
        if not isinstance(row, dict):
            raise DatasetError(f"JSONL row must be object: {path}:{lineno}")
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=ROOT / "config/c_state_config.json")
    parser.add_argument("--c-matrix", type=Path)
    parser.add_argument("--observed-at")
    parser.add_argument("--source-bundle-id")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        store = CStateStore(args.store, config, read_only=args.verify_only)
        if args.verify_only:
            output = {
                "verification": store.verify_and_rebuild(rebuild_projection=False),
                "states": store.list_states(),
            }
        else:
            if not args.c_matrix or not args.observed_at or not args.source_bundle_id:
                raise DatasetError(
                    "update requires --c-matrix, --observed-at and --source-bundle-id"
                )
            output = {
                "update": store.update(
                    read_jsonl(args.c_matrix),
                    observed_at=args.observed_at,
                    source_bundle_id=args.source_bundle_id,
                ),
                "states": store.list_states(),
            }
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"C state operation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
