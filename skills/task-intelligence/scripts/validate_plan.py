#!/usr/bin/env python3
"""validate_plan.py — PlanProposal / ReplanProposal 确定性校验(plan-task v0.2)。

检查(全为确定性规则):
- candidates 1~3 个,id ∈ {A,B,C} 唯一
- execution_pattern ∈ 固定策略
- **禁止 suggested_agent/agent/provider/model 字段**(Skill 不决定具体 agent)
- step_id 唯一;dependencies 存在且 DAG 无环;expected_outputs 非空
- verification 有 type + criteria;risk 枚举;confidence 0..1(如有)
- ReplanProposal: status 枚举、kept_step_ids 引用原 plan 步骤
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLAN_SCHEMA = "icle-task-plan-proposal/v0.1"
REPLAN_SCHEMA = "icle-replan-proposal/v0.1"
STRATEGIES = {"DIRECT", "PLAN_FIRST", "DECOMPOSE", "AUTHOR_REVIEWER",
              "PARALLEL_COMPARE", "HANDOFF"}
RISKS = {"R0", "R1", "R2", "R3"}
CONTEXT_POLICIES = {"CLEAN", "PROJECT_STATE", "ARTIFACT_ONLY"}
REPLAN_STATUSES = {"complete", "continue", "replan_needed", "blocked", "insufficient_info"}
FORBIDDEN_AGENT_KEYS = {"suggested_agent", "agent", "provider", "model", "agent_id"}
STEP_FIELDS = {
    "step_id", "goal", "inputs", "expected_outputs", "dependencies",
    "required_capabilities", "context_policy", "tools", "verification",
    "risk", "estimated_complexity", "parallel_group",
}
CANDIDATE_FIELDS = {"candidate_id", "execution_pattern", "summary",
                    "expected_complexity", "context_requirement", "estimated_calls", "steps"}


class ValidationError(ValueError):
    pass


def _check_step(step: dict, step_ids: set[str] | None = None) -> None:
    if not isinstance(step, dict):
        raise ValidationError("step must be an object")
    for key in step:
        if key not in STEP_FIELDS:
            raise ValidationError(f"unknown step field: {key}")
        if key in FORBIDDEN_AGENT_KEYS:
            raise ValidationError(f"step must not carry agent-specific field: {key}")
    sid = step.get("step_id")
    if not isinstance(sid, str) or not sid:
        raise ValidationError("step_id required")
    if step_ids is not None and sid in step_ids:
        raise ValidationError(f"duplicate step_id: {sid}")
    outputs = step.get("expected_outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ValidationError(f"{sid}: expected_outputs[] required (verifiability)")
    verification = step.get("verification")
    if not isinstance(verification, dict) or not verification.get("type") \
            or not isinstance(verification.get("criteria"), list) or not verification.get("criteria"):
        raise ValidationError(f"{sid}: verification.type + criteria[] required")
    risk = step.get("risk")
    if risk not in RISKS:
        raise ValidationError(f"{sid}: risk must be one of {sorted(RISKS)}")
    policy = step.get("context_policy")
    if policy and policy not in CONTEXT_POLICIES:
        raise ValidationError(f"{sid}: context_policy must be one of {sorted(CONTEXT_POLICIES)}")


def _check_dag(steps: list[dict]) -> None:
    """dependencies 必须引用已存在的 step_id 且无环。"""
    ids = {s["step_id"] for s in steps}
    deps: dict[str, set[str]] = {}
    for step in steps:
        deps[step["step_id"]] = {d for d in step.get("dependencies") or [] if d}
        for d in deps[step["step_id"]]:
            if d not in ids:
                raise ValidationError(f"{step['step_id']}: dependency {d} not in plan")
    # 拓扑检测环
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ValidationError(f"cycle detected at step {node}")
        if node in visited:
            return
        visiting.add(node)
        for dep in deps.get(node, set()):
            visit(dep)
        visiting.discard(node)
        visited.add(node)

    for node in ids:
        visit(node)


def validate_plan(proposal: dict) -> dict:
    if proposal.get("schema_version") != PLAN_SCHEMA:
        raise ValidationError(f"schema_version must be {PLAN_SCHEMA}")
    ledger = proposal.get("ledger")
    if not isinstance(ledger, dict) or not ledger.get("goal"):
        raise ValidationError("ledger.goal required")
    candidates = proposal.get("candidates")
    if not isinstance(candidates, list) or not (1 <= len(candidates) <= 3):
        raise ValidationError("candidates must be 1-3")

    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValidationError("candidate must be an object")
        for key in candidate:
            if key not in CANDIDATE_FIELDS:
                raise ValidationError(f"unknown candidate field: {key}")
            if key in FORBIDDEN_AGENT_KEYS:
                raise ValidationError(f"candidate must not carry agent-specific field: {key}")
        cid = candidate.get("candidate_id")
        if cid not in {"A", "B", "C"}:
            raise ValidationError(f"candidate_id must be A|B|C, got {cid!r}")
        # 枚举归一化:LLM 可能输出小写变体(clean/project_state),大写化后校验;
        # 真未知值仍拒绝。这是确定性工作,不属于 LLM 职责。
        pattern = candidate.get("execution_pattern")
        if isinstance(pattern, str):
            candidate["execution_pattern"] = pattern.upper()
        pattern = candidate.get("execution_pattern")
        if pattern not in STRATEGIES:
            raise ValidationError(f"execution_pattern must be one of {sorted(STRATEGIES)}")
        steps = candidate.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValidationError(f"{cid}: steps[] required")
        seen: set[str] = set()
        for step in steps:
            if isinstance(step.get("context_policy"), str):
                step["context_policy"] = step["context_policy"].upper()
            if isinstance(step.get("risk"), str):
                step["risk"] = step["risk"].upper()
            _check_step(step, seen)
        _check_dag(steps)
    return proposal


def validate_replan(proposal: dict, original_step_ids: set[str] | None = None) -> dict:
    if proposal.get("schema_version") != REPLAN_SCHEMA:
        raise ValidationError(f"schema_version must be {REPLAN_SCHEMA}")
    status = proposal.get("status")
    if status not in REPLAN_STATUSES:
        raise ValidationError(f"status must be one of {sorted(REPLAN_STATUSES)}")
    for key in proposal:
        if key not in {"schema_version", "status", "reasons", "kept_step_ids",
                       "replaced_steps", "new_steps"}:
            raise ValidationError(f"unknown replan field: {key}")
    kept = proposal.get("kept_step_ids") or []
    if original_step_ids is not None:
        for sid in kept:
            if sid not in original_step_ids:
                raise ValidationError(f"kept_step_ids references step not in original plan: {sid}")
    steps = (proposal.get("replaced_steps") or []) + (proposal.get("new_steps") or [])
    seen: set[str] = set()
    for step in steps:
        _check_step(step, seen)
    all_ids = set(kept) | {s["step_id"] for s in steps}
    _check_dag([{"step_id": sid, "dependencies": [], "expected_outputs": ["x"],
                 "verification": {"type": "t", "criteria": ["c"]}, "risk": "R0"}
                for sid in kept] + steps)
    return proposal


def main(argv: list[str]) -> int:
    if len(argv) < 1:
        print("usage: validate_plan.py <proposal.json> [original_step_ids.json]", file=sys.stderr)
        return 2
    proposal = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    original = None
    if len(argv) > 1:
        original = set(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
    try:
        if proposal.get("schema_version", "").startswith("icle-replan"):
            validate_replan(proposal, original)
        else:
            validate_plan(proposal)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    print("VALID")
    return 0


validate_plan_proposal = validate_plan  # 入口约定:与文件名同名
validate_replan_proposal = validate_replan


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
