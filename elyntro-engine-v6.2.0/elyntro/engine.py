"""Core task lifecycle; execution is deliberately deterministic in v0.1."""
from __future__ import annotations
from typing import Any

from .contracts import Task, TaskRequest, TaskStatus
from .state_machine import ensure_transition
from .store import SQLiteStore


class Engine:
    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def submit(self, request: TaskRequest) -> Task:
        request.validate()
        task = Task.from_request(request)
        return self.store.create_task(task, request.idempotency_key)

    def transition(self, task_id: str, target: TaskStatus, reason: str | None = None) -> Task:
        task = self.store.get_task(task_id)
        ensure_transition(task.status, target)
        old = task.status
        task.status = target
        self.store.save_task(task)
        self.store.add_event(task_id, "status_changed", {"from": old.value, "to": target.value, "reason": reason})
        return task

    def set_plan(self, task_id: str, steps: list[str]) -> Task:
        if not isinstance(steps, list) or not steps or any(not isinstance(s, str) or not s.strip() for s in steps):
            raise ValueError("plan must contain at least one non-empty step")
        task = self.store.get_task(task_id)
        if task.status != TaskStatus.PLANNING:
            raise ValueError("a plan can only be set while task status is planning")
        task.plan = [s.strip() for s in steps]
        self.store.save_task(task)
        self.store.add_event(task_id, "plan_created", {"step_count": len(steps)})
        return task

    def add_artifact(self, task_id: str, name: str, content: Any, media_type: str = "text/plain") -> Task:
        if not name.strip() or not media_type.strip():
            raise ValueError("artifact name and media_type must be non-empty")
        task = self.store.get_task(task_id)
        if task.status != TaskStatus.RUNNING:
            raise ValueError("artifacts can only be added while task status is running")
        artifact = {"name": name, "content": content, "media_type": media_type}
        task.artifacts.append(artifact)
        self.store.save_task(task)
        self.store.add_event(task_id, "artifact_added", {"name": name, "media_type": media_type})
        return task

    def verify_success(self, task_id: str, evidence: list[dict[str, Any]]) -> Task:
        """Complete only when evidence exists for every declared success criterion.

        Evidence items must have criterion and detail fields. This is structural
        verification, not proof that evidence is truthful; stronger verifiers come later.
        """
        task = self.store.get_task(task_id)
        if task.status != TaskStatus.VERIFYING:
            raise ValueError("task must be in verifying state")
        if not isinstance(evidence, list) or any(
            not isinstance(item, dict) or not str(item.get("criterion", "")).strip() or not str(item.get("detail", "")).strip()
            for item in evidence
        ):
            raise ValueError("each evidence item must include non-empty criterion and detail fields")
        covered = {item["criterion"].strip() for item in evidence}
        missing = [criterion for criterion in task.success_criteria if criterion not in covered]
        if missing:
            self.store.add_event(task_id, "verification_rejected", {"missing_criteria": missing})
            raise ValueError("missing evidence for success criteria: " + "; ".join(missing))
        task.evidence.extend(evidence)
        self.store.save_task(task)
        self.store.add_event(task_id, "evidence_recorded", {"count": len(evidence)})
        return self.transition(task_id, TaskStatus.SUCCEEDED, "all declared criteria have recorded evidence")


    def verify_with_verifier(self, task_id: str, verifier, evidence: list[dict[str, Any]] | None = None):
        """Run independent criterion validators and update task lifecycle safely.

        VERIFIED -> SUCCEEDED; FAILED -> FAILED; INCONCLUSIVE -> NEEDS_APPROVAL.
        This method intentionally does not treat mere evidence presence as proof.
        """
        from .verifier import VerificationStatus

        task = self.store.get_task(task_id)
        if task.status != TaskStatus.VERIFYING:
            raise ValueError("task must be in verifying state")
        supplied_evidence = task.evidence if evidence is None else evidence
        report = verifier.verify(
            task.success_criteria,
            supplied_evidence,
            artifacts=task.artifacts,
            context={"goal": task.goal, "constraints": task.constraints, "metadata": task.metadata},
            task_id=task.task_id,
        )
        if evidence is not None:
            task.evidence.extend(evidence)
            self.store.save_task(task)
            self.store.add_event(task_id, "evidence_recorded", {"count": len(evidence), "verification_mode": "independent"})
        self.store.add_event(task_id, "independent_verification", report.to_dict())
        if report.status == VerificationStatus.VERIFIED:
            return self.transition(task_id, TaskStatus.SUCCEEDED, "all criteria independently verified"), report
        if report.status == VerificationStatus.FAILED:
            return self.transition(task_id, TaskStatus.FAILED, "independent verification failed"), report
        return self.transition(task_id, TaskStatus.NEEDS_APPROVAL, "verification inconclusive; human review required"), report

    def verify_with_cycle(
        self,
        task_id: str,
        cycle,
        *,
        evidence: list[dict[str, Any]] | None = None,
        policy_engine=None,
        repair=None,
        repair_tool_key: str = "task.repair",
        repair_risk=None,
    ):
        """Run bounded verification/recovery with governance enforced on repairs.

        The task can succeed only when the independent verifier returns VERIFIED.
        Each repair attempt must be authorized before application code runs. A
        denied/approval-required repair is never invoked and leaves the task in
        NEEDS_APPROVAL; exhausted or unsuccessful repairs leave it FAILED.
        """
        from uuid import uuid4
        from .governance import ActionRequest, RiskLevel
        from .verifier import VerificationStatus

        task = self.store.get_task(task_id)
        if task.status != TaskStatus.VERIFYING:
            raise ValueError("task must be in verifying state")
        if repair is not None and policy_engine is None:
            raise ValueError("policy_engine is required when a repair callback is supplied")
        if not isinstance(repair_tool_key, str) or not repair_tool_key.strip():
            raise ValueError("repair_tool_key must be a non-empty string")
        selected_risk = repair_risk or RiskLevel.MEDIUM
        supplied_evidence = list(task.evidence if evidence is None else evidence)
        repair_decisions: list[dict[str, Any]] = []

        def governed_repair(report, current_evidence, context):
            action = ActionRequest(
                action_id=f"{task_id}:repair:{uuid4().hex}",
                tool_key=repair_tool_key,
                description=f"Repair task after verification failure: {task.goal[:160]}",
                risk=selected_risk,
                estimated_tokens=0,
                estimated_cost_usd=0.0,
                metadata={"task_id": task_id, "failed_criteria": list(report.failed_criteria)},
            )
            decision = policy_engine.authorize(action)
            repair_decisions.append(decision.to_dict())
            self.store.add_event(task_id, "repair_governance_decision", decision.to_dict())
            if not decision.allowed:
                raise PermissionError(f"repair not authorized: {decision.outcome} ({decision.reason})")
            return repair(report, current_evidence, context)

        result = cycle.run(
            task.success_criteria,
            supplied_evidence,
            artifacts=task.artifacts,
            context={"goal": task.goal, "constraints": task.constraints, "metadata": task.metadata},
            task_id=task_id,
            repair=governed_repair if repair is not None else None,
        )
        task.evidence = [dict(item) for item in result_evidence(result, supplied_evidence)]
        task.artifacts = [dict(item) for item in getattr(result, "final_artifacts", task.artifacts)]
        self.store.save_task(task)
        self.store.add_event(task_id, "verification_cycle_completed", {
            **result.to_dict(), "repair_governance_decisions": repair_decisions,
        })
        if result.report.status == VerificationStatus.VERIFIED:
            updated = self.transition(task_id, TaskStatus.SUCCEEDED, "verification cycle independently verified all criteria")
        elif any(item.get("outcome") in {"require_approval", "deny", "budget_exceeded"} for item in repair_decisions):
            updated = self.transition(task_id, TaskStatus.NEEDS_APPROVAL, "repair blocked by governance; human review required")
        elif result.report.status == VerificationStatus.INCONCLUSIVE:
            updated = self.transition(task_id, TaskStatus.NEEDS_APPROVAL, "verification inconclusive; human review required")
        else:
            updated = self.transition(task_id, TaskStatus.FAILED, "verification failed after bounded repair attempts")
        return updated, result

    def fail(self, task_id: str, message: str, retryable: bool = False) -> Task:
        if not message.strip():
            raise ValueError("failure message cannot be blank")
        task = self.store.get_task(task_id)
        task.errors.append({"message": message, "retryable": retryable})
        if retryable:
            task.retry_count += 1
        self.store.save_task(task)
        self.store.add_event(task_id, "error_recorded", {"message": message, "retryable": retryable})
        return self.transition(task_id, TaskStatus.FAILED, message)

    def cancel(self, task_id: str, reason: str = "cancelled by user") -> Task:
        return self.transition(task_id, TaskStatus.CANCELLED, reason)


def result_evidence(result, fallback):
    """Return final cycle evidence, falling back for compatibility with older results."""
    final_evidence = getattr(result, "final_evidence", None)
    return final_evidence if final_evidence is not None else fallback
