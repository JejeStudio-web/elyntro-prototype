"""Sequential, auditable execution of explicitly registered task handlers.

The executor never evaluates arbitrary code from a task plan. A plan may invoke only
handlers registered by the application, which keeps the execution surface explicit.
"""
from __future__ import annotations

import json
import hashlib
import time
import threading
from queue import Queue, Empty
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .contracts import Task, TaskStatus
from .engine import Engine
from .execution_control import CancellationToken, ExecutionCancelledError, StepTimeoutError
from .governance import ActionRequest, PolicyEngine, RiskLevel
from .operation_journal import OperationJournal, InvalidOperationTransition


@dataclass(frozen=True)
class StepResult:
    """Serializable output and evidence returned by a registered handler."""
    output: Any = None
    evidence: list[dict[str, str]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> None:
        _ensure_json_serializable(self.output, "step output")
        if not isinstance(self.evidence, list):
            raise ValueError("evidence must be a list")
        for item in self.evidence:
            if not isinstance(item, dict) or not str(item.get("criterion", "")).strip() or not str(item.get("detail", "")).strip():
                raise ValueError("each evidence item must include non-empty criterion and detail fields")
        if not isinstance(self.artifacts, list):
            raise ValueError("artifacts must be a list")
        for artifact in self.artifacts:
            if not isinstance(artifact, dict) or not str(artifact.get("name", "")).strip():
                raise ValueError("each artifact must be a dictionary with a non-empty name")
            _ensure_json_serializable(artifact.get("content"), "artifact content")
            media_type = artifact.get("media_type", "application/json")
            if not isinstance(media_type, str) or not media_type.strip():
                raise ValueError("artifact media_type must be a non-empty string")


@dataclass(frozen=True)
class TaskStep:
    """One named operation; handler_key maps to an application-registered function."""
    step_id: str
    name: str
    handler_key: str
    inputs: dict[str, Any] = field(default_factory=dict)
    max_retries: int = 0
    retry_delay_seconds: float = 0.0
    timeout_seconds: float = 60.0
    risk: RiskLevel = RiskLevel.MEDIUM
    estimated_cost_usd: float = 0.0
    estimated_tokens: int = 0
    requires_network: bool = False
    writes_external_state: bool = False
    user_approved: bool = False
    requires_approval: bool = False

    def validate(self) -> None:
        for field_name, value in (("step_id", self.step_id), ("name", self.name), ("handler_key", self.handler_key)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.inputs, dict):
            raise ValueError("step inputs must be a dictionary")
        _ensure_json_serializable(self.inputs, "step inputs")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be an integer between 0 and 5")
        if isinstance(self.retry_delay_seconds, bool) or not isinstance(self.retry_delay_seconds, (int, float)) or not 0 <= self.retry_delay_seconds <= 30:
            raise ValueError("retry_delay_seconds must be between 0 and 30")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)) or not 0.01 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be between 0.01 and 3600")
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("risk must be a RiskLevel")
        if isinstance(self.estimated_cost_usd, bool) or not isinstance(self.estimated_cost_usd, (int, float)) or self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be a non-negative number")
        if isinstance(self.estimated_tokens, bool) or not isinstance(self.estimated_tokens, int) or self.estimated_tokens < 0:
            raise ValueError("estimated_tokens must be a non-negative integer")
        if any(not isinstance(value, bool) for value in (self.requires_network, self.writes_external_state, self.user_approved, self.requires_approval)):
            raise ValueError("action flags must be booleans")


@dataclass(frozen=True)
class StepContext:
    task_id: str
    goal: str
    constraints: tuple[str, ...]
    step_id: str
    attempt: int
    idempotency_key: str
    cancellation_token: CancellationToken = field(compare=False, repr=False)
    deadline_monotonic: float | None = None

    def checkpoint(self) -> None:
        """Call at safe points to observe cancellation and deadline expiration."""
        self.cancellation_token.checkpoint(self.deadline_monotonic)


Handler = Callable[[dict[str, Any], StepContext], StepResult | Any]


def _ensure_json_serializable(value: Any, label: str) -> None:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-serializable: {exc}") from exc


