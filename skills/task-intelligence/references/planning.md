# planning procedure (v0.2)

PLANNING MODE IS READ-ONLY. You only read/inspect and output proposals; you
never edit, deploy, delete, or execute anything.

## pipeline

1. **Task Ledger first** (borrowed from Magentic-One). Before proposing any
   step, state:
   - `goal` — the outcome the user wants
   - `given_facts[]` — what is already known
   - `verified_facts[]` — what has been verified
   - `facts_to_lookup[]` — what must be looked up
   - `facts_to_derive[]` — what must be derived
   - `constraints[] / unknowns[] / assumptions[]`
   - `required_outputs[] / completion_contract[]` — how completion is checked
   Also consider: project_state, risk, budget, available agents (as capability
   names, not concrete agents), available tools.

2. **Decide whether decomposition is needed at all** — do NOT over-plan:
   - D1 → DIRECT
   - D2 → default DIRECT
   - D3 → DIRECT / PLAN_FIRST
   - D4 → PLAN_FIRST / DECOMPOSE
   - D5 → DECOMPOSE
   Plus decomposition signals: multiple independent deliverables? cross-
   capability? strong dependencies? obvious verification points? any phase that
   can run in parallel? risk that requires review? different capabilities needed?
   If none apply: DO NOT decompose. A trivial single-file task must not enter a
   heavy planning flow.

3. **Emit 1-3 Plan Candidates** (A/B/C), NOT one plan:
   - Candidate A — Direct: one agent, cheap, fast.
   - Candidate B — Plan + Implement (e.g. analysis then implementation).
   - Candidate C — Author + Reviewer.
   The planner decides the *semantic structure*; ICLE's CostEngine and
   RecommendationEngine sort them later. Never pick a concrete agent, provider,
   or model — use `required_capabilities` and `recommended_execution_pattern`.

4. **Every step must be independently verifiable**:
   - small enough to execute, verify, and judge complete on its own
   - `verification.type` + `verification.criteria[]` present
   - `dependencies[]` reference existing `step_id`s and form a DAG (no cycles)

5. **Never calculate authoritative cost** — output `expected_complexity`,
   `context_requirement`, `estimated_calls`; ICLE computes money.

## replan-task (v0.3)

Keep the original ledger and plan; do NOT start from zero.

1. Classify the situation first:
   - task complete? → status `complete`
   - progress being made? → status `continue`
   - blocked (agent unavailable / tool missing / budget exceeded)?
   - looping (repeated same failure)?
   - assumption invalid?
2. Keep still-valid steps (`kept_step_ids`), replan only the invalid tail.
3. Trigger replan ONLY on: step failed / verification failed / timeout /
   repeated same failure / agent unavailable / user correction / budget threshold.

## Fact / Inference / Proposal separation

Never collapse into a single `findings` list. Facts are observed; inferences are
derived (with confidence); proposals are recommendations. ProjectState, Planner
and Judge all consume this distinction.
