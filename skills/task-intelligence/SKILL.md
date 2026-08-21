---
name: icle-task-intelligence
version: 0.2.0
description: Analyze agent conversation/session trajectories into grounded task proposals, classify a task into ICLE's fixed profile, generate or revise execution plans, rate a result, compare two results, or write the narrative of a multi-agent report. Use when ICLE needs any intelligence-layer judgment that must stay a proposal.
---

# ICLE Task Intelligence

## Operating modes

- `analyze-session` — turn a captured agent session into grounded Task Candidates. This is also the only path for session → task extraction.
- `analyze-task` — classify one task into ICLE's fixed TaskProfile (type, difficulty, risk). Never invent categories.
- `plan-task` — turn an approved task into 1-3 execution Plan Candidates (v0.2).
- `replan-task` — revise a plan after failure/blocker (v0.3).
- `split-task` — propose splitting a task into independent child tasks (v0.5).
- `template-customize` — customize a fixed split-template for one concrete task (v0.6).
- `judge-result` — rate one agent result on the five fixed dimensions (1–5).
- `judge-pair` — compare two results of the same task; verdict only, not a score.
- `summarize-reports` — write only the narrative of a multi-agent evaluation form. Counts and flags are already decided.

## Core rules (apply to every mode)

1. Produce proposals, never mutate ICLE state. You only output JSON; ICLE decides to persist.
2. Ground every judgment in supplied evidence IDs. Never claim a fact without a reference.
3. Never invent missing events or facts. If evidence is missing, say so.
4. Separate facts, inference, assumptions, and recommendations. Do not merge them.
5. Use ICLE's fixed TaskType/Difficulty/Risk enums only; never invent new ones.
6. Do not execute work while planning. PLANNING MODE IS READ-ONLY.
7. Prefer the simplest execution strategy that can satisfy the task.
8. Make every plan step independently verifiable.
9. Never choose a specific agent, provider, or model; output required capabilities only.
10. Never calculate authoritative cost; output complexity/context/calls estimates only.
11. When evidence is insufficient, return a refusal status instead of fabricating.

## analyze-session

Read `references/session-analysis.md` for the full procedure; `references/schemas.md`
for the machine schema; run `scripts/compact_session.py` and
`scripts/validate_analysis.py` for the deterministic steps.

Summary: reconstruct what really happened — task boundaries, original vs final intent,
decisions, failures and corrections — and return a `SessionAnalysisProposal`
(every task with `evidence_refs` and per-dimension `confidence`).

## plan-task

Read `references/planning.md` and `references/schemas.md`.

Summary: build a Task Ledger first, decide whether decomposition is needed
(difficulty + decomposition signals), then emit 1-3 Plan Candidates with
`required_capabilities` and `recommended_execution_pattern` — never concrete agents.

## replan-task

Read `references/planning.md` and `references/schemas.md`.

Summary: keep the original ledger and plan, classify the failure, keep still-valid
steps, and locally replan only the invalid part.

## split-task

Read `references/planning.md` (decomposition decision rules) and `references/schemas.md`.

Two-stage flow — you decide the dimensions, the user decides the final split:

- **Stage 1 (propose):** propose exactly TWO plausible decomposition candidates
  on *different* dimensions (e.g. by independently deliverable module vs by
  lifecycle phase vs by component vs by dependency flow). Each candidate has an
  `approach` label, a one-paragraph `rationale` (why this dimension fits), and
  1-8 child tasks. Children are rough sketches at this stage.
- **Stage 2 (refine):** given ONE chosen candidate, elaborate it into concrete,
  well-scoped children (tighten titles, add descriptions, adjust count).

Each child is a real ICLE Task: independent profile/plan/run/result — NOT a
checklist. Children inherit project_id only. Never propose more than 8 children
per candidate; every child needs a one-line `reason`. If a task does not
genuinely benefit from splitting (single deliverable, tiny scope), refuse with
`needs_split=false` and a reason instead of fabricating a split.

**Child count is NOT fixed.** Split only as far as the task genuinely warrants:
small tasks may yield 2, large ones up to 8, usually 3-6. Never pad to a round
number and never force every candidate to the same count.

## template-customize

Read `references/schemas.md` (TemplatePlanProposal).

The user picked a fixed template (feature/bugfix/refactor/migration/review/
release/incident/research/data_analysis/security_audit/documentation/
production_readiness). Your job is semantic customization ONLY:

- Keep the exact set of child `key`s from the template — do NOT add, remove,
  reorder or rename keys.
- Rewrite each child's `title`/`description` so they are concrete for THIS
  task (e.g. key `reproduce` → "Reproduce by submitting
  POST /api/tasks/{id}/children twice and observing duplicate rows").
- Mark nothing as optional yourself — keep the template's `optional` flags.
- If the task clearly does not fit the template, say so by returning
  `fits_template: false` with a reason (the UI then lets the user pick another
  template) — never force a bad fit.

## analyze-task

Read `references/profiling.md` and `references/schemas.md` (TaskProfileProposal).

Classify one task into ICLE's fixed enums. You fill the standard; you never
define it. Difficulty and risk are independent. Refuse invented categories.

## judge-result

Read `references/judging.md` and `references/schemas.md` (RatingProposal).

Rate the given result 1–5 on the five fixed dimensions. No extra keys. A
missing result is `insufficient_evidence`, not a fabricated 3.

## judge-pair

Read `references/judging.md` and `references/schemas.md` (PairVerdict).

Compare two results of the SAME task. Verdict is prefer_a / prefer_b / tie /
inconclusive. Tags are a subset of the fixed list. Do not invent a sixth
dimension.

## summarize-reports

Read `references/reports.md` and `references/schemas.md` (ReportNarrative).

Counts, flags, file lists, and commands are already merged and authoritative.
You may rewrite only `summary` and `blocked_reason`. Copying or changing a
count is a contract violation.
