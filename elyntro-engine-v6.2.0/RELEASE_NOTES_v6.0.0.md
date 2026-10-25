# Elyntro Engine v6.0.0 — Release Notes

## Focus

The first acceptance-gated release consolidates the local orchestration core and adds one repeatable command to verify critical release invariants before packaging or deployment.

## Added

- `elyntro.release_gate`: machine-readable release report and non-zero exit on failure.
- Public API import check.
- Positive and negative workflow acceptance matrix in the release gate.
- Durable operation-journal test across reopening a fresh journal instance.
- Release documentation describing supported guarantees and explicit non-guarantees.

## Validation commands

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m elyntro.release_gate
python -m compileall -q elyntro tests examples model_lab
```

## Known limitations

- Not a foundation model; relies on configured external providers for model inference.
- Does not guarantee zero hallucinations, universal prompt-injection immunity, or exactly-once execution against arbitrary external services.
- Custom handlers and specialized routes must integrate governance, verification, cancellation, and operation journaling explicitly.
- Local SQLite acceptance tests are not substitutes for load tests, provider contract tests, security review, or deployment monitoring.
