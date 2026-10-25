"""Stepwise durable workflows on the local persistent job ledger.

Handlers are trusted application functions. This module deliberately executes steps
sequentially; dependencies are therefore expressed by references to earlier outputs.
It is a local SQLite prototype, not a distributed exactly-once runtime.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry

WorkflowHandler = Callable[[dict[str, Any]], Any]


class DurableWorkflowService:
    """Persist each workflow operation independently and resume completed steps safely.

    Input references use ``{"$ref": "step_id.output.field"}``. Nested dictionaries and
    lists are resolved recursively. References may only target earlier successful steps.
    A step's ``idempotent`` flag controls lease-expiry recovery; it does not make arbitrary
    side effects transactional.
    """

    TASK_TYPE = "durable_workflow"

    def __init__(self, store: PersistentJobStore, verifiers: TaskVerifierRegistry,
                 handlers: Mapping[str, WorkflowHandler] | None = None,
                 *, worker_id: str = "elyntro-workflow-worker", max_retries: int = 2,
                 retry_on: tuple[type[BaseException], ...] = (TimeoutError, ConnectionError)) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must be non-empty")
        if not isinstance(max_retries, int) or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be an integer between 0 and 10")
        if not isinstance(retry_on, tuple) or any(not isinstance(item, type) or not issubclass(item, BaseException) for item in retry_on):
            raise ValueError("retry_on must be a tuple of exception types")
        self.store, self.verifiers = store, verifiers
        self.handlers = dict(handlers or {})
        self.worker_id = worker_id.strip()
        self.max_retries = max_retries
        self.retry_on = retry_on

    def register_handler(self, key: str, handler: WorkflowHandler) -> None:
        if not key.strip() or not callable(handler):
            raise ValueError("handler key and callable handler are required")
        if key in self.handlers:
            raise ValueError(f"handler already registered: {key}")
        self.handlers[key] = handler

    def submit(self, *, workflow_id: str, inputs: dict[str, Any], steps: list[JobStep],
               idempotency_key: str, metadata: dict[str, Any] | None = None) -> str:
        if not isinstance(inputs, dict) or not isinstance(metadata or {}, dict):
            raise ValueError("inputs and metadata must be dictionaries")
        if not steps:
            raise ValueError("workflow must contain at least one step")
        seen: set[str] = set()
        for step in steps:
            step.validate()
            if step.step_id in seen:
                raise ValueError("step IDs must be unique")
            if step.handler_key not in self.handlers:
                raise ValueError(f"unregistered handler: {step.handler_key}")
            self._validate_refs(step.inputs, seen)
            seen.add(step.step_id)
        return self.store.create_job(
            idempotency_key=idempotency_key, task_type=self.TASK_TYPE,
            payload={"workflow_id": workflow_id, "inputs": inputs, "metadata": metadata or {}},
            steps=steps,
        )

    def process_one(self, *, job_id: str | None = None, lease_seconds: float = 300.0) -> dict[str, Any] | None:
        job = self.store.claim_next(worker_id=self.worker_id, lease_seconds=lease_seconds, job_id=job_id)
        if job is None:
            return None
        jid = job["job_id"]
        try:
            outputs = {s["step_id"]: s["output"] for s in job["steps"] if s["state"] == "succeeded"}
            for step in job["steps"]:
                if step["state"] == "succeeded":
                    continue
                if step["state"] != "pending":
                    raise RuntimeError(f"Step {step['step_id']} is not safely runnable: {step['state']}")
                self.store.start_step(jid, step["step_id"], worker_id=self.worker_id)
                resolved = self._resolve(step["inputs"], outputs, job["payload"]["inputs"])
                retry_count = 0
                while True:
                    try:
                        raw = self.handlers[step["handler_key"]](resolved)
                        if isinstance(raw, dict):
                            output = raw
                        else:
                            output = {"value": raw}
                        # Validate persistence boundary now, before recording success.
                        import json
                        json.dumps(output, ensure_ascii=False, allow_nan=False)
                        self.store.finish_step(jid, step["step_id"], output=output)
                        outputs[step["step_id"]] = output
                        break
                    except self.retry_on as exc:
                        retry_allowed = bool(step["idempotent"]) and retry_count < self.max_retries
                        if retry_allowed:
                            retry_count += 1
                            self.store.record_step_retry(jid, step["step_id"], worker_id=self.worker_id,
                                                         error=f"{type(exc).__name__}: {exc}")
                            continue
                        self.store.fail_step(jid, step["step_id"], error=f"{type(exc).__name__}: {exc}")
                        return self.store.get_job(jid)
                    except Exception as exc:
                        self.store.fail_step(jid, step["step_id"], error=f"{type(exc).__name__}: {exc}")
                        return self.store.get_job(jid)
            result = {"workflow_id": job["payload"]["workflow_id"], "outputs": outputs}
            verification = self.verifiers.verify(self.TASK_TYPE, result, {
                "job_id": jid, "workflow_id": job["payload"]["workflow_id"],
                "metadata": job["payload"].get("metadata", {}),
            })
            if verification.get("passed") is True:
                self.store.complete_job(jid, result=result, verification=verification)
            else:
                self.store.fail_job(jid, error=str(verification.get("reason") or "workflow verification failed"))
            return self.store.get_job(jid)
        except Exception:
            # Leave an in-flight step for lease recovery/reconciliation rather than
            # automatically replaying a possibly side-effecting handler.
            raise

    def approve_and_resume(self, job_id: str, *, approved_by: str) -> dict[str, Any] | None:
        self.store.approve(job_id, approved_by=approved_by)
        return self.process_one(job_id=job_id)

    @classmethod
    def _validate_refs(cls, value: Any, earlier: set[str]) -> None:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                ref = value["$ref"]
                if not isinstance(ref, str) or len(ref.split(".")) < 2:
                    raise ValueError("reference must be 'input.field' or 'step_id.output.field'")
                parts = ref.split(".")
                if parts[0] == "input":
                    return
                if len(parts) < 2 or parts[0] not in earlier or parts[1] != "output":
                    raise ValueError("references must target an earlier step output")
            else:
                for item in value.values():
                    cls._validate_refs(item, earlier)
        elif isinstance(value, list):
            for item in value:
                cls._validate_refs(item, earlier)

    @classmethod
    def _resolve(cls, value: Any, outputs: dict[str, Any], root_inputs: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                parts = value["$ref"].split(".")
                if parts[0] == "input":
                    current: Any = root_inputs
                    parts = parts[1:]
                else:
                    current = outputs.get(parts[0])
                    if current is None or parts[1] != "output":
                        raise ValueError(f"reference output unavailable: {value['$ref']}")
                    parts = parts[2:]
                for part in parts:
                    if not isinstance(current, dict) or part not in current:
                        raise ValueError(f"reference field unavailable: {value['$ref']}")
                    current = current[part]
                return current
            return {k: cls._resolve(v, outputs, root_inputs) for k, v in value.items()}
        if isinstance(value, list):
            return [cls._resolve(v, outputs, root_inputs) for v in value]
        return value
