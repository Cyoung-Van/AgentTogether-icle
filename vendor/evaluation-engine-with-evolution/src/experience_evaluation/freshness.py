"""Versioned freshness policy for current and historical evidence views."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional, Sequence

from .public_dataset import DatasetError


FRESHNESS_POLICY_ID = "experience-evaluation-current-evidence/v0.1"
DEFAULT_A_MAX_AGE_DAYS = 120
DEFAULT_B_MAX_AGE_DAYS = 120
_DATE_RE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")


def evidence_date(record: dict[str, Any], date_fields: Sequence[str]) -> Optional[str]:
    for field in date_fields:
        value = record.get(field)
        if isinstance(value, str) and value:
            match = _DATE_RE.search(value)
            if match:
                try:
                    date.fromisoformat(match.group(1))
                except ValueError:
                    continue
                return match.group(1)
    return None


def classify_latest_records(
    records: Sequence[dict[str, Any]],
    *,
    key_fields: Sequence[str],
    as_of: str,
    max_age_days: int,
    date_fields: Sequence[str],
    policy_id: str = FRESHNESS_POLICY_ID,
) -> dict[str, list[dict[str, Any]]]:
    """Classify newest record per exact identity key without deleting history."""
    try:
        as_of_date = date.fromisoformat(as_of)
    except ValueError as exc:
        raise DatasetError(f"invalid freshness as_of: {as_of}") from exc
    if max_age_days <= 0:
        raise DatasetError("freshness max_age_days must be positive")
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    unknown = []
    for record in records:
        row = dict(record)
        value = evidence_date(row, date_fields)
        row["evidence_date"] = value
        row["freshness_policy_id"] = policy_id
        if value is None:
            row["age_days"] = None
            row["freshness_status"] = "unknown_date"
            row["freshness_reason"] = "no_parseable_evidence_date"
            unknown.append(row)
            continue
        age_days = (as_of_date - date.fromisoformat(value)).days
        row["age_days"] = age_days
        key = tuple(row.get(field) for field in key_fields)
        grouped.setdefault(key, []).append(row)
    classified = list(unknown)
    for _, rows in sorted(grouped.items(), key=lambda item: repr(item[0])):
        ordered = sorted(
            rows,
            key=lambda row: (row["evidence_date"], str(row.get("record_id") or row.get("id") or "")),
            reverse=True,
        )
        newest = ordered[0]
        if newest["age_days"] < 0:
            newest["freshness_status"] = "future_dated"
            newest["freshness_reason"] = "evidence_date_after_snapshot_as_of"
        elif newest["age_days"] <= max_age_days:
            newest["freshness_status"] = "active_current"
            newest["freshness_reason"] = "latest_exact_identity_within_window"
        else:
            newest["freshness_status"] = "stale"
            newest["freshness_reason"] = "latest_exact_identity_outside_window"
        classified.append(newest)
        for older in ordered[1:]:
            older["freshness_status"] = "superseded"
            older["freshness_reason"] = f"superseded_by_evidence_date:{newest['evidence_date']}"
            classified.append(older)
    current = sorted(
        (row for row in classified if row["freshness_status"] == "active_current"),
        key=lambda row: tuple(str(row.get(field)) for field in key_fields),
    )
    history = sorted(
        (row for row in classified if row["freshness_status"] != "active_current"),
        key=lambda row: tuple(str(row.get(field)) for field in key_fields) + (str(row.get("evidence_date")),),
    )
    return {"current": current, "history": history, "all_records": current + history}


def link_current_model_configs(
    configs: Sequence[dict[str, Any]],
    model_catalog: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Link source configs to one current catalog identity by exact longest prefix."""
    current_ids = sorted(
        {
            str(row["model_id"])
            for row in model_catalog
            if row.get("status", "current") == "current" and row.get("model_id")
        },
        key=lambda value: (-len(value), value),
    )
    linked = []
    for config in configs:
        row = dict(config)
        config_id = str(row.get("normalized_model_config_id") or "")
        matches = [
            model_id for model_id in current_ids
            if config_id == model_id or config_id.startswith(model_id + "-")
        ]
        if matches:
            longest_length = len(matches[0])
            longest = [item for item in matches if len(item) == longest_length]
        else:
            longest = []
        if len(longest) == 1:
            row["base_model_id"] = longest[0]
            row["catalog_link_status"] = "linked_current_catalog"
        elif len(longest) > 1:
            row["base_model_id"] = None
            row["catalog_link_status"] = "ambiguous_current_catalog"
        else:
            row["base_model_id"] = None
            row["catalog_link_status"] = "unlinked_current_catalog"
        linked.append(row)
    return linked
