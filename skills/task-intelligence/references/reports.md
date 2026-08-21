# summarize-reports

Used when the intelligence layer is on and a task has two or more observed
subtask forms. The deterministic merge already decided every count and flag.

You receive:

- `merged` — the authoritative form (counts, flags, lists)
- `entries` — each subtask's observed form

You return only:

- `summary` — what the agents did together, in the UI language
- `blocked_reason` — empty unless `merged.blocked` is true

Never change `requirements_total`, `requirements_met`, `tests_*`,
`verification_*`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
`reasoning_tokens`, `duration_s`, `files_changed`, `commands_run`, or
`blocked`. Those fields are stripped if you emit them. Inventing work no
subtask reported is a contract violation.
