# Elyntro v6.1.0 — Release Validation

**Release status:** passed local validation checks on the packaged source tree.

## Results

- Full automated test suite: **316 passed**.
- Full suite with `ResourceWarning` treated as an error: **316 passed**.
- Release gate: **4/4 checks passed**.
- Release-gate acceptance matrix: **5/5 scenarios passed**.
- `compileall` across `elyntro`, `tests`, `examples`, and `model_lab`: **passed**.
- ZIP integrity (`unzip -t`): passed with no compression errors.

## New coverage

Seven tests exercise the governed task-execution path: policy denial before handler invocation, explicit approval pause/resume, rejection of a changed plan manifest, budget exhaustion, journaled external writes, refusal to perform external writes without a required journal, and no automatic retry of uncertain external operations.

## Scope limits

These are deterministic local tests. They do not constitute provider contract tests, concurrent load testing, an adversarial security audit, or proof of exactly-once execution against arbitrary external services. Policy enforcement is opt-in for backward compatibility. External side effects are journaled only for steps explicitly marked `writes_external_state=True`; strict mode additionally requires `require_journal_for_external_writes=True`.
