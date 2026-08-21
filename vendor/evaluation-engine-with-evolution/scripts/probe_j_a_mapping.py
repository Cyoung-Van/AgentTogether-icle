#!/usr/bin/env python3
"""Live probe of the frozen J-atom → A-subtask mapping against real A/B cells."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from experience_evaluation.canonical_measurement import CANONICAL_AXES, align_probability_cell
from experience_evaluation.j_a_mapping import load_j_a_mapping_bundle, project_atoms_to_axes, project_atoms_to_subtasks

MODEL = "anthropic-claude-fable-5-max-effort"
HARNESS = "claude-code"


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def lookup(rows, key, value):
    return {row["dimension_id"]: row for row in rows if row.get(key) == value}


def atom_family_scores():
    families = {
        "reasoning": 0.85,
        "coding": 0.70,
        "agentic_coding": 0.60,
        "mathematics": 0.90,
        "data_analysis": 0.75,
        "language": 0.80,
        "instruction_following": 0.65,
    }
    bundle = load_j_a_mapping_bundle(ROOT / "config")
    scores = {}
    for atom_id in bundle["mapped_atom_ids"]:
        axes = [axis for (c, axis) in bundle["W"] if c == atom_id]
        primary = max(axes, key=lambda axis: bundle["W"][(atom_id, axis)])
        scores[atom_id] = families[primary]
    scores["tool_invocation"] = 1.0
    scores["memory_retrieval"] = 1.0
    scores["context_seeking"] = 1.0
    return bundle, scores, families


def main() -> int:
    failures = []
    bundle, scores, families = atom_family_scores()
    a_rows = lookup(
        read_jsonl(ROOT / "data/matrices/2026-08-19/A_models/current/capability_baselines.jsonl"),
        "model_config_id",
        MODEL,
    )
    b_rows = lookup(
        read_jsonl(ROOT / "data/builtin_b/2026-08-21/capability_baselines.jsonl"),
        "agent_id",
        HARNESS,
    )

    print("== bundle ==")
    print(f"mapped_atoms={len(bundle['mapped_atom_ids'])} unlinked={list(bundle['unlinked_atom_ids'])}")
    print(f"subtasks={len(bundle['subtasks'])} T_edges={len(bundle['T'])} W_edges={len(bundle['W'])}")
    print(f"A cells for {MODEL}: {sorted(a_rows)}")
    print(f"B cells for {HARNESS}: {sorted(b_rows)}")

    axes = project_atoms_to_axes(scores, bundle)
    subtasks = project_atoms_to_subtasks(scores, bundle)
    axis_cells = {row["target_id"]: row for row in axes["cells"]}
    subtask_cells = {row["target_id"]: row for row in subtasks["cells"]}

    print("\n== full-coverage axis projection (unlinked forced to 1.0) ==")
    if set(axes["skipped_unlinked_atom_ids"]) != set(bundle["unlinked_atom_ids"]):
        failures.append(f"unlinked skip set wrong: {axes['skipped_unlinked_atom_ids']}")
    for axis in CANONICAL_AXES:
        cell = axis_cells[axis]
        if cell["status"] != "projected":
            failures.append(f"{axis} should be projected, got {cell['status']}")
            continue
        print(
            f"{axis:24} p={cell['value']:.4f}  J={cell['canonical_value']:+.4f}  "
            f"atoms={cell['atom_count']}  coverage={cell['coverage']:.2f}  family={families[axis]:.2f}"
        )
        # Family score is the primary-axis assignment, not an exact identity after max-T collapse.
        if not 0 < cell["value"] < 1:
            failures.append(f"{axis} probability left [0,1]")

    print("\n== A-subtask coverage ==")
    missing_subtasks = [sid for sid, row in subtask_cells.items() if row["status"] != "projected"]
    if missing_subtasks:
        failures.append(f"subtasks not projected: {missing_subtasks}")
    for sid, row in subtask_cells.items():
        print(f"{sid:24} p={row['value']:.4f}  J={row['canonical_value']:+.4f}  atoms={row['atom_count']}")

    print("\n== C = J - A - B on real cells ==")
    for axis in CANONICAL_AXES:
        j = axis_cells[axis]["canonical_value"]
        a = a_rows[axis]["canonical_value"]
        b = b_rows[axis]["canonical_value"]
        c = j - a - b
        print(f"{axis:24} J={j:+.4f}  A={a:+.4f}  B={b:+.4f}  C={c:+.4f}")
        if not math.isfinite(c):
            failures.append(f"{axis} C is not finite")

    print("\n== partial: only coding atoms ==")
    coding_only = {
        atom: scores[atom]
        for atom in ("code_generation_correctness", "code_completion_fidelity", "test_verification")
    }
    partial = {row["target_id"]: row for row in project_atoms_to_axes(coding_only, bundle)["cells"]}
    if partial["coding"]["status"] != "projected":
        failures.append("coding-only should project coding")
    if partial["mathematics"]["status"] != "insufficient_evidence":
        failures.append("coding-only leaked into mathematics")
    if partial["language"]["status"] != "insufficient_evidence":
        failures.append("coding-only leaked into language")
    # test_verification and code_generation_correctness also load agentic_coding
    if partial["agentic_coding"]["status"] != "projected":
        failures.append("coding-only should still project agentic via shared atoms")
    print(
        f"coding={partial['coding']['status']} p={partial['coding']['value']:.4f}  "
        f"agentic={partial['agentic_coding']['status']}  "
        f"math={partial['mathematics']['status']}"
    )

    print("\n== all mapped atoms = 0.5 ⇒ J logit 0 ==")
    half = {atom: 0.5 for atom in bundle["mapped_atom_ids"]}
    half_axes = {row["target_id"]: row for row in project_atoms_to_axes(half, bundle)["cells"]}
    for axis in CANONICAL_AXES:
        if abs(half_axes[axis]["canonical_value"]) > 1e-12:
            failures.append(f"{axis} half-score logit {half_axes[axis]['canonical_value']} != 0")
    print("all seven axes at logit 0")

    print("\n== omit reasoning atoms ⇒ reasoning missing, not zero ==")
    no_reason = {
        atom: scores[atom]
        for atom in bundle["mapped_atom_ids"]
        if (atom, "reasoning") not in bundle["W"]
    }
    omitted = {row["target_id"]: row for row in project_atoms_to_axes(no_reason, bundle)["cells"]}
    if omitted["reasoning"]["status"] != "insufficient_evidence" or omitted["reasoning"]["value"] is not None:
        failures.append("omitted reasoning was treated as a score")
    if omitted["coding"]["status"] != "projected":
        failures.append("omitting reasoning zeroed coding")
    print(f"reasoning={omitted['reasoning']['status']}  coding={omitted['coding']['status']}")

    print("\n== Q mask zeros coding without filling 0 ==")
    masked = {
        row["target_id"]: row
        for row in project_atoms_to_axes(scores, bundle, q_mask={"coding": 0.0, "reasoning": 1.0})["cells"]
    }
    if masked["coding"]["status"] != "insufficient_evidence":
        failures.append("Q=0 did not hide coding")
    if masked["reasoning"]["status"] != "projected":
        failures.append("Q mask damaged reasoning")
    print(f"coding={masked['coding']['status']}  reasoning={masked['reasoning']['status']}")

    print("\n== identity check: logit(clip(p)) matches align_probability_cell ==")
    for axis in CANONICAL_AXES:
        cell = axis_cells[axis]
        aligned = align_probability_cell(cell["value"], None)
        if abs(aligned["canonical_value"] - cell["canonical_value"]) > 1e-12:
            failures.append(f"{axis} logit mismatch")

    print("\n== result ==")
    if failures:
        for item in failures:
            print(f"FAIL {item}")
        return 1
    print("PASS all live probes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
