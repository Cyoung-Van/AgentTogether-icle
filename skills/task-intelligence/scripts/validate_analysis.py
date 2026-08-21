#!/usr/bin/env python3
"""validate_analysis.py — SessionAnalysisProposal 确定性校验(analyze-session Step 6)。

检查(全为确定性规则,不依赖 LLM):
- status 合法(ok | insufficient_evidence)
- evidence refs 必须存在于输入事件集(event_id 集合)
- boundaries start <= end 且都存在
- task_type / subtype / difficulty / risk 枚举合法
- confidence 0..1
- 白名单字段:未知字段拒绝
- 不引用输入之外的 session
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA = "icle-session-analysis/v0.1"
TASK_TYPES = {"CODING", "RESEARCH", "ANALYSIS", "WRITING", "PLANNING", "DATA",
              "SYSTEM_OPERATION", "MULTIMODAL", "OTHER"}
SUBTYPES = {"implementation", "debugging", "refactor", "review", "testing",
            "architecture", "integration", "search", "literature", "comparison",
            "verification", "synthesis", "project", "workflow", "decision", "decomposition"}
DIFFICULTIES = {"D1", "D2", "D3", "D4", "D5"}
RISKS = {"R0", "R1", "R2", "R3"}
OUTCOME_STATUSES = {"completed", "failed", "unknown"}
TASK_FIELDS = {
    "candidate_id", "boundaries", "title", "goal", "original_request", "final_request",
    "task_type", "subtype", "difficulty", "risk", "constraints", "success_criteria",
    "known_facts", "unknowns", "decisions", "assumptions", "attempts", "failures",
    "corrections", "artifacts", "outcome", "user_feedback", "evidence_refs", "confidence",
}
ROOT_FIELDS = {"schema_version", "session_id", "status", "facts", "inferences",
               "proposals", "confidence", "tasks", "reason"}
CONF_KEYS = {"boundary", "intent", "outcome", "task_profile"}


class ValidationError(ValueError):
    pass


def _event_ids(bundle: dict) -> set[str]:
    return {e["event_id"] for e in bundle.get("events", [])}


def _check_conf(conf, where: str) -> None:
    if not isinstance(conf, dict):
        raise ValidationError(f"{where}.confidence must be an object")
    for key, value in conf.items():
        if key not in CONF_KEYS:
            raise ValidationError(f"{where}.confidence unknown key: {key}")
        if not isinstance(value, (int, float)) or not (0 <= value <= 1):
            raise ValidationError(f"{where}.confidence.{key} must be 0..1, got {value!r}")


def validate(proposal: dict, bundle: dict) -> dict:
    """返回规范化后的 proposal;非法即抛 ValidationError。"""
    if not isinstance(proposal, dict):
        raise ValidationError("proposal must be a JSON object")
    if proposal.get("schema_version") != SCHEMA:
        raise ValidationError(f"schema_version must be {SCHEMA}")
    status = proposal.get("status")
    if status not in ("ok", "insufficient_evidence"):
        raise ValidationError(f"status must be ok|insufficient_evidence, got {status!r}")

    ids = _event_ids(bundle)

    # 白名单字段
    for key in proposal:
        if key not in ROOT_FIELDS:
            raise ValidationError(f"unknown root field: {key}")
    if proposal.get("session_id") and bundle.get("session_id") \
            and proposal["session_id"] != bundle["session_id"]:
        raise ValidationError("session_id does not match the input session")

    if status == "insufficient_evidence":
        if not isinstance(proposal.get("reason"), str):
            raise ValidationError("insufficient_evidence requires a reason")
        return proposal

    for conf_key in ("boundary", "intent", "outcome", "task_profile"):
        if not isinstance(proposal.get("confidence", {}).get(conf_key), (int, float)):
            raise ValidationError(f"root confidence.{conf_key} missing")
    _check_conf(proposal.get("confidence", {}), "root")

    tasks = proposal.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValidationError("status ok requires a non-empty tasks[]")

    seen_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise ValidationError("task must be an object")
        for key in task:
            if key not in TASK_FIELDS:
                raise ValidationError(f"unknown task field: {key}")
        cid = task.get("candidate_id")
        if not isinstance(cid, str) or not cid or cid in seen_ids:
            raise ValidationError(f"candidate_id must be a unique non-empty string, got {cid!r}")
        seen_ids.add(cid)

        bounds = task.get("boundaries")
        if not isinstance(bounds, dict) or not bounds.get("start_event") or not bounds.get("end_event"):
            raise ValidationError(f"{cid}: boundaries.start_event/end_event required")
        start, end = bounds["start_event"], bounds["end_event"]
        if start not in ids or end not in ids:
            raise ValidationError(f"{cid}: boundary events not in input: {start}..{end}")
        if int(start[1:]) > int(end[1:]):
            raise ValidationError(f"{cid}: start_event after end_event")

        for enum_field, allowed, label in (
            ("task_type", TASK_TYPES, "task_type"),
            ("difficulty", DIFFICULTIES, "difficulty"),
            ("risk", RISKS, "risk"),
        ):
            value = task.get(enum_field)
            if value not in allowed:
                raise ValidationError(f"{cid}: {label} must be one of {sorted(allowed)}, got {value!r}")
        subtype = task.get("subtype")
        if subtype and subtype not in SUBTYPES:
            raise ValidationError(f"{cid}: subtype not allowed: {subtype!r}")

        _check_conf(task.get("confidence", {}), f"{cid}")

        for field in ("title", "goal", "original_request", "final_request"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                raise ValidationError(f"{cid}: {field} required non-empty string")

        outcome = task.get("outcome")
        if not isinstance(outcome, dict) or outcome.get("status") not in OUTCOME_STATUSES:
            raise ValidationError(f"{cid}: outcome.status must be one of {sorted(OUTCOME_STATUSES)}")

        refs = task.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValidationError(f"{cid}: evidence_refs[] required (grounding rule)")
        for ref in refs:
            if ref not in ids:
                raise ValidationError(f"{cid}: evidence_ref not in input: {ref}")

    for section in ("facts", "inferences", "proposals"):
        for i, item in enumerate(proposal.get(section) or []):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"]:
                raise ValidationError(f"{section}[{i}]: text required")
            if section == "inferences" and not isinstance(item.get("confidence"), (int, float)):
                raise ValidationError(f"inferences[{i}]: confidence required")
            for ref in item.get("evidence") or []:
                if ref not in ids:
                    raise ValidationError(f"{section}[{i}]: evidence not in input: {ref}")

    return proposal


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: validate_analysis.py <proposal.json> <bundle.json>", file=sys.stderr)
        return 2
    proposal = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    bundle = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    try:
        validate(proposal, bundle)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print("VALID")
    return 0


validate_analysis = validate  # 入口约定:与文件名同名


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
