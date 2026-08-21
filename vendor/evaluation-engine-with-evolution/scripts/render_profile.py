#!/usr/bin/env python3
"""Render a read-only subject portrait from C state. No total score."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.c_state import CStateStore  # noqa: E402
from experience_evaluation.profile import render_profiles  # noqa: E402
from experience_evaluation.public_dataset import DatasetError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--c-state-config", type=Path, default=ROOT / "config/c_state_config.json")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/profile_publication_policy.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subject-id", action="append")
    args = parser.parse_args()
    try:
        store_config = json.loads(args.c_state_config.read_text(encoding="utf-8"))
        policy = json.loads(args.policy.read_text(encoding="utf-8"))
        store = CStateStore(args.store, store_config, read_only=True)
        bundle = render_profiles(store, policy, subject_ids=args.subject_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, DatasetError) as exc:
        print(f"profile render failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(args.output),
        "subject_count": bundle["subject_count"],
        "publication_statuses": [row["publication_status"] for row in bundle["portraits"]],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
