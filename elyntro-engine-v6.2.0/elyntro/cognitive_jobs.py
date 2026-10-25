"""Durable job-queue bridge for the integrated CognitiveLoop.

This bridge is a single-host prototype. A CognitiveLoop run is treated as one
non-idempotent queue step: if the worker dies mid-run, the job requires explicit
reconciliation rather than blindly replaying possible side effects.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .contracts import TaskRequest, TaskStatus
from .cognitive_loop import CognitiveLoop, CognitiveRunResult
from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


class CognitiveJobService:
    """Submit CognitiveLoop requests to a durable queue and process one job at a time."""

    TASK_TYPE = "cognitive_goal"
    STEP_ID = "cognitive-loop"
    HANDLER_KEY = "cognitive_loop.run"

    def __init__(self, store: PersistentJobStore, loop: CognitiveLoop,
                 verifiers: TaskVerifierRegistry, *, worker_id: str = "elyntro-worker") -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")
        self.store = store
        self.loop = loop
        self.verifiers = verifiers
        self.worker_id = worker_id.strip()

    def submit(self, request: TaskRequest, *, namespace: str = "default",
               idempotency_key: str | None = None) -> str:
        request.validate()
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        key = idempotency_key or request.idempotency_key
        if not isinstance(key, str) or not key.strip():
            raise ValueError("idempotency_key is required for durable cognitive jobs")
        payload = {
            "request": {
                "goal": request.goal,
                "constraints": list(request.constraints),
                "success_criteria": list(request.success_criteria),
                "idempotency_key": request.idempotency_key or key,
                "metadata": request.metadata,
            },
            "namespace": namespace.strip(),
        }
        return self.store.create_job(
            idempotency_key=key,
            task_type=self.TASK_TYPE,
            payload=payload,
            steps=[JobStep(
                step_id=self.STEP_ID,
                handler_key=self.HANDLER_KEY,
                inputs={"request_key": request.idempotency_key or key},
                idempotent=False,
                requires_approval=False,
            )],
        )

    def process_one(self, *, lease_seconds: float = 300.0, job_id: str | None = None) -> dict[str, Any] | None:
        job = self.store.claim_next(worker_id=self.worker_id, lease_seconds=lease_seconds, job_id=job_id)
        if job is None:
            return None
        job_id = job["job_id"]
        try:
            step = job["steps"][0]
            if step["step_id"] != self.STEP_ID or step["handler_key"] != self.HANDLER_KEY:
                raise ValueError("Queued job does not match the cognitive job contract")

            # A previously paused cognitive task is approved by the user at the job
            # layer, then the exact persisted plan is resumed through CognitiveLoop.approve.
            if step["state"] == "succeeded":
                output = step.get("output") or {}
                task_id = output.get("cognitive_task_id")
                if not task_id:
                    raise ValueError("Persisted cognitive task ID is missing")
                run_result = self.loop.approve(task_id)
            elif step["state"] == "pending":
                self.store.start_step(job_id, self.STEP_ID, worker_id=self.worker_id)
                raw = job["payload"]["request"]
                request = TaskRequest(
                    goal=raw["goal"], constraints=raw.get("constraints", []),
                    success_criteria=raw.get("success_criteria", []),
                    idempotency_key=raw.get("idempotency_key"), metadata=raw.get("metadata", {}),
                )
                run_result = self.loop.run(request, namespace=job["payload"].get("namespace", "default"))
            else:
                raise ValueError(f"Cannot process cognitive step in state {step['state']}")

            result = self._serialize_result(run_result)
            if run_result.awaiting_approval:
                # Persist the returned task ID before pausing. Approval never reconstructs
                # a plan from untrusted input; CognitiveLoop loads its validated stored plan.
                if step["state"] == "pending":
                    self.store.finish_step(job_id, self.STEP_ID, output={
                        "cognitive_task_id": run_result.task.task_id,
                        "task_status": run_result.task.status.value,
                    })
                self.store.pause_for_approval(job_id, detail={
                    "cognitive_task_id": run_result.task.task_id,
                    "reason": "CognitiveLoop requires explicit approval",
                })
                return self.store.get_job(job_id)

            verification = self.verifiers.verify(self.TASK_TYPE, result, {
                "job_id": job_id, "goal": run_result.task.goal,
                "success_criteria": run_result.task.success_criteria,
            })
            if run_result.task.status == TaskStatus.SUCCEEDED and verification.get("passed") is True:
                if step["state"] == "pending":
                    self.store.finish_step(job_id, self.STEP_ID, output={
                        "cognitive_task_id": run_result.task.task_id,
                        "task_status": run_result.task.status.value,
                    })
                self.store.complete_job(job_id, result=result, verification=verification)
            else:
                reason = verification.get("reason") or f"Cognitive task ended with {run_result.task.status.value}"
                if step["state"] == "pending":
                    self.store.fail_step(job_id, self.STEP_ID, error=str(reason))
                else:
                    self.store.fail_job(job_id, error=str(reason))
            return self.store.get_job(job_id)
        except Exception as exc:
            # Avoid silently replaying a step that might have already produced external
            # side effects. Leave a started step for lease recovery/reconciliation.
            current = self.store.get_job(job_id)
            if current["steps"] and current["steps"][0]["state"] == "pending":
                self.store.start_step(job_id, self.STEP_ID, worker_id=self.worker_id)
                self.store.fail_step(job_id, self.STEP_ID, error=f"{type(exc).__name__}: {exc}")
            raise

    def approve(self, job_id: str, *, approved_by: str) -> dict[str, Any] | None:
        self.store.approve(job_id, approved_by=approved_by)
        return self.process_one(job_id=job_id)

    @staticmethod
    def _serialize_result(result: CognitiveRunResult) -> dict[str, Any]:
        task = result.task.to_dict()
        plan = None
        if result.plan is not None:
            plan = {
                "goal": result.plan.goal,
                "planner_name": result.plan.planner_name,
                "rationale": list(result.plan.rationale),
                "steps": [{
                    "step_id": step.step_id, "name": step.name,
                    "handler_key": step.handler_key, "depends_on": list(step.depends_on),
                } for step in result.plan.steps],
            }
        return {
            "task": task, "plan": plan,
            "verification": result.verification,
            "adaptation": result.adaptation,
            "plan_review": result.plan_review,
            "relevant_memory_count": len(result.relevant_memories),
        }
