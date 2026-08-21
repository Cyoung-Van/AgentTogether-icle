#!/usr/bin/env python3
"""validate_profile.py — TaskProfileProposal (analyze-task)."""

from __future__ import annotations

PRIMARY = {
    "CODING", "RESEARCH", "ANALYSIS", "WRITING", "PLANNING",
    "DATA", "SYSTEM_OPERATION", "MULTIMODAL", "OTHER",
}
SUBTYPE_BY_PRIMARY = {
    "CODING": {
        "implementation", "debugging", "refactor", "review",
        "testing", "architecture", "integration",
    },
    "RESEARCH": {"search", "literature", "comparison", "verification", "synthesis"},
    "PLANNING": {"project", "architecture", "workflow", "decision", "decomposition"},
}
DIFFICULTIES = {"D1", "D2", "D3", "D4", "D5"}
RISKS = {"R0", "R1", "R2", "R3"}
CONTEXTS = {"LOW", "MEDIUM", "HIGH"}
TOOLS = {"filesystem", "shell", "git", "web", "mcp", "none"}
DURATIONS = {"short", "medium", "long"}
ADVICE = {"not_recommended", "recommended", "required"}
ALLOWED = {
    "primary_type", "subtype", "difficulty", "risk", "context_requirement",
    "tool_requirement", "estimated_duration", "decomposition", "review", "reason",
}


class ValidationError(ValueError):
    pass


def validate_profile(proposal: dict) -> dict:
    if not isinstance(proposal, dict):
        raise ValidationError("profile must be an object")
    unknown = sorted(set(proposal) - ALLOWED)
    if unknown:
        raise ValidationError(f"unknown profile field: {unknown[0]}")
    primary = proposal.get("primary_type")
    if primary not in PRIMARY:
        raise ValidationError(f"primary_type must be one of {sorted(PRIMARY)}")
    subtype = proposal.get("subtype") or ""
    allowed_sub = SUBTYPE_BY_PRIMARY.get(primary, set())
    if allowed_sub:
        if subtype not in allowed_sub:
            raise ValidationError(f"subtype {subtype!r} is not valid for {primary}")
    elif subtype:
        raise ValidationError(f"{primary} does not take a subtype")
    if proposal.get("difficulty") not in DIFFICULTIES:
        raise ValidationError("difficulty must be D1..D5")
    if proposal.get("risk") not in RISKS:
        raise ValidationError("risk must be R0..R3")
    if proposal.get("context_requirement") not in CONTEXTS:
        raise ValidationError("context_requirement must be LOW|MEDIUM|HIGH")
    tools = proposal.get("tool_requirement")
    if not isinstance(tools, list) or not tools:
        raise ValidationError("tool_requirement must be a non-empty list")
    if any(item not in TOOLS for item in tools):
        raise ValidationError("tool_requirement contains an unknown tool")
    if proposal.get("estimated_duration") not in DURATIONS:
        raise ValidationError("estimated_duration must be short|medium|long")
    if proposal.get("decomposition") not in ADVICE:
        raise ValidationError("decomposition must be not_recommended|recommended|required")
    if proposal.get("review") not in ADVICE:
        raise ValidationError("review must be not_recommended|recommended|required")
    reason = proposal.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("reason must be a non-empty string")
    return proposal
