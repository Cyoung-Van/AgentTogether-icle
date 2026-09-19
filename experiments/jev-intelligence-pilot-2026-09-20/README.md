# ICLE × Jev intelligence pilot

[Read the results (中文)](REPORT.md).

This isolated experiment contains 20 synthetic scenarios and the recorded results
of 18 real calls to `jev-1.13.0`. It checks task profiling, episode reranking,
and a semantic fallback for unknown or conflicting failure classifications.
It does not enable Jev in the application, execute scenario instructions, or
write to the application's experience store.

The small, deliberately constructed sample is a coverage check, not a general
accuracy or production-readiness benchmark. Labels and requests were frozen
before inference. Raw responses include the observed numerical warning.

## Replay without API calls

Use Python 3.11 from the repository root:

```sh
python experiments/jev-intelligence-pilot-2026-09-20/pilot.py analyze
```

This reads the included responses and runs the existing ICLE profile validator,
episode-similarity function, and rule-based failure classifier. No API key is
needed. See `summary.json` for machine-readable results and `details.json` for
individual outcomes, including failures and review decisions.

## Live-call behavior

`pilot.py run` skips every case with an existing response. All requests in this
published run have responses, so replaying it makes no additional calls.
Missing responses would require `TYPESAFE_API_KEY` in the environment or the
repository-root `.env` file. Credentials are never included in this directory.
The stored budget permits at most 18 calls, with no automatic retries. Preserve
the frozen inputs, responses and ledger when designing a separate experiment.

## Evidence and provenance

- `frozen.json`: scenarios, expected labels, requests and thresholds.
- `manifest.json`: frozen-input hash, ICLE revision and source-file hashes.
- `responses/`: original API responses, request hashes and timing.
- `usage.json`: actual call and token counts.
- `artifact_manifest.json`: hashes of the published package files.

For publication, the script's local machine paths were replaced by repository-
relative paths and standard environment-based credential loading. Prompts,
labels, recorded responses, thresholds and reported results were unchanged.
