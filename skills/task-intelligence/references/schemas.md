# Machine schemas (v0.1)

These are the ONLY allowed output structures. Unknown fields are rejected by
`scripts/validate_analysis.py`. Enums are ICLE's fixed standards.

## SessionAnalysisProposal

```json
{
  "schema_version": "icle-session-analysis/v0.1",
  "session_id": "<string>",
  "status": "ok",
  "facts": [
    {"text": "<observed fact>", "evidence": ["e81"]}
  ],
  "inferences": [
    {"text": "<derived>", "evidence": ["e81","e83"], "confidence": 0.8}
  ],
  "proposals": [
    {"text": "<recommendation>"}
  ],
  "confidence": {
    "boundary": 0.0,
    "intent": 0.0,
    "outcome": 0.0,
    "task_profile": 0.0
  },
  "tasks": [
    {
      "candidate_id": "c1",
      "boundaries": {"start_event": "e132", "end_event": "e284"},
      "title": "<short>",
      "goal": "<one sentence>",
      "original_request": "<first ask>",
      "final_request": "<final ask, may differ>",
      "task_type": "CODING",
      "subtype": "refactor",
      "difficulty": "D2",
      "risk": "R1",
      "constraints": ["<string>"],
      "success_criteria": ["<string>"],
      "known_facts": ["<string>"],
      "unknowns": ["<string>"],
      "decisions": ["<string>"],
      "assumptions": ["<string>"],
      "attempts": ["<string>"],
      "failures": ["<string>"],
      "corrections": ["<string>"],
      "artifacts": ["<string>"],
      "outcome": {"status": "completed|failed|unknown", "summary": "<string>"},
      "user_feedback": ["<string>"],
      "evidence_refs": ["e132","e145"],
      "confidence": {"boundary": 0.0, "intent": 0.0, "outcome": 0.0, "task_profile": 0.0}
    }
  ]
}
```

Refusal: `{"status": "insufficient_evidence", "session_id": "...", "reason": "<string>"}`

## Enums (fixed, never invented)

- task_type: CODING | RESEARCH | ANALYSIS | WRITING | PLANNING | DATA | SYSTEM_OPERATION | MULTIMODAL | OTHER
- subtype: implementation | debugging | refactor | review | testing | architecture | integration | search | literature | comparison | verification | synthesis | project | workflow | decision | decomposition
- difficulty: D1..D5
- risk: R0..R3
- outcome.status: completed | failed | unknown

## confidence

All confidence values are 0..1 floats. Multi-dimensional: boundary / intent /
outcome / task_profile — each measures a DIFFERENT thing; a high boundary
confidence does not imply a high profile confidence.

## TaskLedger (plan-task input stage)

```json
{
  "goal": "<string>",
  "given_facts": ["<string>"],
  "verified_facts": ["<string>"],
  "facts_to_lookup": ["<string>"],
  "facts_to_derive": ["<string>"],
  "constraints": ["<string>"],
  "unknowns": ["<string>"],
  "assumptions": ["<string>"],
  "required_outputs": ["<string>"],
  "completion_contract": ["<string>"]
}
```

## PlanProposal (plan-task output, 1-3 candidates)

```json
{
  "schema_version": "icle-task-plan-proposal/v0.1",
  "ledger": { "goal": "...", "given_facts": [], "verified_facts": [], "facts_to_lookup": [], "facts_to_derive": [], "constraints": [], "unknowns": [], "assumptions": [], "required_outputs": [], "completion_contract": [] },
  "candidates": [
    {
      "candidate_id": "A",
      "execution_pattern": "DIRECT",
      "summary": "<one sentence>",
      "expected_complexity": "low",
      "context_requirement": "MEDIUM",
      "estimated_calls": "2-4",
      "steps": [
        {
          "step_id": "S1",
          "goal": "<what this step achieves>",
          "inputs": ["<string>"],
          "expected_outputs": ["<string>"],
          "dependencies": [],
          "required_capabilities": ["coding"],
          "context_policy": "CLEAN",
          "tools": [],
          "verification": {"type": "test|manual|review|notes", "criteria": ["<string>"]},
          "risk": "R1",
          "estimated_complexity": "low",
          "parallel_group": null
        }
      ]
    }
  ]
}
```

Rules:
- 1-3 candidates, candidate_id A/B/C.
- execution_pattern ∈ DIRECT | PLAN_FIRST | DECOMPOSE | AUTHOR_REVIEWER | PARALLEL_COMPARE | HANDOFF.
- **No `suggested_agent` field anywhere** — capabilities only.
- steps: unique step_id, dependencies form a DAG, every step has verification + expected_outputs.

## ReplanProposal (replan-task output)

