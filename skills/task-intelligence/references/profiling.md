# analyze-task — TaskProfile

You fill ICLE's fixed standard. You never invent a category. Unknown values
are rejected by `scripts/validate_profile.py`, not rewritten.

## Enums

- `primary_type`: CODING | RESEARCH | ANALYSIS | WRITING | PLANNING | DATA | SYSTEM_OPERATION | MULTIMODAL | OTHER
- `subtype` by primary type:
  - CODING → implementation | debugging | refactor | review | testing | architecture | integration
  - RESEARCH → search | literature | comparison | verification | synthesis
  - PLANNING → project | architecture | workflow | decision | decomposition
  - anything else → empty string
- `difficulty`: D1 | D2 | D3 | D4 | D5
- `risk`: R0 | R1 | R2 | R3
- `context_requirement`: LOW | MEDIUM | HIGH
- `tool_requirement[]`: filesystem | shell | git | web | mcp | none
- `estimated_duration`: short | medium | long
- `decomposition`: not_recommended | recommended | required
- `review`: not_recommended | recommended | required

## Difficulty vs risk

Independent axes. Do not raise risk because a task is long, and do not raise
difficulty because a task touches production.

- D1 single step · D2 2–4 steps · D3 multi-step with exploration · D4 multiple modules / uncertainty · D5 open-ended long horizon
- R0 read only · R1 reversible edits · R2 wide / API / db changes · R3 credentials or production

## Reason

One sentence explaining difficulty and risk. Prose follows the UI language.
Keep the enum tokens themselves untranslated.
