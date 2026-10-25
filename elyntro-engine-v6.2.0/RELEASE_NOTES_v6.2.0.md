# Elyntro Engine v6.2.0 — Release Notes

## Focus

Make the canonical cognitive execution path fail closed when task results have not been independently verified, and add a repeatable end-to-end benchmark using concrete artifacts.

## Changes

- `CognitiveLoop` now requires independent verification by default. If no `EvidenceVerifier` is supplied, it creates an empty verifier; criteria with evidence but no independent validator become `INCONCLUSIVE` and the task moves to `NEEDS_APPROVAL`, never `SUCCEEDED` merely because a handler claimed completion.
- Added `require_independent_verification=False` as an explicit compatibility opt-out for legacy structural-only integrations. This mode does not establish truth and should not be used for production claims of verified success.
- The loop always configures the executor with the selected verifier, so a verifier supplied to `CognitiveLoop` cannot silently be bypassed by an executor that was constructed without it.
- `CognitiveRunResult.verification` now prefers the complete `independent_verification` report over the shorter executor summary, exposing per-criterion checks to the caller.
- Added four deterministic end-to-end benchmark scenarios: responsive HTML artifact creation, sales-data analysis, compound-interest calculation, and rejection of an unsupported success claim.
- Added regression tests for the strict default and independently validated success.

## Local validation

- Full unit test suite: 319 tests passed.
- Full unit test suite with `ResourceWarning` treated as an error: 319 tests passed.
- Release gate: 4/4 checks passed.
- Release-gate workflow matrix: 5/5 scenarios passed.
- `compileall`: passed.
- Deterministic task benchmark: 4/4 expected outcomes; 3 independently verified successes and 1 correctly routed to human review.

## Limitations

- The benchmark uses local deterministic handlers; it measures orchestration, artifact generation, and verification integration, not foundation-model intelligence or quality.
- No external model/provider calls, web browsing, deployed application, image/audio/video generation, interactive 3D, voice Live, or computer-use task was tested in this release.
- Independent verification is only as strong as the domain-specific validators supplied by the host application.
- This is not a claim of AGI, universal correctness, production security, or world-leading performance.
