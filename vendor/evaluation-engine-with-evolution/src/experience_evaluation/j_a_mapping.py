"""Project J evaluation atoms onto LiveBench A subtasks and canonical axes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_AXES,
    CANONICAL_SCALE_ID,
    align_probability_cell,
)
from .public_dataset import DatasetError


MAPPING_ID = "j-atom-to-livebench-subtask/v0.1"
A_CATALOG_ID = "livebench-current-subtask/v0.1"
ATOM_CATALOG_ID = "eval-atom-seven-link/v0.1"
GENERATOR = "experience-evaluation-j-a-projector/v0.1"


def load_j_a_mapping_bundle(config_dir: Path) -> dict[str, Any]:
    """Load and validate the frozen A catalog, J atoms, and T mapping."""
    a_catalog = _read_json(config_dir / "a_subtask_catalog.json")
    atoms = _read_json(config_dir / "j_evaluation_atoms.json")
    mapping = _read_json(config_dir / "j_to_a_mapping.json")
    return validate_j_a_mapping_bundle(a_catalog, atoms, mapping)


def validate_j_a_mapping_bundle(
    a_catalog: dict[str, Any],
    atom_catalog: dict[str, Any],
    mapping: dict[str, Any],
) -> dict[str, Any]:
    _require_id(a_catalog, "catalog_id", A_CATALOG_ID)
    _require_id(atom_catalog, "catalog_id", ATOM_CATALOG_ID)
    _require_id(mapping, "mapping_id", MAPPING_ID)
    for document in (a_catalog, atom_catalog, mapping):
        if document.get("axis_contract_id") != AXIS_CONTRACT_ID:
            raise DatasetError(f"mapping documents must use {AXIS_CONTRACT_ID}")

    subtasks = {}
    for row in a_catalog.get("subtasks") or []:
        subtask_id = row.get("subtask_id")
        axis_id = row.get("axis_id")
        if not isinstance(subtask_id, str) or not subtask_id:
            raise DatasetError("A catalog subtask_id must be a non-empty string")
        if axis_id not in CANONICAL_AXES:
            raise DatasetError(f"A subtask {subtask_id} has unknown axis {axis_id}")
        if subtask_id in subtasks:
            raise DatasetError(f"duplicate A subtask_id: {subtask_id}")
        subtasks[subtask_id] = {**row, "axis_id": axis_id}

    atoms = {}
    for row in atom_catalog.get("atoms") or []:
        atom_id = row.get("atom_id")
        status = row.get("link_status")
        if not isinstance(atom_id, str) or not atom_id:
            raise DatasetError("J atom_id must be a non-empty string")
        if status not in {"mapped", "unlinked"}:
            raise DatasetError(f"J atom {atom_id} link_status must be mapped or unlinked")
        if atom_id in atoms:
            raise DatasetError(f"duplicate J atom_id: {atom_id}")
        atoms[atom_id] = row

    edges = []
    seen = set()
    t_weights: dict[tuple[str, str], float] = {}
    for edge in mapping.get("edges") or []:
        atom_id = edge.get("atom_id")
        subtask_id = edge.get("a_subtask_id")
        weight = edge.get("weight")
        if atom_id not in atoms:
            raise DatasetError(f"mapping edge uses unknown atom: {atom_id}")
        if atoms[atom_id]["link_status"] != "mapped":
            raise DatasetError(f"unlinked atom cannot appear in T: {atom_id}")
        if subtask_id not in subtasks:
            raise DatasetError(f"mapping edge uses unknown A subtask: {subtask_id}")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool):
            raise DatasetError(f"T weight for {atom_id}->{subtask_id} must be numeric")
        weight = float(weight)
        if not 0 < weight <= 1:
            raise DatasetError(f"T weight for {atom_id}->{subtask_id} must be in (0,1]")
        key = (atom_id, subtask_id)
        if key in seen:
            raise DatasetError(f"duplicate mapping edge: {atom_id}->{subtask_id}")
        seen.add(key)
        t_weights[key] = weight
        edges.append({**edge, "weight": weight})

    mapped_atoms = {atom_id for atom_id, row in atoms.items() if row["link_status"] == "mapped"}
    atoms_with_edges = {atom_id for atom_id, _ in t_weights}
    missing_atom_edges = sorted(mapped_atoms - atoms_with_edges)
    if missing_atom_edges:
        raise DatasetError(f"mapped atoms have no T edges: {missing_atom_edges}")
    unused_subtasks = sorted(set(subtasks) - {subtask_id for _, subtask_id in t_weights})
    if unused_subtasks:
        raise DatasetError(f"A subtasks have no incoming T: {unused_subtasks}")

    axis_membership: dict[str, tuple[str, ...]] = {}
    for axis in CANONICAL_AXES:
        members = tuple(
            subtask_id for subtask_id, row in subtasks.items() if row["axis_id"] == axis
        )
        if not members:
            raise DatasetError(f"canonical axis has no A subtasks: {axis}")
        axis_membership[axis] = members

    w_weights: dict[tuple[str, str], float] = {}
    for atom_id in mapped_atoms:
        for axis, members in axis_membership.items():
            weight = max((t_weights.get((atom_id, subtask_id), 0.0) for subtask_id in members), default=0.0)
            if weight > 0:
                w_weights[(atom_id, axis)] = weight
    uncovered_axes = [axis for axis in CANONICAL_AXES if not any(k == axis for _, k in w_weights)]
    if uncovered_axes:
        raise DatasetError(f"axes have no W coverage: {uncovered_axes}")

    return {
        "schema_version": "experience-evaluation-j-a-mapping-bundle/v0.1",
        "mapping_id": MAPPING_ID,
        "axis_contract_id": AXIS_CONTRACT_ID,
        "a_catalog": a_catalog,
        "atom_catalog": atom_catalog,
        "mapping": {**mapping, "edges": edges},
        "subtasks": subtasks,
        "atoms": atoms,
        "axis_membership": axis_membership,
        "T": t_weights,
        "W": w_weights,
        "G": {
            (subtask_id, row["axis_id"]): 1.0
            for subtask_id, row in subtasks.items()
        },
        "mapped_atom_ids": tuple(sorted(mapped_atoms)),
        "unlinked_atom_ids": tuple(
            sorted(atom_id for atom_id, row in atoms.items() if row["link_status"] == "unlinked")
        ),
    }


def project_atoms_to_subtasks(
    atom_scores: Mapping[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """Weighted mean of observed atoms onto each A subtask, then logit."""
    return _project(
        atom_scores,
        weights=bundle["T"],
        targets=tuple(bundle["subtasks"]),
        target_kind="a_subtask",
        bundle=bundle,
    )


def project_atoms_to_axes(
    atom_scores: Mapping[str, Any],
    bundle: dict[str, Any],
    *,
    q_mask: Optional[Mapping[str, float]] = None,
) -> dict[str, Any]:
    """Weighted mean of observed atoms onto canonical axes, then logit.

    ``q_mask[k]`` is the TaskSpec semantic loading. A non-positive mask zeros
    that axis even when W is positive. Missing mask keys default to 1, so a
    caller expressing a TaskSpec Q must pass an explicit 0.0 for every axis the
    task does not load. Unknown mask keys are rejected rather than ignored: a
    misspelled axis would otherwise silently leave that axis at full weight.
    """
    weights = dict(bundle["W"])
    if q_mask is not None:
        unknown = sorted(set(q_mask) - set(CANONICAL_AXES))
        if unknown:
            raise DatasetError(f"Q mask keys are outside the canonical axes: {unknown}")
        for axis in CANONICAL_AXES:
            loading = q_mask.get(axis, 1.0)
            if not isinstance(loading, (int, float)) or isinstance(loading, bool):
                raise DatasetError(f"Q mask for {axis} must be numeric")
            if float(loading) <= 0:
                for key in list(weights):
                    if key[1] == axis:
                        del weights[key]
            else:
                for key in list(weights):
                    if key[1] == axis:
                        weights[key] = weights[key] * float(loading)
    return _project(
        atom_scores,
        weights=weights,
        targets=CANONICAL_AXES,
        target_kind="canonical_axis",
        bundle=bundle,
    )


def _project(
    atom_scores: Mapping[str, Any],
    *,
    weights: Mapping[tuple[str, str], float],
    targets: Sequence[str],
    target_kind: str,
    bundle: dict[str, Any],
) -> dict[str, Any]:
    observed = _normalize_atom_scores(atom_scores, bundle)
    cells = []
    for target in targets:
        numerator = 0.0
        coverage = 0.0
        used = []
        for atom_id, value in observed.items():
            weight = weights.get((atom_id, target), 0.0)
            if weight <= 0:
                continue
            numerator += weight * value
            coverage += weight
            used.append({"atom_id": atom_id, "weight": weight, "value": value})
        if coverage <= 0:
            cells.append({
                "target_id": target,
                "target_kind": target_kind,
                "status": "insufficient_evidence",
                "value": None,
                "canonical_value": None,
                "coverage": 0.0,
                "atom_count": 0,
                "atoms": [],
            })
            continue
        probability = numerator / coverage
        aligned = align_probability_cell(probability, None)
        cells.append({
            "target_id": target,
            "target_kind": target_kind,
            "status": "projected",
            "value": probability,
            "coverage": coverage,
            "atom_count": len(used),
            "atoms": used,
            "scale_id": aligned["source_scale_id"],
            "canonical_scale_id": CANONICAL_SCALE_ID,
            "canonical_value": aligned["canonical_value"],
            "boundary_clipped": aligned["boundary_clipped"],
        })
    return {
        "schema_version": "experience-evaluation-j-atom-projection/v0.1",
        "generator_version": GENERATOR,
        "mapping_id": bundle["mapping_id"],
        "axis_contract_id": AXIS_CONTRACT_ID,
        "target_kind": target_kind,
        "observed_atom_ids": sorted(observed),
        "skipped_unlinked_atom_ids": [
            atom_id for atom_id in atom_scores if atom_id in set(bundle["unlinked_atom_ids"])
        ],
        "cells": cells,
    }


def _normalize_atom_scores(
    atom_scores: Mapping[str, Any], bundle: dict[str, Any]
) -> dict[str, float]:
    atoms = bundle["atoms"]
    mapped = set(bundle["mapped_atom_ids"])
    observed = {}
    for atom_id, raw in atom_scores.items():
        if atom_id not in atoms:
            raise DatasetError(f"unknown J atom: {atom_id}")
        if atom_id not in mapped:
            continue
        if raw is None:
            continue
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise DatasetError(f"atom score {atom_id} must be numeric or null")
        value = float(raw)
        if not 0 <= value <= 1:
            raise DatasetError(f"atom score {atom_id} must be in [0,1]")
        observed[atom_id] = value
    return observed


def _require_id(document: dict[str, Any], field: str, expected: str) -> None:
    value = document.get(field)
    if value != expected:
        raise DatasetError(f"{field} must be {expected}, got {value!r}")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DatasetError(f"missing mapping document: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DatasetError(f"{path} must contain a JSON object")
    return payload
