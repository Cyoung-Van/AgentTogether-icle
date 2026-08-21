#!/usr/bin/env python3
"""validate_verdict.py — PairVerdict (judge-pair)."""

from __future__ import annotations

VERDICTS = {"prefer_a", "prefer_b", "tie", "inconclusive"}
TAGS = {
    "correctness", "completeness", "style", "speed",
    "cost", "initiative", "understanding", "maintainability",
}
ALLOWED = {"verdict", "reason", "confidence", "reason_tags"}


class ValidationError(ValueError):
    pass


def validate_verdict(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValidationError("verdict must be an object")
    unknown = sorted(set(proposal) - ALLOWED)
    if unknown:
        raise ValidationError(f"unknown verdict field: {unknown[0]}")
    verdict = proposal.get("verdict")
    if verdict not in VERDICTS:
        raise ValidationError(f"verdict must be one of {sorted(VERDICTS)}")
    reason = str(proposal.get("reason") or "").strip()
    if not reason:
        raise ValidationError("reason required")
    confidence = proposal.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValidationError("confidence must be a number")
    if not 0 <= float(confidence) <= 1:
        raise ValidationError("confidence must be 0..1")
    tags = proposal.get("reason_tags") or []
    if not isinstance(tags, list) or any(tag not in TAGS for tag in tags):
        raise ValidationError("reason_tags must be a subset of the fixed list")
    return {
        "verdict": verdict,
        "reason": reason[:500],
        "confidence": float(confidence),
        "reason_tags": list(tags),
    }
