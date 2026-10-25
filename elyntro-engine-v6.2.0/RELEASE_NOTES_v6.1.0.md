# Elyntro Engine v6.1.0 — Release Notes

## Focus

Consolidate execution controls on the canonical `TaskExecutor` path: explicit action authorization, plan-manifest integrity, and durable journaling for externally visible operations.

## Added

- Optional per-action `PolicyEngine` enforcement immediately before registered handlers are invoked. Decisions are persisted to the task event log; denied and over-budget actions fail without calling the handler.
- Explicit action metadata on `TaskStep`: risk, estimated cost/tokens, network access, external writes, and approval state.
- Explicit step-level approval gate that pauses a task in `NEEDS_APPROVAL`; changing the persisted plan on resume is rejected by a SHA-256 execution-plan manifest.
- Optional `OperationJournal` integration for steps marked `writes_external_state`. Intent is persisted before dispatch; interrupted or failed dispatches are marked `uncertain` and are not automatically retried. Successful journal records preserve the step result, evidence, and artifacts for safe recovery.
- A strict option, `require_journal_for_external_writes=True`, that prevents marked external-write steps from invoking handlers when no journal is configured.
- Seven new governed-executor tests covering policy denial, approval pause/resume, plan tampering, budget exhaustion, journaling, missing-journal refusal, and uncertain outcomes.
- A fourth release-gate check proving that a denied handler is not invoked and the policy decision is persisted.

## Validation commands

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
python -m elyntro.release_gate
python -m compileall -q elyntro tests examples model_lab
```

## Guarantees and limitations

- Governance is opt-in for compatibility: applications must pass a `PolicyEngine` to `TaskExecutor` to enable per-action policy enforcement.
- External-write journaling applies only to steps explicitly marked `writes_external_state=True`. For strict enforcement, configure both an `OperationJournal` and `require_journal_for_external_writes=True`.
- The journal cannot make arbitrary third-party APIs transactional or guarantee exactly-once execution. Uncertain outcomes require provider-side reconciliation.
- The plan fingerprint protects execution/resume consistency after the manifest is recorded; it is not a substitute for an authenticated approval service or OS-level sandbox.
- The executor runs trusted registered Python handlers. Policy checks do not sandbox a handler or stop it from doing undeclared side effects.
- Local tests do not replace provider contract tests, concurrency/load tests, adversarial security review, or deployment monitoring.
