"""Integrated, policy-gated failure diagnosis and adaptive recovery proposals.

This coordinator proposes a safe next action; it never executes tools or retries by
itself. A retry is eligible only when the caller explicitly confirms idempotency.
Revised plans are validated by AdaptivePlanningLoop before being returned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .adaptive import AdaptationDecision, AdaptivePlanningLoop, ExecutionFeedback
from .failure_diagnosis import FailureDiagnosis, FailureDiagnoser
from .planner import ExecutionPlan


@dataclass(frozen=True)
class RecoveryProposal:
    diagnosis: FailureDiagnosis
    feedback: ExecutionFeedback
    decision: AdaptationDecision
    operation_idempotent: bool
    execution_authorized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis.to_dict(),
            "feedback": {
                "status": self.feedback.status,
                "summary": self.feedback.summary,
                "completed_step_ids": list(self.feedback.completed_step_ids),
                "failed_step_id": self.feedback.failed_step_id,
                "evidence_gaps": list(self.feedback.evidence_gaps),
                "retryable": self.feedback.retryable,
            },
            "decision": {
                "action": self.decision.action,
                "rationale": self.decision.rationale,
                "revision_number": self.decision.revision_number,
                "unresolved_items": list(self.decision.unresolved_items),
                "plan": None if self.decision.plan is None else {
                    "goal": self.decision.plan.goal,
                    "planner_name": self.decision.plan.planner_name,
                    "requires_approval": self.decision.plan.requires_approval,
                    "steps": [
                        {"step_id": s.step_id, "name": s.name, "handler_key": s.handler_key,
                         "inputs": s.inputs, "depends_on": list(s.depends_on),
                         "requires_approval": s.requires_approval, "rationale": s.rationale}
                        for s in self.decision.plan.steps
                    ],
                },
            },
            "operation_idempotent": self.operation_idempotent,
            "execution_authorized": self.execution_authorized,
        }


class AdaptiveRecoveryCoordinator:
    """Connect failure classification to the adaptive planning decision boundary.

    The caller must supply execution evidence and the actual idempotency property.
    Unknown failures remain non-retryable. A returned proposal is not permission to
    execute: the application must still enforce approval, budgets and tool policy.
    """

    def __init__(self, adaptive_loop: AdaptivePlanningLoop,
                 diagnoser: FailureDiagnoser | None = None) -> None:
        self.adaptive_loop = adaptive_loop
        self.diagnoser = diagnoser or FailureDiagnoser()

    def propose(self, *, goal: str, current_plan: ExecutionPlan,
                error: BaseException | str | None = None,
                completed_step_ids: Iterable[str] = (),
                failed_step_id: str | None = None,
                evidence_gaps: Iterable[str] = (),
                approval_required: bool = False,
                dependency_failed: bool = False,
                tool_registered: bool | None = None,
                permission_denied: bool = False,
                operation_idempotent: bool = False,
                constraints: Sequence[str] = (),
                success_criteria: Sequence[str] = (),
                revision_number: int = 0) -> RecoveryProposal:
        completed = tuple(completed_step_ids)
        gaps = tuple(evidence_gaps)
        diagnosis = self.diagnoser.diagnose(
            error=error, evidence_gaps=gaps, approval_required=approval_required,
            dependency_failed=dependency_failed, tool_registered=tool_registered,
            permission_denied=permission_denied,
        )
        feedback = diagnosis.to_feedback(
            completed_step_ids=completed, failed_step_id=failed_step_id,
            evidence_gaps=gaps,
        )
        # Idempotency is a property of the actual operation, not of an exception type.
        # Never let the diagnostic heuristic alone authorize retry.
        if not operation_idempotent or diagnosis.requires_human or gaps:
            feedback = ExecutionFeedback(
                status=feedback.status, summary=feedback.summary,
                completed_step_ids=feedback.completed_step_ids,
                failed_step_id=feedback.failed_step_id,
                evidence_gaps=feedback.evidence_gaps, retryable=False,
            )
        decision = self.adaptive_loop.decide(
            goal, current_plan, feedback, constraints=constraints,
            success_criteria=success_criteria, revision_number=revision_number,
        )
        return RecoveryProposal(
            diagnosis=diagnosis, feedback=feedback, decision=decision,
            operation_idempotent=bool(operation_idempotent), execution_authorized=False,
        )
