"""Move rebuild-only dumps out of a snapshot's distributed file set.

The digest stays in the manifest under ``audit_artifacts`` so a re-downloaded
copy is still verifiable. Runtime never reads these files; only the build
scripts that produced them do.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MATRIX_AUDIT_ARTIFACTS = (
    "B_harnesses/rejected_comparisons.jsonl",
    "B_harnesses/crossed_task_outcomes.jsonl",
    "B_harnesses/provisional_same_label_blocks.jsonl",
    "B_harnesses/history/provisional_same_label_blocks.jsonl",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/matrices/2026-08-19")
    parser.add_argument("--produced-by", default="scripts/build_ab_datasets.py")
    parser.add_argument("--delete", action="store_true", help="remove the local copies")
    args = parser.parse_args()

    manifest_path = args.snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or {}
    artifacts = manifest.get("audit_artifacts") or {}

    for relative in MATRIX_AUDIT_ARTIFACTS:
        path = args.snapshot / relative
        if relative in artifacts:
            print(f"already recorded: {relative}")
        elif path.is_file():
            artifacts[relative] = {
                "sha256": files.get(relative) or sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": count_rows(path),
                "produced_by": args.produced_by,
                "distribution": "rebuild_only_not_in_git",
            }
            print(f"recorded: {relative}")
        else:
            print(f"missing, cannot record: {relative}")
            continue
        files.pop(relative, None)
        if args.delete and path.is_file():
            path.unlink()
            print(f"deleted: {relative}")

    manifest["files"] = files
    manifest["audit_artifacts"] = artifacts
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"distributed files: {len(files)}  audit artifacts: {len(artifacts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
