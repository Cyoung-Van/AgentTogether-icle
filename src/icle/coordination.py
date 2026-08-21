"""Coordination measurement contract: TeamRevision identity, no team total.

The seven LiveBench axes cannot host coordination. This module only names a
team and refuses to invent C_coord until solo baselines exist.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .evolution import CANONICAL_AXES

COORD_CONTRACT = "icle-coordination-team-revision/v0.1"
COORD_AXES = ("tool_invocation", "memory_retrieval", "context_seeking")


def team_revision_id(
    *,
    members: list[str],
    topology: str,
    orchestrator: str | None = None,
    policy: dict[str, Any] | None = None,
) -> str:
    """Stable hash of ordered members, topology, orchestrator and policy."""
    payload = {
        "members": [str(item) for item in members],
        "topology": str(topology),
        "orchestrator": orchestrator or "",
        "policy": policy or {},
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"team-{digest[:16]}"


def coordination_portrait(
    *,
    members: list[str],
    topology: str,
    orchestrator: str | None = None,
    policy: dict[str, Any] | None = None,
    solo_baselines: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only coordination portrait. Missing solo baseline keeps every axis unavailable."""
    revision = team_revision_id(
        members=members, topology=topology, orchestrator=orchestrator, policy=policy
    )
    reason = "solo_baseline_missing" if not solo_baselines else "coordination_measurement_unimplemented"
    axes = {
        axis: {
            "status": "unavailable",
            "mean": None,
            "c": None,
            "reason": reason,
        }
        for axis in COORD_AXES
    }
    return {
        "schema_version": "icle-coordination-portrait/v0.1",
        "contract_id": COORD_CONTRACT,
        "team_revision_id": revision,
        "members": list(members),
        "topology": topology,
        "orchestrator": orchestrator,
        "publication_status": "insufficient_evidence",
        "ranking": False,
        "axes": axes,
        "livebench_axes_not_used": list(CANONICAL_AXES),
        "not_claimed": [
            "team total score",
            "livebench coordination axis",
            "causal collaboration gain",
        ],
    }
