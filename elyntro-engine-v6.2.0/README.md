# Elyntro Engine — v6.2.0

A Python orchestration engine with task lifecycle management, planning, model routing, evidence verification, recovery primitives, and bounded execution controls. It is not a foundation model; configured model providers and application-specific handlers supply the underlying capabilities.

## v6.2 — independent verification is the default

The canonical `CognitiveLoop` now fails closed if a task has no independent criterion validator. If evidence exists but no validator is registered, the result is `INCONCLUSIVE` and the task enters `NEEDS_APPROVAL`; a handler's own success claim is not proof. Register domain-specific checks with `EvidenceVerifier` and pass it to `CognitiveLoop`. For example:

```python
from elyntro import CognitiveLoop, EvidenceVerifier

verifier = EvidenceVerifier()
verifier.register(
    "report produced",
    lambda criterion, evidence, context: any(
        item.get("name") == "report.md" and isinstance(item.get("content"), str)
        for item in context.get("artifacts", [])
    ),
)
# loop = CognitiveLoop(engine, planner, tools, executor, verifier=verifier)
```

An explicit compatibility option, `require_independent_verification=False`, restores legacy structural-only behavior; this must not be represented as independently verified success.

Run the deterministic task benchmark with `python examples/real_task_benchmark.py`. It exercises HTML artifact creation, data analysis, compound-interest calculation, and rejection of unsupported success claims. It uses local handlers and does not measure model intelligence or external providers. See `RELEASE_VALIDATION_v6.2.0.md`.

## v5.5 — bounded verification and repair cycle

This release adds `VerificationCycle`, an opt-in loop that runs independent evidence validators, allows a bounded repair callback after a definitive verification failure, and re-runs verification on the repaired evidence/artifacts. `INCONCLUSIVE` results fail closed and are returned for human review; the repair callback is not called in that case. Repair attempts are capped at 0–5. The callback is application code and must use the engine's governance controls when calling tools or external services.

The cycle remains opt-in at the application route level, but the core `Engine` now provides a governed lifecycle integration method. Other specialized execution services must opt into that method or implement equivalent controls. A successful report means registered validators accepted the declared criteria; it is not a universal guarantee of truth.

## v5.6 — governed verification and repair integration

`Engine.verify_with_cycle(...)` integrates the bounded verification cycle into the persisted task lifecycle. A task is marked `SUCCEEDED` only when the independent verifier returns `VERIFIED`; `INCONCLUSIVE` results require human review; failed verification after bounded repair remains failed. If a repair callback is supplied, a `PolicyEngine` is mandatory and each repair attempt must be authorized before the callback is invoked. Governance decisions and the cycle report are written to the task event log. Final evidence/artifacts returned by the cycle are persisted.

Minimal flow:

```python
from elyntro import Engine, VerificationCycle, EvidenceVerifier
from elyntro.governance import GovernancePolicy, PolicyEngine

verifier = EvidenceVerifier()
verifier.register("output valid", lambda criterion, evidence, context: any(e.get("value") == "valid" for e in evidence))
cycle = VerificationCycle(verifier, max_repairs=2)
policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"task.repair"})))
# After moving a task to VERIFYING:
# task, result = engine.verify_with_cycle(task_id, cycle, evidence=evidence,
#     policy_engine=policy, repair=repair_callback)
```

See `examples/governed_verification_demo.py` for a runnable end-to-end example.

## Requirements

- Python 3.11+ recommended; no third-party packages required for the engine/tests.

## Run tests

From this folder:

```bash
python -m unittest discover -s tests -v
```

## Run the lifecycle demo

```bash
python -m elyntro.cli
```

The demo creates `elyntro.db` in the current directory.

## Verification cycle example

```python
from elyntro import EvidenceVerifier, VerificationCycle

verifier = EvidenceVerifier()
verifier.register(
    "output is valid",
    lambda criterion, evidence, context: evidence[0].get("value") == "valid",
)

def repair(report, evidence, context):
    # Replace this with a governed, application-specific correction step.
    return ([{"criterion": "output is valid", "detail": "checked output", "value": "valid"}], [])

result = VerificationCycle(verifier, max_repairs=2).run(
    ["output is valid"],
    [{"criterion": "output is valid", "detail": "initial output", "value": "invalid"}],
    repair=repair,
)
print(result.to_dict())
```

## Guarantees and limits

- Verification is only as strong as the registered validators.
- Repair is bounded; a failed result remains failed after the retry budget is exhausted.
- Inconclusive results are not treated as success.
- Repair callbacks are not sandboxed and do not gain permission to use tools automatically.
- This release does not claim automatic integration into all execution routes, distributed transactions, or zero hallucinations.

## v5.7 — read-only runtime health audit

This release adds `RuntimeHealthAuditor`, which inspects recent routed model runs and durable jobs and reports stale executions, interrupted runs, jobs awaiting approval, unreadable job records, and non-idempotent steps with uncertain outcomes. The report is structured and severity-ranked. The auditor is deliberately read-only: it does not resume, retry, mark tasks complete, or mutate ledger state. Operators must reconcile uncertain external side effects before replaying work.

