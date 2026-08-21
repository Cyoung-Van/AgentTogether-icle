#!/usr/bin/env python3
"""Ingest immutable RunBundles from an external runner. Does not execute Agents."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.public_dataset import DatasetError  # noqa: E402
from experience_evaluation.run_bundle import RunBundleStore, load_run_bundles, to_execution_result  # noqa: E402


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundles", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    try:
        bundles = load_run_bundles(args.bundles)
        store = RunBundleStore(args.store)
        ingest = store.ingest(bundles)
        results = [to_execution_result(bundle) for bundle in bundles]
        write_jsonl(args.results, results)
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"run bundle ingest failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "store": str(args.store),
        "results": str(args.results),
        "accepted": ingest["accepted"],
        "duplicates": ingest["duplicates"],
        "result_count": len(results),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
