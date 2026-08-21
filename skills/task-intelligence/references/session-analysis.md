# analyze-session procedure (v0.1)

Task Reconstruction — NOT summarization. The goal is to recover the task structure
that produced the session: boundaries, intent (original vs final), decisions,
failures, corrections, and outcome. Every claim must be grounded in `evidence_refs`.

## Pipeline (6 steps)

1. **Deterministic compaction** (`scripts/compact_session.py`)
   - Drop noise: heartbeats, repeated streaming chunks, UI events, empty messages,
     repeated tool metadata, meaningless logs.
   - Keep: `event_id / turn_id / ts / role / content / tool / tool_result_summary /
     project / artifact refs`.
   - No LLM in this step.

2. **Rule-based coarse boundaries** (code, not LLM)
   - Signals that raise `boundary_probability` (never a verdict by itself):
     - large time gap between consecutive events
     - cwd/project switch
     - a new top-level user request after an assistant answer
     - artifact set changes significantly
     - agent explicitly announces "next, ..." / "接下来..."
   - Produce `PossibleBoundary[]` only.

3. **LLM semantic segmentation**
   - Input: compacted bundle + PossibleBoundary[] + fixed task definition.
   - Output `segments[]`; every segment must carry `start_event/end_event`,
     `title`, `goal`, `confidence`, and `evidence` refs.
   - Never output a task without evidence refs (LangExtract-style grounding).

4. **Task reconstruction** (per segment)
   - Recover: `original_request` AND `final_request` — real conversations often
     change intent ("add a Router" → "actually I want cost+experience routing").
     Reconstructing from the first user prompt alone is a known failure mode.
   - Also: `constraints / success_criteria / known_facts / unknowns /
     decisions / assumptions / attempts / failures / corrections / artifacts /
     outcome / user_feedback`.

5. **Behavioral signal extraction**
   - Beyond tasks, extract: `corrections[] / failures[] / preferences[] /
     decisions[] / rejected_approaches[] / successful_approaches[]`.
   - A correction: agent tried X → failed → switched to Y and succeeded
     (implicit correction signal, hindsight analysis).

6. **Output a Proposal only** — `SessionAnalysisProposal` (see schemas.md).
   - ICLE validates it, shows it to the user, and only after accept/edit does it
     create a TaskEpisode. Never persist directly.

## Grounding rules

- Every `facts[]`, `inferences[]`, `proposals[]` entry has `evidence`.
- Separate the three: facts (observed), inferences (derived, with confidence),
  proposals (recommendations).
- Multi-dimensional confidence: `boundary / intent / outcome / task_profile`.
  A clear boundary does not imply a confident difficulty estimate.

## Refusal

If the session does not contain enough signal to identify any task, return:
`{"status": "insufficient_evidence", ...}` instead of fabricating a task.

## Long sessions (Map → Merge → Verify)

Never feed thousands of events to one LLM call:
- Chunk by rules (boundaries), extract locally per chunk (`PartialAnalysis`),
- Merge partial analyses into task candidates,
- Run a contradiction/boundary pass to resolve overlaps and duplicates.
- Threshold handled by the caller; this reference only states the policy.