```json
{
  "schema_version": "icle-replan-proposal/v0.1",
  "status": "complete|continue|replan_needed|blocked|insufficient_info",
  "reasons": ["<string>"],
  "kept_step_ids": ["S1", "S2"],
  "replaced_steps": [
    {"step_id": "S3", "goal": "...", "inputs": [], "expected_outputs": [], "dependencies": ["S2"], "required_capabilities": [], "context_policy": "CLEAN", "tools": [], "verification": {"type": "test", "criteria": []}, "risk": "R1", "estimated_complexity": "low", "parallel_group": null}
  ],
  "new_steps": [
    {"step_id": "S4", "goal": "...", "inputs": [], "expected_outputs": [], "dependencies": ["S3"], "required_capabilities": [], "context_policy": "CLEAN", "tools": [], "verification": {"type": "test", "criteria": []}, "risk": "R1", "estimated_complexity": "low", "parallel_group": null}
  ]
}
```

## TaskSplitProposal (split-task output)

### Stage 1 — propose (2 candidates on different dimensions)

```json
{
  "schema_version": "icle-task-split-proposal/v0.2",
  "parent_task_id": "t-xxxx",
  "needs_split": true,
  "reason": "<why splitting helps>",
  "candidates": [
    {
      "candidate_id": "c1",
      "approach": "<dimension label, e.g. By module>",
      "rationale": "<one paragraph: why this dimension fits>",
      "children": [
        {"title": "<string>", "description": "<string>", "reason": "<one line>"}
      ]
    },
    {
      "candidate_id": "c2",
      "approach": "<different dimension>",
      "rationale": "<one paragraph>",
      "children": [...]
    }
  ]
}
```

Rules:
- Exactly TWO candidates, on *different* dimensions (module vs phase vs
  component vs dependency flow — you choose the two that fit best).
- children per candidate: 1..8;every child needs non-empty `title` and a
  one-line `reason`.
- `needs_split: false` → candidates = [] and `reason` explains why the task
  should stay whole (refuse instead of fabricating a split).
- Children are real independent tasks (profile/plan/run/result), NOT checklist
  items; they inherit project_id only.

### Stage 2 — refine (elaborate ONE chosen candidate)

```json
{
  "schema_version": "icle-task-split-proposal/v0.2",
  "parent_task_id": "t-xxxx",
  "candidate_id": "c1",
  "approach": "<kept from chosen candidate>",
  "rationale": "<kept>",
  "children": [
    {"title": "<concrete>", "description": "<specific>", "reason": "<one line>"}
  ]
}
```

Same rules as stage 1 (children 1..8, non-empty title + reason).

## TemplatePlanProposal (template-customize output)

```json
{
  "schema_version": "icle-template-plan/v0.1",
  "template_id": "bugfix",
  "fits_template": true,
  "reason": "<why fits / why not>",
  "children": [
    {"key": "reproduce", "title": "<concrete>", "description": "<task-specific>", "optional": false}
  ]
}
```

Rules:
- `children` keys must be the EXACT set from the chosen template — no adds,
  no removals, no renames.
- `optional` must be copied verbatim from the template (the LLM never decides).
- `fits_template: false` → children = [] and `reason` explains the mismatch.

## TaskProfileProposal (analyze-task output)

```json
{
  "primary_type": "CODING",
  "subtype": "implementation",
  "difficulty": "D2",
  "risk": "R1",
  "context_requirement": "LOW",
  "tool_requirement": ["filesystem"],
  "estimated_duration": "short",
  "decomposition": "not_recommended",
  "review": "not_recommended",
  "reason": "<one sentence>"
}
```

Rules:
- Enums only from `profiling.md`. Unknown values are rejected.
- `subtype` is empty string when the primary type has no subtype list.
- `tool_requirement` is a list; each item ∈ filesystem|shell|git|web|mcp|none.

## RatingProposal (judge-result output)

```json
{
  "dimensions": {
    "requirement_fit": 4,
    "correctness": 4,
    "efficiency": 3,
    "autonomy": 4,
    "maintainability": 3
  },
  "overall_preference": 4,
  "would_use_again": "yes",
  "comment": "<one sentence>"
}
```

All dimension values and `overall_preference` are integers 1..5.
`would_use_again` ∈ yes|maybe|no. No other keys.

## PairVerdict (judge-pair output)

```json
{
  "verdict": "prefer_a",
  "reason": "<one sentence>",
  "confidence": 0.7,
  "reason_tags": ["correctness"]
}
```

- verdict ∈ prefer_a|prefer_b|tie|inconclusive
- reason_tags ⊂ {correctness, completeness, style, speed, cost, initiative, understanding, maintainability}

## ReportNarrative (summarize-reports output)

```json
{
  "summary": "<one short paragraph of what the agents did together>",
  "blocked_reason": ""
}
```

Only these two keys. Counts and flags are supplied in the payload and must
not be repeated or changed.
