"""Built-in A/B registry, conservative identity resolution, and J→C pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Optional, Sequence

from .canonical_measurement import (
    AXIS_CONTRACT_ID,
    CANONICAL_SCALE_ID,
    LIVEBENCH_CATEGORY_AXIS,
    PROBABILITY_SCALE_ID,
    align_additive_effect_cell,
    align_probability_cell,
    validate_axis_subset,
)
from .a_local_probe import (
    calibrate_local_probe_a,
    model_match_key,
    overlay_local_a,
    split_results_by_role,
)
from .b_prior import build_default_b, is_family_usable_b_observation, specialize_b
from .b_specialization import match_agent_family
from .evaluation_matrix import generate_evaluation_matrix
from .public_dataset import (
    DatasetError,
    canonical_model_id,
    normalize_id,
    sha256_file,
    stable_record_id,
)


C_GENERATOR_VERSION = "experience-evaluation-c-generator/v0.4"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"JSON must be an object: {path}")
    return value


def _read_jsonl(path: Path, *, optional: bool = False) -> list[dict[str, Any]]:
    if optional and not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise DatasetError(f"cannot read JSONL {path}: {exc}") from exc
    rows = []
    for lineno, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSONL {path}:{lineno}: {exc}") from exc
        if not isinstance(value, dict):
            raise DatasetError(f"JSONL row must be an object: {path}:{lineno}")
        rows.append(value)
    return rows


class BuiltinDatasetRegistry:
    """Discover immutable built-in A/B snapshots under a matrix root."""

    def __init__(self, root: str | Path, *, include_history: bool = False):
        self.root = Path(root)
        self.include_history = include_history

    def available(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            item.name for item in self.root.iterdir()
            if item.is_dir() and (item / "manifest.json").is_file()
        )

    def open(self, as_of: str) -> "BuiltinSnapshot":
        if as_of not in self.available():
            raise DatasetError(f"unknown built-in matrix snapshot: {as_of}")
        return BuiltinSnapshot(
            self.root / as_of,
            include_history=self.include_history,
        )

    def latest(self) -> "BuiltinSnapshot":
        snapshots = self.available()
        if not snapshots:
            raise DatasetError(f"no built-in matrix snapshots under {self.root}")
        return self.open(snapshots[-1])


class BuiltinSnapshot:
    """One verified built-in A/B data snapshot."""

    def __init__(self, root: Path, *, include_history: bool = False):
        self.root = root
        self.manifest = _read_json(root / "manifest.json")
        self.as_of = str(self.manifest.get("as_of") or root.name)
        self._verify_integrity()
        self.integrity_verified = True
        self.coverage = _read_json(root / "coverage.json")
        self.freshness_summary = _read_json(root / "freshness_summary.json")
        if self.freshness_summary.get("current_view_ready") is not True:
            raise DatasetError(f"snapshot current view is not ready: {root}")
        self.data_view = "history_inclusive" if include_history else "current_only"
        self.model_catalog = _read_jsonl(root / "A_models/model_catalog.jsonl")
        a_view = root / "A_models" if include_history else root / "A_models/current"
        b_view = root / "B_harnesses" if include_history else root / "B_harnesses/current"
        self.source_model_configs = _read_jsonl(a_view / "source_model_configs.jsonl")
        self.a_observations = _read_jsonl(a_view / "model_benchmark_observations.jsonl")
        self.a_capability_baselines = _read_jsonl(
            a_view / "capability_baselines.jsonl", optional=True
        )
        self.harness_catalog = _read_jsonl(root / "B_harnesses/harness_catalog.jsonl")
        self.stable_agent_families = _read_jsonl(
            root / "B_harnesses/stable/agent_families.jsonl", optional=True
        )
        self.stable_agent_aliases = _read_jsonl(
            root / "B_harnesses/stable/aliases.jsonl", optional=True
        )
        self.stable_b_priors = _read_jsonl(
            root / "B_harnesses/stable/capability_baselines.jsonl", optional=True
        )
        self.b_capability_baselines = _read_jsonl(
            b_view / "capability_baselines.jsonl", optional=True
        )
        self.b_anchor_report = _read_json(root / "B_harnesses/exact_anchor_report.json")

    def _verify_integrity(self) -> None:
        files = self.manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise DatasetError(f"snapshot manifest lacks file hashes: {self.root}")
        resolved_root = self.root.resolve()
        for relative, expected in files.items():
            path = self._manifest_path(relative, resolved_root)
            if not path.is_file():
                raise DatasetError(f"manifest file missing: {relative}")
            actual = sha256_file(path)
            if actual != expected:
                raise DatasetError(
                    f"manifest hash mismatch for {relative}: expected {expected}, got {actual}"
                )
        self.audit_artifacts = self._verify_audit_artifacts(resolved_root)

    def _manifest_path(self, relative: str, resolved_root: Path) -> Path:
        path = (self.root / relative).resolve()
        if resolved_root not in path.parents:
            raise DatasetError(f"manifest path escapes snapshot: {relative}")
        return path

    def _verify_audit_artifacts(self, resolved_root: Path) -> dict[str, str]:
        """Audit artifacts are rebuild-only dumps that need not ship with the repo.

        Their digests stay in the manifest so a re-downloaded copy is still
        verifiable, but an absent file is reported rather than treated as
        corruption. Runtime never reads them.
        """
        artifacts = self.manifest.get("audit_artifacts") or {}
        if not isinstance(artifacts, dict):
            raise DatasetError(f"manifest audit_artifacts must be an object: {self.root}")
        statuses = {}
        for relative, entry in sorted(artifacts.items()):
            if relative in (self.manifest.get("files") or {}):
                raise DatasetError(
                    f"audit artifact cannot also be a distributed file: {relative}"
                )
            expected = entry.get("sha256") if isinstance(entry, dict) else entry
            if not isinstance(expected, str) or not expected:
                raise DatasetError(f"audit artifact lacks sha256: {relative}")
            path = self._manifest_path(relative, resolved_root)
            if not path.is_file():
                statuses[relative] = "not_distributed"
                continue
            if sha256_file(path) != expected:
                raise DatasetError(f"audit artifact hash mismatch for {relative}")
            statuses[relative] = "present_verified"
        return statuses

    def descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": "experience-evaluation-builtin-snapshot/v0.1",
            "as_of": self.as_of,
            "root": str(self.root),
            "integrity_verified": self.integrity_verified,
            "manifest_sha256": sha256_file(self.root / "manifest.json"),
            "coverage": self.coverage,
            "data_view": self.data_view,
            "freshness_summary": self.freshness_summary,
            "audit_artifacts": dict(self.audit_artifacts),
        }

    def _resolve_model(self, identity: dict[str, Any]) -> dict[str, Any]:
        provider = identity.get("provider") or identity.get("model_provider")
        config_queries = []
        for key in ("model_config_id", "model_revision", "source_model_id"):
            value = identity.get(key)
            if isinstance(value, str) and value:
                config_queries.append(canonical_model_id(value, provider))
                config_queries.append(normalize_id(value))
        model_value = identity.get("model_id") or identity.get("model")
        if isinstance(model_value, str) and model_value:
            config_queries.append(canonical_model_id(model_value, provider))
            config_queries.append(normalize_id(model_value))
        config_queries = list(dict.fromkeys(config_queries))
        for row in self.source_model_configs:
            normalized_config = row.get("normalized_model_config_id")
            source_model = row.get("source_model_id")
            if normalized_config in config_queries or (
                isinstance(source_model, str) and normalize_id(source_model) in config_queries
            ):
                return {
                    "match_tier": "exact_dataset_config",
                    "model_config_id": normalized_config,
                    "model_id": None,
                    "source_model_id": source_model,
                    "identity_status": row.get("identity_status"),
                }

        catalog_queries = set(config_queries)
        if isinstance(identity.get("model_id"), str):
            catalog_queries.add(identity["model_id"])
        for row in self.model_catalog:
            candidates = {
                row.get("model_id"),
                canonical_model_id(str(row.get("display_name")), row.get("provider"))
                if row.get("display_name") else None,
                normalize_id(str(row.get("display_name"))) if row.get("display_name") else None,
            }
            if catalog_queries & {candidate for candidate in candidates if candidate}:
                return {
                    "match_tier": "catalog_identity_only",
                    "model_id": row["model_id"],
                    "model_config_id": None,
                    "display_name": row.get("display_name"),
                    "provider": row.get("provider"),
                }
        return {"match_tier": "unresolved", "model_id": None, "model_config_id": None}

    def _resolve_harness(self, identity: dict[str, Any]) -> dict[str, Any]:
        value = identity.get("harness_id") or identity.get("harness")
        revision = identity.get("harness_revision") or identity.get("harness_version")
        stable_match = match_agent_family(
            value,
            self.stable_agent_families,
            self.stable_agent_aliases,
            observed_version=revision,
        )
        if stable_match["match_tier"] != "generic_default":
            agent_id = stable_match["agent_id"]
            return {
                **stable_match,
                "harness_id": agent_id,
                "harness_revision": revision,
                "harness_revision_id": f"{agent_id}@{revision}" if revision else None,
            }
        if not isinstance(value, str) or not value:
            return {
                **stable_match,
                "harness_id": "generic-agent-shell",
                "harness_revision": revision,
                "harness_revision_id": None,
            }
        query = normalize_id(value)
        matched = next((
            row for row in self.harness_catalog
            if row.get("harness_id") == query
            or normalize_id(str(row.get("display_name"))) == query
        ), None)
        if matched is None:
            return {
                **stable_match,
                "harness_id": "generic-agent-shell",
                "harness_revision": revision,
                "harness_revision_id": None,
            }
        revision_id = f"{matched['harness_id']}@{revision}" if revision else None
        return {
            "match_tier": "catalog_identity_only",
            "harness_id": matched["harness_id"],
            "harness_revision": revision,
            "harness_revision_id": revision_id,
            "display_name": matched.get("display_name"),
            "observed_version": revision,
            "version_policy": "ignored_for_matching",
        }

    def resolve_identity(self, identity: dict[str, Any]) -> dict[str, Any]:
        subject_id = identity.get("subject_id")
        if not isinstance(subject_id, str) or not subject_id:
            raise DatasetError("subject identity requires subject_id")
        return {
            "schema_version": "experience-evaluation-resolved-identity/v0.1",
            "subject_id": subject_id,
            "model": self._resolve_model(identity),
            "harness": self._resolve_harness(identity),
            "input_identity": identity,
        }

    def lookup_a(
        self, resolved_model: dict[str, Any], dimensions: Sequence[str]
    ) -> dict[str, Any]:
        config_id = resolved_model.get("model_config_id")
        if resolved_model.get("match_tier") != "exact_dataset_config" or not config_id:
            return {"status": "unavailable", "cells": [], "reasons": ["exact_model_config_unavailable"]}
        calibrated = [
            row for row in self.a_capability_baselines
            if row.get("model_config_id") == config_id
            and row.get("dimension_id") in dimensions
            and row.get("calibration_status") == "calibrated_for_c"
        ]
        if calibrated:
            calibrated = [self._align_a_cell(row) for row in calibrated]
            missing = sorted(set(dimensions) - {row["dimension_id"] for row in calibrated})
            return {
                "status": "available" if not missing else "partial",
                "cells": calibrated,
                "reasons": [f"missing_dimension:{item}" for item in missing],
                "model_config_id": config_id,
            }

        grouped: dict[str, list[float]] = {}
        source_ids: dict[str, list[str]] = {}
        for row in self.a_observations:
            if row.get("model_id") != config_id or row.get("score_status") != "observed":
                continue
            dimension = LIVEBENCH_CATEGORY_AXIS.get(str(row.get("category")))
            if dimension not in dimensions:
                continue
            score = row.get("score")
            if not isinstance(score, (int, float)):
                continue
            grouped.setdefault(dimension, []).append(float(score) / 100.0)
            source_ids.setdefault(dimension, []).append(str(row.get("record_id")))
        cells = []
        for dimension, values in sorted(grouped.items()):
            stderr = statistics.stdev(values) / math.sqrt(len(values)) if len(values) >= 2 else None
            cells.append({
                "baseline_id": stable_record_id("a-reference", self.as_of, config_id, dimension),
                "model_config_id": config_id,
                "dimension_id": dimension,
                "value": sum(values) / len(values),
                "stderr": stderr,
                "scale_id": "normalized_0_1",
                "calibration_status": "uncalibrated_reference",
                "source_record_ids": source_ids[dimension],
                "axis_contract_id": AXIS_CONTRACT_ID,
                **align_probability_cell(sum(values) / len(values), stderr),
            })
        if not cells:
            return {"status": "unavailable", "cells": [], "reasons": ["a_capability_evidence_unavailable"]}
        missing = sorted(set(dimensions) - {row["dimension_id"] for row in cells})
        return {
            "status": "reference_only",
            "cells": cells,
            "reasons": ["not_calibrated_for_c", *[f"missing_dimension:{item}" for item in missing]],
            "model_config_id": config_id,
            "axis_contract_id": AXIS_CONTRACT_ID,
        }

    @staticmethod
    def _align_a_cell(row: dict[str, Any]) -> dict[str, Any]:
        if row.get("scale_id") == PROBABILITY_SCALE_ID:
            alignment = align_probability_cell(row["value"], row.get("stderr"))
        elif row.get("scale_id") == CANONICAL_SCALE_ID:
            alignment = align_additive_effect_cell(
                row["value"], row.get("stderr"), row["scale_id"]
            )
        else:
            raise DatasetError(f"unsupported A baseline scale: {row.get('scale_id')}")
        return {**row, "axis_contract_id": AXIS_CONTRACT_ID, **alignment}

    def lookup_b(
        self, resolved_harness: dict[str, Any], dimensions: Sequence[str]
    ) -> dict[str, Any]:
        harness_id = resolved_harness.get("harness_id") or "generic-agent-shell"
        revision_id = resolved_harness.get("harness_revision_id")
        observed_cells = [
            row for row in self.b_capability_baselines
            if (
                row.get("agent_id") == harness_id
                or row.get("harness_id") == harness_id
            )
            and row.get("dimension_id") in dimensions
            and is_family_usable_b_observation(row)
        ]
        aligned_cells = []
        alignment_errors = []
        for row in observed_cells:
            try:
                aligned_cells.append({
                    **row,
                    "axis_contract_id": AXIS_CONTRACT_ID,
                    **align_additive_effect_cell(
                        row["value"], row.get("stderr"), row.get("scale_id")
                    ),
                })
            except DatasetError as exc:
                alignment_errors.append(str(exc))
        # Stable family priors are validated against the canonical additive scale
        # instead of trusting the canonical fields already on disk.
        stable_by_dimension: dict[str, dict[str, Any]] = {}
        for row in self.stable_b_priors:
            if row.get("agent_id") != harness_id or row.get("dimension_id") not in dimensions:
                continue
            try:
                stable_by_dimension[row["dimension_id"]] = {
                    **row,
                    "axis_contract_id": AXIS_CONTRACT_ID,
                    **align_additive_effect_cell(
                        row["value"], row.get("stderr"), row.get("scale_id")
                    ),
                }
            except (DatasetError, KeyError) as exc:
                alignment_errors.append(
                    f"stable_prior_rejected:{row.get('dimension_id')}:{exc}"
                )
        default_by_dimension = {
            row["dimension_id"]: row
            for row in build_default_b(
                harness_id=harness_id,
                harness_revision_id=revision_id,
                dimensions=dimensions,
            )
        }
        # A partial stable family file must not drop or crash an axis: fall back
        # per dimension and report which axes used the generic prior instead.
        incomplete_family_dimensions = sorted(
            set(dimensions) - set(stable_by_dimension)
        ) if stable_by_dimension else []
        prior_cells = [
            stable_by_dimension.get(dimension, default_by_dimension[dimension])
            for dimension in dimensions
        ]
        prior_source = (
            "agent_specialized_prior" if stable_by_dimension else "generic_default_prior"
        )
        observed_by_dimension: dict[str, dict[str, Any]] = {}
        conflicting_observations = []
        for row in aligned_cells:
            dimension = row["dimension_id"]
            existing = observed_by_dimension.get(dimension)
            if existing is not None:
                conflicting_observations.append(dimension)
                continue
            observed_by_dimension[dimension] = row
        # Conflicting family evidence is dropped rather than silently resolved by
        # iteration order; the axis keeps its prior and the reason is reported.
        for dimension in sorted(set(conflicting_observations)):
            observed_by_dimension.pop(dimension, None)
        cells = []
        specialized_dimensions = []
        for prior in prior_cells:
            observed = observed_by_dimension.get(prior["dimension_id"])
            if observed is None:
                cells.append(prior)
                continue
            try:
                cells.append(specialize_b(prior, observed))
                specialized_dimensions.append(prior["dimension_id"])
            except DatasetError as exc:
                alignment_errors.append(str(exc))
                cells.append(prior)
        default_dimensions = sorted(
            set(dimensions) - set(specialized_dimensions)
        )
        return {
            "status": "specialized" if specialized_dimensions else prior_source,
            "cells": cells,
            "reasons": [
                *(
                    ["no_family_observation_using_agent_specialized_prior"]
                    if not specialized_dimensions and prior_source == "agent_specialized_prior"
                    else ["no_family_observation_using_default_prior"]
                    if not specialized_dimensions
                    else []
                ),
                *[f"default_prior_dimension:{item}" for item in default_dimensions],
                *[
                    f"incomplete_family_prior_dimension:{item}"
                    for item in incomplete_family_dimensions
                ],
                *[
                    f"conflicting_family_observation_dimension:{item}"
                    for item in sorted(set(conflicting_observations))
                ],
                *alignment_errors,
            ],
            "harness_revision_id": revision_id,
            "axis_contract_id": AXIS_CONTRACT_ID,
            "specialized_dimensions": sorted(specialized_dimensions),
            "default_prior_dimensions": default_dimensions,
            "incomplete_family_prior_dimensions": incomplete_family_dimensions,
            "conflicting_observation_dimensions": sorted(set(conflicting_observations)),
            "exact_anchor_blocks": self.b_anchor_report.get("exact_anchor_blocks", 0),
        }


def _same_number(left: Any, right: Any) -> bool:
    """Compare two optional numbers without exact float equality."""
    if left is None or right is None:
        return left is None and right is None
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return False
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)


def _align_j_cell(cell: dict[str, Any]) -> dict[str, Any]:
    """Validate and pass through J's canonical projection without re-transforming it."""
    if cell.get("axis_contract_id") != AXIS_CONTRACT_ID:
        raise DatasetError("J cell axis contract does not match the canonical J contract")
    if cell.get("source_scale_id") != PROBABILITY_SCALE_ID:
        raise DatasetError("J cell source scale does not match the canonical J contract")
    if cell.get("canonical_scale_id") != CANONICAL_SCALE_ID:
        raise DatasetError("J cell canonical scale does not match the canonical J contract")
    if cell.get("value") is None:
        if any(cell.get(key) is not None for key in ("source_value", "source_stderr", "canonical_value", "canonical_stderr")):
            raise DatasetError("unavailable J cell cannot contain scale values")
        return {**cell, "scale_alignment_status": "unavailable_no_j_value"}
    if not _same_number(cell.get("source_value"), cell.get("value")) or cell.get("source_scale_id") != PROBABILITY_SCALE_ID:
        raise DatasetError("J source projection does not match its objective value")
    if not _same_number(cell.get("source_stderr"), cell.get("stderr")):
        raise DatasetError("J source stderr does not match its objective stderr")
    canonical_value = cell.get("canonical_value")
    canonical_stderr = cell.get("canonical_stderr")
    if not isinstance(canonical_value, (int, float)) or not math.isfinite(float(canonical_value)):
        raise DatasetError("available J cell requires finite canonical_value")
    if canonical_stderr is not None and (
        not isinstance(canonical_stderr, (int, float))
        or canonical_stderr < 0
        or not math.isfinite(float(canonical_stderr))
    ):
        raise DatasetError("J canonical_stderr must be non-negative finite numeric or null")
    expected = align_probability_cell(cell["value"], cell.get("stderr"))
    for key in ("canonical_value", "canonical_stderr"):
        actual = cell.get(key)
        expected_value = expected[key]
        if expected_value is None:
            if actual is not None:
                raise DatasetError(f"J {key} does not match canonical transform")
        elif not math.isclose(float(actual), float(expected_value), rel_tol=1e-12, abs_tol=1e-12):
            raise DatasetError(f"J {key} does not match canonical transform")
    if cell.get("boundary_clipped") != expected["boundary_clipped"]:
        raise DatasetError("J boundary clipping metadata does not match canonical transform")
    return {**cell, "scale_alignment_status": "j_generator_canonical_projection"}