```python
from elyntro import ExecutionLedger, PersistentJobStore, RuntimeHealthAuditor

ledger = ExecutionLedger("runtime.sqlite")
jobs = PersistentJobStore("jobs.sqlite")
report = RuntimeHealthAuditor(ledger, jobs).audit(stale_after_seconds=300, limit=200)
print(report.to_dict())
```

See `examples/runtime_health_demo.py`. This is an operational audit for a local SQLite deployment, not a distributed health service or proof that a provider-side action did not occur.


## v5.8 — deterministic SQLite connection lifecycle

This release adds `ClosingConnection`, a small SQLite connection subclass whose context manager preserves normal commit/rollback behavior and closes the connection on exit. Short-lived database connections across the engine now use it, including the task store, memory, model evaluation, routing feedback, policy experiments, persistent jobs, runtime ledger, recovery, reconciliation, and monitoring modules. This fixes the core pattern behind Python `ResourceWarning` messages: SQLite connection context managers commit or roll back, but do not close connections by themselves. Explicit `close()` calls remain supported.

Validation performed for this release:

- `python -m unittest discover -s tests`: 302 tests passed.
- `python -W error::ResourceWarning -m unittest discover -s tests`: 302 tests passed without unclosed-connection warnings.
- Added regression tests for commit-and-close and rollback-and-close semantics.
- The fix covers Elyntro's application connection paths; third-party adapters and application code that open their own SQLite connections remain responsible for closing them.


## v5.9 — durable operation intent journal

This release adds `OperationJournal`, a SQLite-backed write-ahead journal for side effects performed by application handlers. It persists an idempotency key and canonical request before an external action, records state transitions and outcomes, and exposes uncertain operations for explicit reconciliation. Reusing a key with a different operation/request is rejected. A `started` operation is never automatically replayed: after a crash or timeout, the caller must mark it `uncertain` and reconcile the external system before deciding whether it was applied.

```python
from elyntro import OperationJournal

journal = OperationJournal("operations.sqlite")
record = journal.prepare("ticket:create:42", "create_ticket", {"title": "Investigate outage"})
journal.transition(record["operation_id"], "started")
# Call the external service only after the intent is durable.
# Then persist its response: journal.transition(id, "succeeded", result={...})
```

Run the demo from this directory with `python -m examples.operation_journal_demo`. The journal is a primitive, not an automatic exactly-once guarantee: it cannot make an arbitrary third-party API transactional. Integrations should pass the same idempotency key to the provider when supported and explicitly reconcile ambiguous outcomes.

## v6.0.0 — first acceptance-gated orchestration release

This release adds a repeatable pre-release gate that runs three independent checks:

1. Public API imports for the engine, model gateway, operation journal, governance, and verification cycle.
2. The positive/negative durable-workflow acceptance matrix, including incorrect-result rejection, missing-verifier fail-closed behavior, and non-idempotent timeout handling.
3. An operation-journal reopen test proving that an `uncertain` state and its canonical request remain discoverable after creating a fresh journal instance.

Run the release gate from this folder:

```bash
python -m elyntro.release_gate
```

The command emits JSON and exits with code `0` only when all checks pass; otherwise it exits with code `1`. The full automated suite remains:

```bash
python -W error::ResourceWarning -m unittest discover -s tests -v
```

### v6.0 acceptance scope and limits

- Elyntro is an orchestration engine, not a newly trained foundation model. It depends on configured model providers and application-registered handlers.
- Acceptance scenarios are deterministic local tests; they do not prove model intelligence, production availability, adversarial security, or performance at scale.
- The operation journal helps manage ambiguous external side effects but cannot guarantee exactly-once execution for arbitrary providers. Use provider-supported idempotency keys and reconcile uncertain outcomes.
- Governance, verification, and journaling must be explicitly integrated by each application execution path; the release gate does not certify every third-party adapter or custom handler.
- This release is a first stable milestone for the local orchestration core, not a claim that every planned Elyntro capability is complete.


## v6.1.0 — governed task execution and external-operation recovery

`TaskExecutor` can now optionally enforce `PolicyEngine` authorization before each handler invocation, persist policy decisions, pause for explicit step approval, and reject a modified plan when resuming from its recorded manifest. Steps that declare `writes_external_state=True` can be wrapped with `OperationJournal` intent/state transitions; uncertain outcomes are not automatically retried.

Minimal governed setup:

```python
from elyntro import Engine, SQLiteStore, TaskExecutor, TaskStep
from elyntro.governance import GovernancePolicy, PolicyEngine, RiskLevel
from elyntro.operation_journal import OperationJournal

policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"lookup", "publish"})))
journal = OperationJournal("operations.sqlite")
executor = TaskExecutor(
    engine, handlers, policy_engine=policy, operation_journal=journal,
    require_journal_for_external_writes=True,
)
steps = [
    TaskStep("lookup-1", "Look up records", "lookup", risk=RiskLevel.LOW, requires_network=True),
    TaskStep("publish-1", "Publish result", "publish", risk=RiskLevel.HIGH,
             writes_external_state=True, user_approved=True),
]
```

The example is illustrative: the host application must create `engine`, register trusted handlers, and obtain approval from its own authenticated approval workflow before setting `user_approved=True`. Governance is opt-in for backward compatibility. The journal is a recovery primitive, not a universal exactly-once guarantee.
