#!/usr/bin/env python3
"""Create physically separate Matrix A and Matrix B datasets."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.public_dataset import (  # noqa: E402
    build_b_matched_dataset,
    parse_livebench_subtasks,
    stable_record_id,
)
from experience_evaluation.a_calibration import calibrate_a_baselines  # noqa: E402
from experience_evaluation.freshness import (  # noqa: E402
    classify_latest_records,
    link_current_model_configs,
)


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--freshness-policy",
        type=Path,
        default=ROOT / "config" / "freshness_policy.json",
    )
    parser.add_argument(
        "--replace-output",
        action="store_true",
        help="Regenerate derived A/B files in an existing output directory.",
    )
    parser.add_argument(
        "--a-calibration-contract",
        type=Path,
        default=ROOT / "config" / "a_calibration_contract.json",
    )
    args = parser.parse_args()
    freshness_policy = json.loads(args.freshness_policy.read_text(encoding="utf-8"))
    snapshot = ROOT / "data" / "snapshots" / args.as_of
    raw = ROOT / "data" / "raw" / args.as_of
    out = ROOT / "data" / "matrices" / args.as_of
    if out.exists() and not args.replace_output:
        raise SystemExit(f"A/B dataset already exists: {out}")

    models = read_jsonl(snapshot / "model_catalog.jsonl")
    harnesses = read_jsonl(snapshot / "harness_catalog.jsonl")
    observations = read_jsonl(snapshot / "agent_model_observations.jsonl")
    outcomes = read_jsonl(snapshot / "task_outcomes.jsonl")

    a_records = parse_livebench_subtasks(
        raw / "livebench" / "table_2026_06_25.csv",
        raw / "livebench" / "categories_2026_06_25.json",
        source_id="livebench-2026-06-25",
    )
    a_dir = out / "A_models"
    write_jsonl(a_dir / "model_catalog.jsonl", models)
    write_jsonl(a_dir / "model_benchmark_observations.jsonl", a_records)
    source_configs = []
    seen_configs = set()
    for row in a_records:
        if row["source_model_id"] in seen_configs:
            continue
        seen_configs.add(row["source_model_id"])
        source_configs.append({
            "schema_version": "experience-evaluation-source-model-config/v0.1",
            "source_id": row["source_id"],
            "source_model_id": row["source_model_id"],
            "normalized_model_config_id": row["model_id"],
            "identity_status": "source_exact_unlinked_to_official_catalog",
        })
    write_jsonl(a_dir / "source_model_configs.jsonl", source_configs)

    linked_configs = link_current_model_configs(source_configs, models)
    linked_by_id = {
        row["normalized_model_config_id"]: row for row in linked_configs
    }
    a_freshness = classify_latest_records(
        a_records,
        key_fields=("benchmark_id", "benchmark_item_id", "model_id"),
        as_of=args.as_of,
        max_age_days=int(freshness_policy["a_max_age_days"]),
        date_fields=("result_date", "observed_at", "source_id"),
        policy_id=freshness_policy["policy_id"],
    )
    current_a_records = []
    historical_a_records = list(a_freshness["history"])
    for row in a_freshness["current"]:
        config = linked_by_id.get(row["model_id"])
        if config and config.get("catalog_link_status") == "linked_current_catalog":
            current_a_records.append({**row, "base_model_id": config["base_model_id"]})
        else:
            historical_a_records.append({
                **row,
                "freshness_status": "historical_reference",
                "freshness_reason": "model_config_not_linked_to_current_catalog",
            })
    current_config_ids = {row["model_id"] for row in current_a_records}
    current_configs = [
        {**row, "freshness_status": "active_current"}
        for row in linked_configs
        if row["normalized_model_config_id"] in current_config_ids
    ]
    historical_configs = [
        {
            **row,
            "freshness_status": "historical_reference",
            "freshness_reason": "no_active_current_observation_or_catalog_link",
        }
        for row in linked_configs
        if row["normalized_model_config_id"] not in current_config_ids
    ]
    a_current_dir = a_dir / "current"
    a_history_dir = a_dir / "history"
    write_jsonl(a_current_dir / "source_model_configs.jsonl", current_configs)
    write_jsonl(a_current_dir / "model_benchmark_observations.jsonl", current_a_records)
    a_contract = json.loads(args.a_calibration_contract.read_text(encoding="utf-8"))
    a_calibration = calibrate_a_baselines(
        current_a_records, a_contract, as_of=args.as_of
    )
    write_jsonl(a_current_dir / "capability_baselines.jsonl", a_calibration["cells"])
    (a_current_dir / "calibration_report.json").write_text(
        json.dumps(
            {key: value for key, value in a_calibration.items() if key != "cells"},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    write_jsonl(a_history_dir / "source_model_configs.jsonl", historical_configs)
    write_jsonl(a_history_dir / "model_benchmark_observations.jsonl", historical_a_records)

    resolved = [r for r in observations if r.get("identity_status") != "unresolved"]
    resolved_ids = {r["observation_id"] for r in resolved}
    b_outcomes = [r for r in outcomes if r["observation_id"] in resolved_ids]
    edges = []
    seen = set()
    for row in resolved:
        key = (row["agent_id"], row["model_id"], row["benchmark_id"], row.get("reasoning_effort"))
        if key in seen:
            continue
        seen.add(key)
        edges.append({
            "schema_version": "experience-evaluation-matrix-b-edge/v0.1",
            "edge_id": stable_record_id("b-edge", *key),
            "matrix": "B",
            "harness_id": row["agent_id"],
            "model_id": row["model_id"],
            "benchmark_id": row["benchmark_id"],
            "reasoning_effort": row.get("reasoning_effort"),
            "evidence_granularity": row["evidence_granularity"],
        })
    b_dir = out / "B_harnesses"
    write_jsonl(b_dir / "harness_catalog.jsonl", harnesses)
    write_jsonl(b_dir / "crossed_agent_model_observations.jsonl", resolved)
    write_jsonl(b_dir / "crossed_task_outcomes.jsonl", b_outcomes)
    write_jsonl(b_dir / "design_edges.jsonl", sorted(edges, key=lambda x: x["edge_id"]))
    matched = build_b_matched_dataset(observations, outcomes)
    write_jsonl(b_dir / "exact_matched_blocks.jsonl", matched["matched_blocks"])
    write_jsonl(b_dir / "provisional_same_label_blocks.jsonl", matched["provisional_blocks"])
    write_jsonl(b_dir / "pairwise_harness_comparisons.jsonl", matched["pairwise_comparisons"])
    write_jsonl(b_dir / "pairwise_harness_summaries.jsonl", matched["pair_summaries"])
    write_jsonl(b_dir / "rejected_comparisons.jsonl", matched["rejected"])

    stable_b_pointer = ROOT / "data/builtin_b/latest.json"
    stable_b_manifest = None
    if stable_b_pointer.is_file():
        pointer = json.loads(stable_b_pointer.read_text(encoding="utf-8"))
        stable_source = ROOT / pointer["dataset"]
        stable_b_dir = b_dir / "stable"
        stable_b_dir.mkdir(parents=True, exist_ok=True)
        for name in ("agent_families.jsonl", "aliases.jsonl", "capability_baselines.jsonl"):
            shutil.copy2(stable_source / name, stable_b_dir / name)
        stable_b_manifest = json.loads(
            (stable_source / "manifest.json").read_text(encoding="utf-8")
        )

    exact_blocks_with_dates = []
    for block in matched["matched_blocks"]:
        dates = sorted({
            ref.get("observed_at")
            for member in block.get("members", [])
            for ref in member.get("evidence_refs", [])
            if ref.get("observed_at")
        })
        exact_blocks_with_dates.append({
            **block,
            "result_date": dates[-1] if dates else None,
        })
    b_freshness = classify_latest_records(
        exact_blocks_with_dates,
        key_fields=("block_id",),
        as_of=args.as_of,
        max_age_days=int(freshness_policy["b_max_age_days"]),
        date_fields=("result_date",),
        policy_id=freshness_policy["policy_id"],
    )
    b_current_dir = b_dir / "current"
    b_history_dir = b_dir / "history"
    write_jsonl(b_current_dir / "exact_matched_blocks.jsonl", b_freshness["current"])
    write_jsonl(b_current_dir / "capability_baselines.jsonl", [])
    write_jsonl(b_history_dir / "exact_matched_blocks.jsonl", b_freshness["history"])
    write_jsonl(
        b_history_dir / "provisional_same_label_blocks.jsonl",
        matched["provisional_blocks"],
    )

    rejection_summary = {
        "schema_version": "experience-evaluation-b-rejection-summary/v0.1",
        "total_rejected_outcomes": len(matched["rejected"]),
        "by_stage": dict(sorted(Counter(r["stage"] for r in matched["rejected"]).items())),
        "by_primary_reason": dict(sorted(Counter(r["reason"] for r in matched["rejected"]).items())),
        "by_all_reasons": dict(sorted(Counter(
            reason
            for row in matched["rejected"]
            for reason in row.get("reasons", [row["reason"]])
        ).items())),
    }
    (b_dir / "rejection_summary.json").write_text(
        json.dumps(rejection_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    exact_anchor_report = {
        "schema_version": "experience-evaluation-b-exact-anchor-report/v0.1",
        "as_of": args.as_of,
        "exact_anchor_blocks": len(matched["matched_blocks"]),
        "exact_pairwise_comparisons": len(matched["pairwise_comparisons"]),
        "provisional_same_label_blocks": len(matched["provisional_blocks"]),
        "anchor_status": "available" if matched["matched_blocks"] else "none_in_current_public_snapshot",
        "next_action_if_zero": [
            "obtain task-level public runs with complete benchmark/task/model/harness/environment revisions",
            "run a controlled cross-harness benchmark with all non-harness conditions frozen",
        ] if not matched["matched_blocks"] else [],
    }
    (b_dir / "exact_anchor_report.json").write_text(
        json.dumps(exact_anchor_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    freshness_summary = {
        "schema_version": "experience-evaluation-freshness-summary/v0.1",
        "as_of": args.as_of,
        "policy": freshness_policy,
        "current_view_ready": True,
        "A": {
            "current_model_configs": len(current_configs),
            "current_observations": len(current_a_records),
            "current_capability_baselines": a_calibration["cell_count"],
            "calibration_contract_id": a_calibration["contract_id"],
            "historical_model_configs": len(historical_configs),
            "historical_observations": len(historical_a_records),
        },
        "B": {
            "current_exact_blocks": len(b_freshness["current"]),
            "historical_exact_blocks": len(b_freshness["history"]),
            "historical_provisional_blocks": len(matched["provisional_blocks"]),
            "current_capability_baselines": 0,
            "stable_agent_families": (
                stable_b_manifest.get("agent_family_count", 0)
                if stable_b_manifest else 0
            ),
            "stable_capability_priors": (
                stable_b_manifest.get("capability_cell_count", 0)
                if stable_b_manifest else 0
            ),
        },
    }

    coverage = {
        "schema_version": "experience-evaluation-ab-coverage/v0.1",
        "as_of": args.as_of,
        "A": {
            "catalog_models": len(models),
            "livebench_model_configs": len({r["source_model_id"] for r in a_records}),
            "subtask_observations": len(a_records),
            "categories": sorted({r["category"] for r in a_records}),
            "granularity": "model_by_subtask_score",
            "current_model_configs": len(current_configs),
            "current_subtask_observations": len(current_a_records),
            "historical_subtask_observations": len(historical_a_records),
        },
        "B": {
            "catalog_harnesses": len(harnesses),
            "resolved_agent_model_observations": len(resolved),
            "task_outcomes": len(b_outcomes),
            "design_edges": len(edges),
            "observed_harnesses": len({r["agent_id"] for r in resolved}),
            "observed_models": len({r["model_id"] for r in resolved}),
            "exact_matched_blocks": len(matched["matched_blocks"]),
            "provisional_same_label_blocks": len(matched["provisional_blocks"]),
            "pairwise_comparisons": len(matched["pairwise_comparisons"]),
            "pair_summaries": len(matched["pair_summaries"]),
            "rejected_records": len(matched["rejected"]),
            "granularity": "strict_exact_or_provisional_crossed_model_harness_task_or_submission",
            "current_exact_blocks": len(b_freshness["current"]),
            "historical_provisional_blocks": len(matched["provisional_blocks"]),
            "stable_agent_families": (
                stable_b_manifest.get("agent_family_count", 0)
                if stable_b_manifest else 0
            ),
        },
        "rule": "A and B are physically separate; no score is copied between them.",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "freshness_summary.json").write_text(
        json.dumps(freshness_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "experience-evaluation-ab-manifest/v0.1",
        "as_of": args.as_of,
        "files": {},
    }
    for path in sorted(out.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][str(path.relative_to(out))] = file_hash(path)
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(coverage, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
