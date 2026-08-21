#!/usr/bin/env python3
"""validate_report_summary.py — ReportNarrative (summarize-reports)."""

from __future__ import annotations

ALLOWED = {"summary", "blocked_reason"}


class ValidationError(ValueError):
    pass


def validate_report_summary(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValidationError("narrative must be an object")
    unknown = sorted(set(proposal) - ALLOWED)
    if unknown:
        raise ValidationError(f"narrative may not carry {unknown[0]}")
    summary = str(proposal.get("summary") or "").strip()
    if not summary:
        raise ValidationError("summary required")
    return {
        "summary": summary[:1000],
        "blocked_reason": str(proposal.get("blocked_reason") or "")[:1000],
    }