def _c_observation_fingerprint(
    j_cell: dict[str, Any],
    a_cell: Optional[dict[str, Any]],
    b_cell: Optional[dict[str, Any]],
    calibration_contract: Optional[dict[str, Any]],
) -> str:
    """Identify one immutable C observation without depending on its own ID."""
    payload = {
        "j_cell": j_cell,
        "a_cell": a_cell,
        "b_cell": b_cell,
        "calibration_contract": calibration_contract,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _c_cell(
    j_cell: dict[str, Any],
    a_lookup: dict[str, Any],
    b_lookup: dict[str, Any],
    calibration_contract: Optional[dict[str, Any]],
) -> dict[str, Any]:
    dimension = j_cell["dimension_id"]
    a_cell = next((row for row in a_lookup.get("cells", []) if row.get("dimension_id") == dimension), None)
    b_cell = next((row for row in b_lookup.get("cells", []) if row.get("dimension_id") == dimension), None)
    reasons = []
    warnings = []
    if j_cell.get("canonical_value") is None:
        reasons.append("j_value_unavailable")
    synthetic_scope = bool(
        calibration_contract
        and calibration_contract.get("publication_scope") == "synthetic_only"
    )
    allowed_j_statuses = {"synthetic_only"} if synthetic_scope else {"publishable"}
    if j_cell.get("publish_status") not in allowed_j_statuses:
        reasons.append("j_not_publishable")
    if a_cell is None:
        reasons.append("a_dimension_unavailable")
    if b_cell is None:
        reasons.append("b_dimension_unavailable")
    if not calibration_contract:
        reasons.append("calibration_contract_missing")
    elif calibration_contract.get("verified") is not True:
        reasons.append("calibration_contract_unverified")
    if calibration_contract and calibration_contract.get("verified") is True:
        scale_id = calibration_contract.get("scale_id")
        if j_cell.get("canonical_scale_id") != scale_id:
            reasons.append("j_scale_mismatch")
        for label, cell in (("a", a_cell), ("b", b_cell)):
            if cell is not None and cell.get("canonical_scale_id") != scale_id:
                reasons.append(f"{label}_scale_mismatch")
            allowed_calibration = (
                {"calibrated_for_c"}
                if label == "a"
                else {
                    "calibrated_for_c",
                    "default_prior_for_c",
                    "document_specialized_prior_for_c",
                    "specialized_for_c",
                }
            )
            if cell is not None and cell.get("calibration_status") not in allowed_calibration:
                reasons.append(f"{label}_not_calibrated_for_c")
            if synthetic_scope and cell is not None:
                if cell.get("publication_scope") != "synthetic_only":
                    reasons.append(f"{label}_synthetic_scope_mismatch")
                if cell.get("evidence_origin") != "known_parameter_simulation":
                    reasons.append(f"{label}_synthetic_origin_mismatch")
        if synthetic_scope:
            if calibration_contract.get("evidence_origin") != "known_parameter_simulation":
                reasons.append("calibration_synthetic_origin_mismatch")
            if j_cell.get("publication_scope") != "synthetic_only":
                reasons.append("j_synthetic_scope_mismatch")
            if j_cell.get("evidence_origin") != "known_parameter_simulation":
                reasons.append("j_synthetic_origin_mismatch")
        if calibration_contract.get("independence_assumption") is not True:
            reasons.append("covariance_contract_missing")
    reasons = sorted(set(reasons))
    if b_cell is not None and b_cell.get("calibration_status") == "default_prior_for_c":
        warnings.append("b_default_prior_used")
    if b_cell is not None and b_cell.get("calibration_status") == "document_specialized_prior_for_c":
        warnings.append("b_agent_specialized_prior_used")
    if b_cell is not None and b_cell.get("calibration_status") == "specialized_for_c":
        warnings.append("b_specialized_with_family_data")
    observation_fingerprint = _c_observation_fingerprint(
        j_cell, a_cell, b_cell, calibration_contract
    )
    base = {
        "schema_version": "experience-evaluation-c-matrix-cell/v0.4",
        "cell_id": stable_record_id(
            "c-cell",
            C_GENERATOR_VERSION,
            j_cell["subject_id"],
            dimension,
            observation_fingerprint,
        ),
        "c_generator_version": C_GENERATOR_VERSION,
        "subject_id": j_cell["subject_id"],
        "dimension_id": dimension,
        "formula": "C=J-A-B",
        "axis_contract_id": AXIS_CONTRACT_ID,
        "source_scale_id": CANONICAL_SCALE_ID,
        "canonical_scale_id": CANONICAL_SCALE_ID,
        "calibration_contract_id": calibration_contract.get("contract_id")
        if calibration_contract else None,
        "reasons": reasons,
        "warnings": warnings,
        "evidence_origin": j_cell.get("evidence_origin"),
        "publication_scope": j_cell.get("publication_scope"),
    }
    if reasons:
        return {
            **base,
            "status": "unavailable",
            "value": None,
            "stderr": None,
            "canonical_value": None,
            "canonical_stderr": None,
        }
    value = (
        float(j_cell["canonical_value"])
        - float(a_cell["canonical_value"])
        - float(b_cell["canonical_value"])
    )
    standard_errors = (
        j_cell.get("canonical_stderr"),
        a_cell.get("canonical_stderr"),
        b_cell.get("canonical_stderr"),
    )
    if all(isinstance(item, (int, float)) for item in standard_errors):
        stderr = math.sqrt(sum(float(item) ** 2 for item in standard_errors))
        uncertainty_status = "available_independence_assumption"
    else:
        stderr = None
        uncertainty_status = "unavailable"
    return {
        **base,
        "status": "available",
        "value": value,
        "stderr": stderr,
        "canonical_value": value,
        "canonical_stderr": stderr,
        "uncertainty_status": uncertainty_status,
        "scale_id": CANONICAL_SCALE_ID,
        "j_cell_id": j_cell["cell_id"],
        "a_baseline_id": a_cell["baseline_id"],
        "b_baseline_id": b_cell["baseline_id"],
        "b_calibration_status": b_cell["calibration_status"],
    }


def generate_c_matrix(
    *,
    canonical_j_matrix: Sequence[dict[str, Any]],
    a_lookups: dict[str, dict[str, Any]],
    b_lookups: dict[str, dict[str, Any]],
    calibration_contract: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate C on the C-owned canonical axis and additive scale contract."""
    validate_axis_subset(sorted({cell["dimension_id"] for cell in canonical_j_matrix}))
    for cell in canonical_j_matrix:
        _align_j_cell(cell)
        if cell.get("axis_contract_id") != AXIS_CONTRACT_ID:
            raise DatasetError("J cell axis contract does not match C generator")
        if cell.get("canonical_scale_id") != CANONICAL_SCALE_ID:
            raise DatasetError("J cell scale contract does not match C generator")
        if cell["subject_id"] not in a_lookups or cell["subject_id"] not in b_lookups:
            raise DatasetError(f"C inputs missing subject lookup: {cell['subject_id']}")
    return [
        _c_cell(
            cell,
            a_lookups[cell["subject_id"]],
            b_lookups[cell["subject_id"]],
            calibration_contract,
        )
        for cell in canonical_j_matrix
    ]


def _calibrate_optional_local_probe(
    *,
    snapshot: BuiltinSnapshot,
    task_specs: Sequence[dict[str, Any]],
    baseline_results: Sequence[dict[str, Any]],
    identity_inputs: dict[str, dict[str, Any]],
    pipeline_config: dict[str, Any],
    generator_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], Optional[tuple[str, str]]]:
    probe_cfg = pipeline_config.get("local_probe_a")
    if not probe_cfg:
        return [], None, None
    if probe_cfg.get("enabled") is not True:
        return [], None, None
    if not baseline_results:
        raise DatasetError("local_probe_a.enabled requires baseline_model execution results")
    baseline_subject_id = probe_cfg.get("baseline_subject_id")
    if not isinstance(baseline_subject_id, str) or not baseline_subject_id:
        raise DatasetError("local_probe_a requires baseline_subject_id")
    contract = probe_cfg.get("contract")
    if not isinstance(contract, dict):
        raise DatasetError("local_probe_a requires contract")
    baseline_identity = identity_inputs.get(
        baseline_subject_id, {"subject_id": baseline_subject_id}
    )
    baseline_resolution = snapshot.resolve_identity(baseline_identity)
    model_config_id = (
        baseline_resolution["model"].get("model_config_id")
        or probe_cfg.get("model_config_id")
        or baseline_identity.get("model_revision")
        or baseline_subject_id
    )
    report = calibrate_local_probe_a(
        task_specs,
        baseline_results,
        generator_config,
        contract,
        baseline_subject_id=baseline_subject_id,
        model_config_id=str(model_config_id),
    )
    return report["cells"], report, model_match_key(baseline_identity, baseline_resolution["model"])


def run_matrix_pipeline(
    *,
    registry: BuiltinDatasetRegistry,
    task_specs: Sequence[dict[str, Any]],
    execution_results: Sequence[dict[str, Any]],
    subject_identities: Sequence[dict[str, Any]],
    pipeline_config: dict[str, Any],
) -> dict[str, Any]:
    """Auto-resolve A/B, generate J locally, and infer C when gates allow."""
    snapshot = registry.latest()
    generator_config = pipeline_config.get("generator")
    if not isinstance(generator_config, dict):
        raise DatasetError("pipeline_config requires generator config")
    identity_inputs = {}
    for identity in subject_identities:
        subject_id = identity.get("subject_id")
        if not isinstance(subject_id, str) or not subject_id:
            raise DatasetError("every subject identity requires subject_id")
        if subject_id in identity_inputs:
            raise DatasetError(f"duplicate subject identity: {subject_id}")
        identity_inputs[subject_id] = identity
    agent_results, baseline_results = split_results_by_role(
        execution_results, subject_identities
    )
    local_cells, local_probe_report, baseline_model_key = _calibrate_optional_local_probe(
        snapshot=snapshot,
        task_specs=task_specs,
        baseline_results=baseline_results,
        identity_inputs=identity_inputs,
        pipeline_config=pipeline_config,
        generator_config=generator_config,
    )
    j_bundle = generate_evaluation_matrix(task_specs, agent_results, generator_config)
    resolved = []
    a_lookups = {}
    b_lookups = {}
    dimensions = j_bundle["summary"]["dimensions"]
    validate_axis_subset(dimensions)
    canonical_j_matrix = [_align_j_cell(cell) for cell in j_bundle["evaluation_matrix"]]
    for subject_id in sorted({row["subject_id"] for row in j_bundle["evaluation_matrix"]}):
        identity = identity_inputs.get(subject_id, {"subject_id": subject_id})
        resolution = snapshot.resolve_identity(identity)
        resolved.append(resolution)
        a_lookup = snapshot.lookup_a(resolution["model"], dimensions)
        if local_cells:
            agent_key = model_match_key(identity, resolution["model"])
            if baseline_model_key is not None and agent_key == baseline_model_key:
                a_lookup = overlay_local_a(a_lookup, local_cells, dimensions)
            else:
                a_lookup = {
                    **a_lookup,
                    "reasons": sorted(set([*(a_lookup.get("reasons") or []), "local_probe_model_mismatch"])),
                }
        a_lookups[subject_id] = a_lookup
        b_lookups[subject_id] = snapshot.lookup_b(resolution["harness"], dimensions)
    calibration_contract = pipeline_config.get("calibration_contract")
    c_matrix = generate_c_matrix(
        canonical_j_matrix=canonical_j_matrix,
        a_lookups=a_lookups,
        b_lookups=b_lookups,
        calibration_contract=calibration_contract,
    )
    return {
        "schema_version": "experience-evaluation-abjc-pipeline/v0.1",
        "snapshot": snapshot.descriptor(),
        "identity_resolution": resolved,
        "a_lookups": a_lookups,
        "b_lookups": b_lookups,
        "j_bundle": j_bundle,
        "canonical_j_matrix": canonical_j_matrix,
        "c_matrix": c_matrix,
        "local_probe_a": local_probe_report,
        "summary": {
            "subject_count": len(resolved),
            "dimension_count": len(dimensions),
            "c_available_cells": sum(row["status"] == "available" for row in c_matrix),
            "c_unavailable_cells": sum(row["status"] != "available" for row in c_matrix),
            "dataset_snapshot": snapshot.as_of,
            "axis_contract_id": AXIS_CONTRACT_ID,
            "canonical_scale_id": CANONICAL_SCALE_ID,
            "c_generator_version": C_GENERATOR_VERSION,
            "local_probe_a_cell_count": 0 if local_probe_report is None else local_probe_report["cell_count"],
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_pipeline_bundle(
    output_dir: Path,
    pipeline: dict[str, Any],
    *,
    pipeline_config: dict[str, Any],
    input_files: Optional[dict[str, dict[str, str]]] = None,
) -> dict[str, Any]:
    """Persist one immutable A/B/J/C pipeline result with hashes."""
    if output_dir.exists():
        raise DatasetError(f"pipeline bundle already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    _write_json(output_dir / "snapshot.json", pipeline["snapshot"])
    _write_jsonl(output_dir / "identity_resolution.jsonl", pipeline["identity_resolution"])
    a_rows = []
    b_rows = []
    for subject_id, lookup in sorted(pipeline["a_lookups"].items()):
        if lookup.get("cells"):
            for cell in lookup["cells"]:
                a_rows.append({"subject_id": subject_id, "lookup_status": lookup["status"], **cell})
        else:
            a_rows.append({
                "subject_id": subject_id,
                "lookup_status": lookup["status"],
                "value": None,
                "reasons": lookup.get("reasons", []),
            })
    for subject_id, lookup in sorted(pipeline["b_lookups"].items()):
        if lookup.get("cells"):
            for cell in lookup["cells"]:
                b_rows.append({"subject_id": subject_id, "lookup_status": lookup["status"], **cell})
        else:
            b_rows.append({
                "subject_id": subject_id,
                "lookup_status": lookup["status"],
                "value": None,
                "reasons": lookup.get("reasons", []),
            })
    _write_jsonl(output_dir / "a_baselines.jsonl", a_rows)
    _write_jsonl(output_dir / "b_baselines.jsonl", b_rows)
    _write_jsonl(output_dir / "objective_matrix.jsonl", pipeline["j_bundle"]["objective_matrix"])
    _write_jsonl(output_dir / "j_matrix.jsonl", pipeline["j_bundle"]["evaluation_matrix"])
    _write_jsonl(output_dir / "canonical_j_matrix.jsonl", pipeline["canonical_j_matrix"])
    _write_jsonl(output_dir / "c_matrix.jsonl", pipeline["c_matrix"])
    _write_jsonl(output_dir / "rejected_results.jsonl", pipeline["j_bundle"]["rejected_results"])
    _write_json(output_dir / "pipeline_config.json", pipeline_config)
    if pipeline.get("local_probe_a") is not None:
        _write_json(output_dir / "local_probe_a.json", pipeline["local_probe_a"])
    _write_json(output_dir / "summary.json", {
        **pipeline["summary"],
        "j_summary": pipeline["j_bundle"]["summary"],
    })
    files = {
        item.name: sha256_file(item)
        for item in sorted(output_dir.iterdir())
        if item.is_file()
    }
    manifest = {
        "schema_version": "experience-evaluation-abjc-bundle-manifest/v0.1",
        "dataset_snapshot": pipeline["snapshot"]["as_of"],
        "dataset_manifest_sha256": pipeline["snapshot"]["manifest_sha256"],
        "files": files,
        "inputs": input_files or {},
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest
