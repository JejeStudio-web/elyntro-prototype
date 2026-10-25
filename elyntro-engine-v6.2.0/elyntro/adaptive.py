"""Bounded, auditable plan adaptation for Elyntro Engine.

This module coordinates feedback and safe replanning. It does not claim to reason
like an LLM: without an AdaptationBackend it applies conservative deterministic rules.
Model-proposed revisions are untrusted and validated against the registered tool catalog.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from .planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition


@dataclass(frozen=True)
class ExecutionFeedback:
    status: str  # succeeded, failed, blocked, needs_approval
    summary: str
    completed_step_ids: tuple[str, ...] = ()
    failed_step_id: str | None = None
    evidence_gaps: tuple[str, ...] = ()
    retryable: bool = False

    def validate(self) -> None:
        if self.status not in {"succeeded", "failed", "blocked", "needs_approval"}:
            raise ValueError("unsupported feedback status")
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ValueError("feedback summary must be non-empty")
        if len(set(self.completed_step_ids)) != len(self.completed_step_ids):
            raise ValueError("completed_step_ids must be unique")
        if self.failed_step_id is not None and not isinstance(self.failed_step_id, str):
            raise ValueError("failed_step_id must be a string or None")
        if any(not isinstance(gap, str) or not gap.strip() for gap in self.evidence_gaps):
            raise ValueError("evidence gaps must be non-empty strings")


@dataclass(frozen=True)
class AdaptationDecision:
    action: str  # finish, continue, replan, stop, needs_approval
    rationale: str
    revision_number: int
    plan: ExecutionPlan | None = None
    unresolved_items: tuple[str, ...] = ()


class AdaptationBackend(Protocol):
    name: str

    def revise(
        self,
        goal: str,
        current_plan: ExecutionPlan,
        feedback: ExecutionFeedback,
        constraints: Sequence[str],
        success_criteria: Sequence[str],
        tools: Sequence[ToolDefinition],
        revision_number: int,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class AdaptationPolicy:
    max_revisions: int = 2
    stop_on_approval_gate: bool = True
    require_revision_for_evidence_gaps: bool = True

    def validate(self) -> None:
        if isinstance(self.max_revisions, bool) or not isinstance(self.max_revisions, int) or not 0 <= self.max_revisions <= 10:
            raise ValueError("max_revisions must be an integer between 0 and 10")
        if not isinstance(self.stop_on_approval_gate, bool) or not isinstance(self.require_revision_for_evidence_gaps, bool):
            raise ValueError("policy flags must be booleans")


class AdaptivePlanningLoop:
    """Decide whether to finish, continue, replan, stop, or request approval.

    The loop never executes tools. The caller remains responsible for approval and
    explicitly handing a validated plan to TaskExecutor.
    """

    def __init__(
        self,
        planner: RuleBasedPlanner,
        tools: Sequence[ToolDefinition],
        policy: AdaptationPolicy | None = None,
        backend: AdaptationBackend | None = None,
    ) -> None:
        self.planner = planner
        self.tools = tuple(tools)
        self.policy = policy or AdaptationPolicy()
        self.backend = backend
        self.policy.validate()
        self.catalog = {tool.key: tool for tool in self.tools}
        if len(self.catalog) != len(self.tools):
            raise ValueError("tool keys must be unique")
        for tool in self.tools:
            tool.validate()

    def decide(
        self,
        goal: str,
        current_plan: ExecutionPlan,
        feedback: ExecutionFeedback,
        constraints: Sequence[str] = (),
        success_criteria: Sequence[str] = (),
        revision_number: int = 0,
    ) -> AdaptationDecision:
        feedback.validate()
        if not isinstance(revision_number, int) or isinstance(revision_number, bool) or revision_number < 0:
            raise ValueError("revision_number must be a non-negative integer")
        self._validate_current_plan(current_plan, feedback.completed_step_ids)

        if feedback.status == "succeeded" and not feedback.evidence_gaps:
            return AdaptationDecision("finish", "Execution succeeded and no evidence gaps were reported.", revision_number)
        if feedback.status == "needs_approval" or (
            self.policy.stop_on_approval_gate and current_plan.requires_approval
        ):
            return AdaptationDecision("needs_approval", "A human approval gate must be resolved before execution continues.", revision_number,
                                      unresolved_items=(feedback.summary,))
        if feedback.status == "blocked":
            return AdaptationDecision("stop", "Execution is blocked; automatic adaptation would be unsafe without resolving the blocker.", revision_number,
                                      unresolved_items=(feedback.summary,))
        if feedback.status == "succeeded" and feedback.evidence_gaps and not self.policy.require_revision_for_evidence_gaps:
            return AdaptationDecision("finish", "Execution succeeded; evidence gaps are explicitly non-blocking under policy.", revision_number,
                                      unresolved_items=feedback.evidence_gaps)
        if revision_number >= self.policy.max_revisions:
            return AdaptationDecision("stop", "Maximum number of plan revisions reached.", revision_number,
                                      unresolved_items=(feedback.summary, *feedback.evidence_gaps))
        if feedback.status == "failed" and feedback.retryable and not feedback.evidence_gaps:
            return AdaptationDecision("continue", "The failure is marked retryable; let the executor's bounded retry policy handle it before replanning.", revision_number)

        if self.backend is None:
            return AdaptationDecision("stop", "No adaptation model is connected; refusing to invent a correction. Register an AdaptationBackend or revise the plan manually.", revision_number,
                                      unresolved_items=(feedback.summary, *feedback.evidence_gaps))

        proposal = self.backend.revise(goal, current_plan, feedback, constraints, success_criteria, self.tools, revision_number + 1)
        revised = self.planner._validate_proposal(goal, proposal, self.catalog, self.backend.name)
        self._ensure_completed_steps_preserved(current_plan, revised, feedback.completed_step_ids)
        return AdaptationDecision("replan", "A revised plan was proposed and passed structural/tool validation; inspect it before execution.",
                                  revision_number + 1, plan=revised, unresolved_items=feedback.evidence_gaps)

    def _validate_current_plan(self, plan: ExecutionPlan, completed_step_ids: Sequence[str]) -> None:
        if not plan.steps:
            raise ValueError("current plan must contain at least one step")
        self.planner._validate_dag(plan.steps)
        for step in plan.steps:
            if step.handler_key not in self.catalog:
                raise ValueError(f"plan references unregistered tool: {step.handler_key}")
        unknown_completed = set(completed_step_ids) - {step.step_id for step in plan.steps}
        if unknown_completed:
            raise ValueError(f"completed steps are not present in current plan: {sorted(unknown_completed)}")

    @staticmethod
    def _ensure_completed_steps_preserved(old: ExecutionPlan, new: ExecutionPlan, completed: Sequence[str]) -> None:
        old_steps = {step.step_id: step for step in old.steps}
        new_steps = {step.step_id: step for step in new.steps}
        for step_id in completed:
            if step_id not in new_steps:
                raise ValueError(f"revised plan removed completed step {step_id!r}; this could invalidate recovery")
            before, after = old_steps[step_id], new_steps[step_id]
            if before.handler_key != after.handler_key or before.inputs != after.inputs:
                raise ValueError(f"revised plan changed completed step {step_id!r}; refusing unsafe replay")