class TaskExecutor:
    """Run a sequential plan with bounded retries and persistent event records.

    This is deliberately local and synchronous. Handlers are trusted application code;
    never register an arbitrary-code or shell handler for untrusted task input.
    """

    def __init__(self, engine: Engine, handlers: Mapping[str, Handler] | None = None, verifier=None,
                 policy_engine: PolicyEngine | None = None, operation_journal: OperationJournal | None = None,
                 require_journal_for_external_writes: bool = False) -> None:
        self.engine = engine
        self.handlers: dict[str, Handler] = dict(handlers or {})
        self.verifier = verifier
        self.policy_engine = policy_engine
        self.operation_journal = operation_journal
        self.require_journal_for_external_writes = require_journal_for_external_writes
        if not isinstance(require_journal_for_external_writes, bool):
            raise ValueError("require_journal_for_external_writes must be boolean")

    def register_handler(self, key: str, handler: Handler) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("handler key must be a non-empty string")
        if not callable(handler):
            raise ValueError("handler must be callable")
        if key in self.handlers:
            raise ValueError(f"handler already registered: {key}")
        self.handlers[key] = handler

    @staticmethod
    def _plan_fingerprint(steps: list[TaskStep]) -> str:
        manifest = [{
            "step_id": s.step_id, "name": s.name, "handler_key": s.handler_key,
            "inputs": s.inputs, "max_retries": s.max_retries,
            "retry_delay_seconds": s.retry_delay_seconds, "timeout_seconds": s.timeout_seconds,
            "risk": s.risk.value, "estimated_cost_usd": float(s.estimated_cost_usd),
            "estimated_tokens": s.estimated_tokens, "requires_network": s.requires_network,
            "writes_external_state": s.writes_external_state, "requires_approval": s.requires_approval,
        } for s in steps]
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _authorize_step(self, task_id: str, task: Task, step: TaskStep, attempt: int) -> dict[str, Any] | None:
        if self.policy_engine is None:
            return None
        action = ActionRequest(
            action_id=f"{task_id}:{step.step_id}:attempt:{attempt}", tool_key=step.handler_key,
            description=step.name, risk=step.risk, estimated_cost_usd=float(step.estimated_cost_usd),
            estimated_tokens=step.estimated_tokens, requires_network=step.requires_network,
            writes_external_state=step.writes_external_state, user_approved=step.user_approved,
            metadata={"task_id": task_id, "step_id": step.step_id, "attempt": attempt},
        )
        decision = self.policy_engine.authorize(action)
        self.engine.store.add_event(task_id, "action_policy_decision", decision.to_dict())
        return decision.to_dict()

    def _journal_prepare(self, task_id: str, step: TaskStep, context: StepContext) -> tuple[bool, Any]:
        """Return (reuse_result, result); uncertain effects are never replayed automatically."""
        if not step.writes_external_state:
            return False, None
        if self.operation_journal is None:
            if self.require_journal_for_external_writes:
                raise PermissionError("external-write step requires an OperationJournal")
            return False, None
        key = context.idempotency_key
        expected_request = {"task_id": task_id, "step_id": step.step_id, "inputs": step.inputs}
        try:
            record = self.operation_journal.get_by_key(key)
            if record["operation_name"] != step.handler_key or record["request"] != expected_request:
                raise ValueError("idempotency key is already bound to another operation or request")
            if record["state"] in {"succeeded", "resolved_applied"}:
                saved_result = record["result"]
                if isinstance(saved_result, dict) and saved_result.get("__elyntro_journal_result__") is True:
                    saved_result = StepResult(output=saved_result.get("output"),
                        evidence=saved_result.get("evidence", []), artifacts=saved_result.get("artifacts", []))
                return True, saved_result
            if record["state"] in {"started", "uncertain"}:
                if record["state"] == "started":
                    self.operation_journal.transition(record["operation_id"], "uncertain", error="recovered without a committed task result", actor="task_executor")
                raise RuntimeError("external operation has an uncertain outcome; reconcile it before retrying")
            if record["state"] != "prepared":
                raise RuntimeError(f"external operation is not replayable (state={record['state']})")
        except KeyError:
            record = self.operation_journal.prepare(key, step.handler_key, {"task_id": task_id, "step_id": step.step_id, "inputs": step.inputs})
        self.operation_journal.transition(record["operation_id"], "started", actor="task_executor")
        self.engine.store.add_event(task_id, "external_operation_started", {
            "step_id": step.step_id, "operation_id": record["operation_id"], "idempotency_key": key,
        })
        return False, None

    def _journal_complete(self, task_id: str, step: TaskStep, context: StepContext, result: Any) -> None:
        if not step.writes_external_state or self.operation_journal is None:
            return
        record = self.operation_journal.get_by_key(context.idempotency_key)
        if record["state"] == "started":
            if isinstance(result, StepResult):
                serializable = {"__elyntro_journal_result__": True, "output": result.output,
                                "evidence": result.evidence, "artifacts": result.artifacts}
            else:
                serializable = result
            self.operation_journal.transition(record["operation_id"], "succeeded", result=serializable, actor="task_executor")
            self.engine.store.add_event(task_id, "external_operation_succeeded", {
                "step_id": step.step_id, "operation_id": record["operation_id"],
            })

    def _journal_uncertain(self, task_id: str, step: TaskStep, context: StepContext, exc: BaseException) -> None:
        if not step.writes_external_state or self.operation_journal is None:
            return
        try:
            record = self.operation_journal.get_by_key(context.idempotency_key)
            if record["state"] == "started":
                self.operation_journal.transition(record["operation_id"], "uncertain",
                    error=f"{type(exc).__name__}: {str(exc)[:1000]}", actor="task_executor")
                self.engine.store.add_event(task_id, "external_operation_uncertain", {
                    "step_id": step.step_id, "operation_id": record["operation_id"],
                    "error_type": type(exc).__name__,
                })
        except (KeyError, InvalidOperationTransition):
            # Preserve the original execution error; reconciliation can inspect the journal separately.
            pass

    def execute(self, task_id: str, steps: list[TaskStep], cancellation_token: CancellationToken | None = None) -> Task:
        """Execute or resume a plan. Completed steps are recovered from the event log.

        A handler that has started but has no committed success event may run again
        after a crash. Such handlers must use context.idempotency_key for external
        side effects. Timeouts bound how long the executor waits, not the lifetime of
        a non-cooperative Python thread; handlers must call context.checkpoint().
        """
        if not isinstance(steps, list) or not steps:
            raise ValueError("steps must contain at least one TaskStep")
        for step in steps:
            if not isinstance(step, TaskStep):
                raise ValueError("every plan item must be a TaskStep")
            step.validate()
        ids = [step.step_id for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique")
        missing_handlers = sorted({step.handler_key for step in steps if step.handler_key not in self.handlers})
        if missing_handlers:
            raise ValueError("unregistered handlers: " + ", ".join(missing_handlers))

        fingerprint = self._plan_fingerprint(steps)
        task = self.engine.store.get_task(task_id)
        manifest_events = [e for e in self.engine.store.list_events(task_id) if e["event_type"] == "execution_plan_manifest"]
        if manifest_events and manifest_events[-1]["payload"].get("fingerprint") != fingerprint:
            raise ValueError("execution plan differs from the persisted manifest; approval/resume is blocked")
        if not manifest_events:
            self.engine.store.add_event(task_id, "execution_plan_manifest", {
                "fingerprint": fingerprint, "step_count": len(steps),
                "step_ids": [step.step_id for step in steps],
            })
        is_resume = task.status in {TaskStatus.RUNNING, TaskStatus.VERIFYING}
        is_verifying_resume = task.status == TaskStatus.VERIFYING
        is_approved_ready = task.status == TaskStatus.READY
        if task.status not in {TaskStatus.CREATED, TaskStatus.RUNNING, TaskStatus.READY, TaskStatus.VERIFYING}:
            raise ValueError(f"executor requires a created, approved-ready, or interrupted running task; current status is {task.status.value}")
        token = cancellation_token or CancellationToken()
        try:
            if task.status == TaskStatus.CREATED:
                self.engine.transition(task_id, TaskStatus.VALIDATING, "executor accepted the task")
                self.engine.transition(task_id, TaskStatus.PLANNING, "using supplied deterministic plan")
                self.engine.set_plan(task_id, [step.name for step in steps])
                self.engine.transition(task_id, TaskStatus.READY, "plan validated")
                self.engine.transition(task_id, TaskStatus.RUNNING, "execution started")
            elif is_approved_ready:
                if task.plan != [step.name for step in steps]:
                    raise ValueError("approved plan does not match the persisted task plan")
                self.engine.transition(task_id, TaskStatus.RUNNING, "approved plan execution started")
                self.engine.store.add_event(task_id, "approved_execution_started", {"step_count": len(steps)})
            else:
                if task.plan != [step.name for step in steps]:
                    raise ValueError("resume plan does not match the persisted task plan")
                self.engine.store.add_event(task_id, "verification_resumed" if is_verifying_resume else "execution_resumed",
                                            {"step_count": len(steps), "phase": "verification" if is_verifying_resume else "execution"})

            events = self.engine.store.list_events(task_id)
            committed: dict[str, dict[str, Any]] = {}
            for event in events:
                if event["event_type"] == "step_result_committed":
                    committed[event["payload"]["step_id"]] = event["payload"]
            # Rebuild evidence from durable results before executing remaining steps.
            collected_evidence: list[dict[str, Any]] = []
            for step in steps:
                saved = committed.get(step.step_id)
                if saved is not None:
                    collected_evidence.extend(saved.get("evidence", []))

            for step in steps:
                if step.step_id in committed:
                    self.engine.store.add_event(task_id, "step_recovered", {"step_id": step.step_id, "source": "committed_result"})
                    continue
                current = self.engine.store.get_task(task_id)
                if current.status == TaskStatus.CANCELLED or token.is_cancelled:
                    if current.status != TaskStatus.CANCELLED:
                        self.engine.cancel(task_id, "cancellation token requested")
                    self.engine.store.add_event(task_id, "execution_stopped", {"reason": "task cancelled", "step_id": step.step_id})
                    return self.engine.store.get_task(task_id)
                if current.status != TaskStatus.RUNNING:
                    raise RuntimeError(f"task left running state unexpectedly: {current.status.value}")

                self.engine.store.add_event(task_id, "step_started", {
                    "step_id": step.step_id, "name": step.name, "handler_key": step.handler_key,
                    "max_retries": step.max_retries, "timeout_seconds": step.timeout_seconds,
                    "idempotency_key": f"{task_id}:{step.step_id}",
                })
                success = False
                for attempt in range(1, step.max_retries + 2):
                    deadline = time.monotonic() + float(step.timeout_seconds)
                    context = StepContext(
                        task_id=task_id, goal=current.goal, constraints=tuple(current.constraints),
                        step_id=step.step_id, attempt=attempt,
                        idempotency_key=f"{task_id}:{step.step_id}",
                        cancellation_token=token, deadline_monotonic=deadline,
                    )
                    try:
                        context.checkpoint()
                        approval_required = step.requires_approval and not step.user_approved
                        if approval_required:
                            approval_payload = {"outcome": "require_approval", "reason": "tool definition requires explicit approval",
                                "action_id": f"{task_id}:{step.step_id}:attempt:{attempt}", "tool_key": step.handler_key}
                            self.engine.store.add_event(task_id, "action_approval_required", {
                                "step_id": step.step_id, "action_id": approval_payload["action_id"],
                                "reason": approval_payload["reason"], "plan_fingerprint": fingerprint,
                            })
                            self.engine.transition(task_id, TaskStatus.NEEDS_APPROVAL,
                                f"action {step.step_id} requires explicit approval")
                            return self.engine.store.get_task(task_id)
                        decision = self._authorize_step(task_id, current, step, attempt)
                        if decision is not None and decision["outcome"] == "require_approval":
                            self.engine.store.add_event(task_id, "action_approval_required", {
                                "step_id": step.step_id, "action_id": decision["action_id"],
                                "reason": decision["reason"], "plan_fingerprint": fingerprint,
                            })
                            self.engine.transition(task_id, TaskStatus.NEEDS_APPROVAL,
                                f"action {step.step_id} requires explicit approval")
                            return self.engine.store.get_task(task_id)
                        if decision is not None and decision["outcome"] != "allow":
                            self.engine.store.add_event(task_id, "action_blocked", {
                                "step_id": step.step_id, "outcome": decision["outcome"], "reason": decision["reason"],
                            })
                            return self.engine.fail(task_id, f"Action '{step.name}' blocked by policy: {decision['outcome']} ({decision['reason']})")
                        reuse_journal_result, journal_result = self._journal_prepare(task_id, step, context)
                        if reuse_journal_result:
                            raw_result = journal_result
                            ok = True
                        else:
                            result_queue: Queue[tuple[bool, Any]] = Queue(maxsize=1)
                            def invoke() -> None:
                                try:
                                    result_queue.put((True, self.handlers[step.handler_key](dict(step.inputs), context)))
                                except BaseException as handler_exc:
                                    result_queue.put((False, handler_exc))
                            worker = threading.Thread(target=invoke, name=f"elyntro-{step.step_id}", daemon=True)
                            worker.start()
                            while True:
                                context.checkpoint()
                                remaining = deadline - time.monotonic()
                                if remaining <= 0:
                                    raise StepTimeoutError(f"step '{step.step_id}' exceeded {step.timeout_seconds:g}s timeout")
                                try:
                                    ok, raw_result = result_queue.get(timeout=min(0.1, remaining))
                                    break
                                except Empty:
                                    continue
                            if not ok:
                                raise raw_result
                        context.checkpoint()
                        result = raw_result if isinstance(raw_result, StepResult) else StepResult(output=raw_result)
                        self._journal_complete(task_id, step, context, result)
                        result.validate()
                        output = {"step_id": step.step_id, "name": step.name, "output": result.output, "attempt": attempt}
                        self.engine.add_artifact(task_id, f"step-{step.step_id}.json", output, "application/json")
                        for artifact in result.artifacts:
                            self.engine.add_artifact(task_id, artifact["name"], artifact.get("content"), artifact.get("media_type", "application/json"))
                        collected_evidence.extend(result.evidence)
                        committed_payload = {
                            "step_id": step.step_id, "attempt": attempt, "output": result.output,
                            "evidence": result.evidence, "artifacts": result.artifacts,
                        }
                        self.engine.store.add_event(task_id, "step_result_committed", committed_payload)
                        self.engine.store.add_event(task_id, "step_succeeded", {
                            "step_id": step.step_id, "attempt": attempt,
                            "evidence_count": len(result.evidence), "artifact_count": len(result.artifacts),
                        })
                        success = True
                        break
                    except Exception as exc:
                        self._journal_uncertain(task_id, step, context, exc)
                        if token.is_cancelled or isinstance(exc, ExecutionCancelledError):
                            latest = self.engine.store.get_task(task_id)
                            if latest.status not in {TaskStatus.CANCELLED, TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                                self.engine.cancel(task_id, "cancellation requested during step execution")
                            self.engine.store.add_event(task_id, "execution_stopped", {"reason": "cancellation requested", "step_id": step.step_id})
                            return self.engine.store.get_task(task_id)
                        self.engine.store.add_event(task_id, "step_attempt_failed", {
                            "step_id": step.step_id, "attempt": attempt,
                            "error_type": type(exc).__name__, "message": str(exc),
                            "will_retry": (attempt <= step.max_retries and not (step.writes_external_state and self.operation_journal is not None)),
                        })
                        if step.writes_external_state and self.operation_journal is not None:
                            # Never blindly retry an externally visible action after its outcome may be ambiguous.
                            break
                        if attempt <= step.max_retries and step.retry_delay_seconds:
                            # Wait can be interrupted by cancellation rather than sleeping blindly.
                            if token.wait(float(step.retry_delay_seconds)):
                                self.engine.cancel(task_id, "cancellation requested during retry delay")
                                return self.engine.store.get_task(task_id)
                if not success:
                    failed = self.engine.fail(task_id, f"Step '{step.name}' failed after {step.max_retries + 1} attempt(s)", retryable=step.max_retries > 0)
                    return failed

            if token.is_cancelled:
                self.engine.cancel(task_id, "cancellation requested before verification")
                return self.engine.store.get_task(task_id)
            if not is_verifying_resume:
                self.engine.transition(task_id, TaskStatus.VERIFYING, "all plan steps executed")
            unique_evidence: list[dict[str, Any]] = []
            seen: set[str] = set()
            for item in collected_evidence:
                criterion = item["criterion"].strip()
                if criterion not in seen:
                    unique_evidence.append(item)
                    seen.add(criterion)
            try:
                if self.verifier is not None:
                    task, report = self.engine.verify_with_verifier(task_id, self.verifier, unique_evidence)
                    self.engine.store.add_event(task_id, "executor_verification_result", {"status": report.status.value, "passed": report.passed})
                    return task
                return self.engine.verify_success(task_id, unique_evidence)
            except ValueError as exc:
                # Missing evidence is a terminal execution failure, not a task left stuck in VERIFYING.
                return self.engine.fail(task_id, f"Verification failed: {exc}")
        except Exception as exc:
            latest = self.engine.store.get_task(task_id)
            self.engine.store.add_event(task_id, "executor_error", {"error_type": type(exc).__name__, "message": str(exc)})
            if latest.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}:
                try:
                    return self.engine.fail(task_id, f"Executor error: {exc}")
                except Exception:
                    raise exc
            raise
