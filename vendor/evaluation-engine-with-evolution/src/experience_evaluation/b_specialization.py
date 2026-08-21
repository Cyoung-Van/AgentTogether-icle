"""Build and match stable, document-derived Agent family priors for Matrix B."""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .canonical_measurement import AXIS_CONTRACT_ID, CANONICAL_AXES, CANONICAL_SCALE_ID
from .public_dataset import DatasetError, normalize_id, stable_record_id


B_SPECIALIZATION_GENERATOR = "experience-evaluation-b-specialization/v0.1"


def build_agent_specializations(
    agents: Sequence[dict[str, Any]], rubric: dict[str, Any]
) -> dict[str, Any]:
    """Convert official-description feature flags into seven-axis weak priors."""
    base = float(rubric["base_mean_logit"])
    prior_sd = float(rubric["prior_sd_logit"])
    minimum = float(rubric.get("min_logit", -0.25))
    maximum = float(rubric.get("max_logit", 0.60))
    feature_deltas = rubric.get("feature_deltas")
    if not isinstance(feature_deltas, dict):
        raise DatasetError("B specialization rubric requires feature_deltas")
    seen = set()
    agent_rows = []
    alias_rows = []
    cells = []
    for raw in agents:
        agent_id = normalize_id(str(raw.get("agent_id") or ""))
        if not agent_id or agent_id in seen:
            raise DatasetError(f"duplicate or invalid stable Agent family: {agent_id}")
        seen.add(agent_id)
        display_name = str(raw.get("display_name") or agent_id)
        features = list(raw.get("features") or [])
        unknown_features = sorted(set(features) - set(feature_deltas))
        if unknown_features:
            raise DatasetError(f"{agent_id} uses unknown B features: {unknown_features}")
        sources = list(raw.get("sources") or [])
        if not sources or any(not isinstance(item.get("url"), str) for item in sources):
            raise DatasetError(f"{agent_id} requires official source URLs")
        agent_rows.append({
            "schema_version": "experience-evaluation-stable-agent-family/v0.1",
            "agent_id": agent_id,
            "display_name": display_name,
            "description": raw.get("description"),
            "features": features,
            "sources": sources,
            "version_policy": "stable_family_ignore_release_version",
            "generator_version": B_SPECIALIZATION_GENERATOR,
        })
        all_aliases = [agent_id, display_name, *(raw.get("aliases") or [])]
        for alias in all_aliases:
            normalized_alias = normalize_id(str(alias))
            alias_rows.append({
                "schema_version": "experience-evaluation-agent-alias/v0.1",
                "agent_id": agent_id,
                "alias": str(alias),
                "normalized_alias": normalized_alias,
            })
        for dimension in CANONICAL_AXES:
            contributions = []
            value = base
            for feature in features:
                delta = float((feature_deltas[feature] or {}).get(dimension, 0.0))
                if delta:
                    contributions.append({"feature": feature, "delta_logit": delta})
                    value += delta
            value = min(max(value, minimum), maximum)
            cells.append({
                "schema_version": "experience-evaluation-b-family-prior/v0.1",
                "baseline_id": stable_record_id(
                    "b-family", B_SPECIALIZATION_GENERATOR, agent_id, dimension
                ),
                "agent_id": agent_id,
                "harness_id": agent_id,
                "harness_revision_id": f"{agent_id}@stable-family",
                "dimension_id": dimension,
                "value": value,
                "stderr": prior_sd,
                "canonical_value": value,
                "canonical_stderr": prior_sd,
                "axis_contract_id": AXIS_CONTRACT_ID,
                "scale_id": CANONICAL_SCALE_ID,
                "canonical_scale_id": CANONICAL_SCALE_ID,
                "scale_role": "additive_effect",
                "calibration_status": "document_specialized_prior_for_c",
                "evidence_tier": "official_description_prior",
                "uncertainty_kind": "prior_sd",
                "base_mean_logit": base,
                "feature_contributions": contributions,
                "source_urls": [item["url"] for item in sources],
                "generator_version": B_SPECIALIZATION_GENERATOR,
            })
    alias_index: dict[str, str] = {}
    deduplicated_aliases = []
    for row in alias_rows:
        existing = alias_index.get(row["normalized_alias"])
        if existing is not None and existing != row["agent_id"]:
            raise DatasetError(
                f"ambiguous stable Agent alias {row['normalized_alias']}: {existing}/{row['agent_id']}"
            )
        if existing is None:
            alias_index[row["normalized_alias"]] = row["agent_id"]
            deduplicated_aliases.append(row)
    return {
        "schema_version": "experience-evaluation-b-specialization-dataset/v0.1",
        "generator_version": B_SPECIALIZATION_GENERATOR,
        "agents": sorted(agent_rows, key=lambda row: row["agent_id"]),
        "aliases": sorted(deduplicated_aliases, key=lambda row: row["normalized_alias"]),
        "cells": sorted(cells, key=lambda row: (row["agent_id"], row["dimension_id"])),
    }


def match_agent_family(
    value: Optional[str],
    agents: Sequence[dict[str, Any]],
    aliases: Sequence[dict[str, Any]],
    *,
    observed_version: Optional[str] = None,
) -> dict[str, Any]:
    """Match a stable family by ID/display/alias; release version never changes identity."""
    if not isinstance(value, str) or not value.strip():
        return {
            "match_tier": "generic_default",
            "agent_id": "generic-agent-shell",
            "observed_version": observed_version,
            "version_policy": "ignored_for_matching",
        }
    query = normalize_id(value)
    by_id = {row["agent_id"]: row for row in agents}
    if query in by_id:
        tier = "stable_family_id"
        agent_id = query
    else:
        alias_index = {row["normalized_alias"]: row["agent_id"] for row in aliases}
        agent_id = alias_index.get(query)
        tier = "stable_family_alias" if agent_id else "generic_default"
    if not agent_id:
        agent_id = "generic-agent-shell"
    return {
        "match_tier": tier,
        "agent_id": agent_id,
        "display_name": by_id.get(agent_id, {}).get("display_name"),
        "observed_version": observed_version,
        "version_policy": "ignored_for_matching",
    }
