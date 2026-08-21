# judge-result / judge-pair

Judgments are proposals. ICLE persists them only after validation.

## judge-result

Score one result against the task. Five dimensions, integers 1–5:

- `requirement_fit` — did it do what was asked
- `correctness` — is the work right
- `efficiency` — wasted steps / retries
- `autonomy` — needed the user to finish it
- `maintainability` — would another agent or human keep this

`overall_preference` is also 1–5. `would_use_again` is yes | maybe | no.
`comment` is one sentence.

Do not invent a sixth dimension. Do not output 0 or 6. If the result text is
empty, refuse with a validation-failing object rather than inventing a 3.

## judge-pair

Same task, two results. Verdict only:

- `prefer_a` / `prefer_b` / `tie` / `inconclusive`
- `reason` one sentence
- `confidence` 0..1
- `reason_tags` a subset of: correctness, completeness, style, speed, cost, initiative, understanding, maintainability

Do not pick an agent. Do not emit a numeric score. Inconclusive is allowed
when the two results are not comparable.
