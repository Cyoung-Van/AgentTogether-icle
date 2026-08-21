#!/usr/bin/env python3
"""validate_rating.py — RatingProposal (judge-result)."""

from __future__ import annotations

DIMENSIONS = (
    "requirement_fit", "correctness", "efficiency", "autonomy", "maintainability",
)
ALLOWED = {"dimensions", "overall_preference", "would_use_again", "comment"}


class ValidationError(ValueError):
    pass


def _score(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
        raise ValidationError(f"{where} must be an integer 1..5")
    return value


def validate_rating(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValidationError("rating must be an object")
    unknown = sorted(set(proposal) - ALLOWED)
    if unknown:
        raise ValidationError(f"unknown rating field: {unknown[0]}")
    dimensions = proposal.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ValidationError("dimensions must be an object")
    extra = sorted(set(dimensions) - set(DIMENSIONS))
    if extra:
        raise ValidationError(f"unknown dimension: {extra[0]}")
    clean = {name: _score(dimensions.get(name), name) for name in DIMENSIONS}
    overall = _score(proposal.get("overall_preference"), "overall_preference")
    again = proposal.get("would_use_again")
    if again not in {"yes", "maybe", "no"}:
        raise ValidationError("would_use_again must be yes|maybe|no")
    comment = str(proposal.get("comment") or "").strip()
    if not comment:
        raise ValidationError("comment required")
    return {
        "dimensions": clean,
        "overall_preference": overall,
        "would_use_again": again,
        "comment": comment[:500],
    }
