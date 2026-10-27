"""Integrated, auditable goal-to-result workflow for Elyntro Engine v1.0.

This is orchestration around registered application handlers, not a foundation model.
Plans and model/backend proposals are untrusted and are structurally validated before
execution. Approval-required plans pause until approve() is explicitly called.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .adaptive import AdaptivePlanningLoop, ExecutionFeedback
from .contracts import Task, TaskRequest, TaskStatus
from .engine import Engine
from .memory import MemoryStore
from .planner import ExecutionPlan, PlanningService, PlannedStep, RuleBasedPlanner, ToolDefinition
from .task_executor import TaskExecutor, TaskStep
from .verifier import EvidenceVerifier, VerificationReport
from .plan_review import PlanQualityReviewer


@dataclass(frozen=True)
class CognitiveRunResult:
    task: Task
    plan: ExecutionPlan | None
    relevant_memories: tuple[dict[str, Any], ...] = ()
    verification: dict[str, Any] | None = None
    adaptation: dict[str, Any] | None = None
    plan_review: dict[str, Any] | None = None

    @property
    def finished(self) -> bool:
        return self.task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}

    @property
    def awaiting_approval(self) -> bool:
        return self.task.status == TaskStatus.NEEDS_APPROVAL


class CognitiveLoop:
    """Connect task persistence, contextual memory, planning, execution and verification.

    All side effects occur through explicitly registered TaskExecutor handlers. The
    loop never executes a model-generated command or arbitrary code. Memory is
    retrieved only from the caller-supplied namespace; authentication/authorization
    for that namespace remains the hosting application's responsibility.
    """

    PLAN_METADATA_KEY = "elyntro_cognitive_loop"

    def __init__(
        self,
        engine: Engine,
        planner: RuleBasedPlanner,
        tools: Sequence[ToolDefinition],
        executor: TaskExecutor,
        *,
        memory: MemoryStore | None = None,
        verifier: EvidenceVerifier | None = None,
        adaptation: AdaptivePlanningLoop | None = None,
        memory_limit: int = 8,
        reviewer: PlanQualityReviewer | None = None,
        review_min_score: float | None = None,
        critic: Any | None = None,
        require_independent_verification: bool = True,
    ) -> None:
        if not isinstance(require_independent_verification, bool):
            raise ValueError("require_independent_verification must be boolean")
        if not 1 <= memory_limit <= 100:
            raise ValueError("memory_limit must be between 1 and 100")
        self.engine = engine
        self.planner = planner
        self.tools = tuple(tools)
        self.executor = executor
        self.memory = memory
        # The canonical cognitive path fails closed: handler-provided evidence is a
        # claim, not proof. With no domain validator, EvidenceVerifier returns
        # INCONCLUSIVE and the task requires human review instead of false success.
        # Legacy structural-only behavior is available only by explicit opt-out.
        self.require_independent_verification = require_independent_verification
        self.verifier = verifier if verifier is not None else (
            EvidenceVerifier() if require_independent_verification else None
        )
        self.adaptation = adaptation
        self.memory_limit = memory_limit
        self.reviewer = reviewer or PlanQualityReviewer()
        self.critic = critic
        if review_min_score is not None and not 0 <= review_min_score <= 100:
            raise ValueError("review_min_score must be between 0 and 100 or None")
        self.review_min_score = review_min_score
        self.planning = PlanningService(engine, planner, self.tools)
        self.catalog = {tool.key: tool for tool in self.tools}
        if len(self.catalog) != len(self.tools):
            raise ValueError("tool keys must be unique")
        # Ensure the executor can run every planned handler before a task is accepted.
        missing = sorted({tool.key for tool in self.tools if tool.key not in executor.handlers})
        if missing:
            raise ValueError("tools lack registered executor handlers: " + ", ".join(missing))
        self.executor.verifier = self.verifier

    def run(self, request: TaskRequest, *, namespace: str = "default") -> CognitiveRunResult:
        """Submit, retrieve relevant memory, plan, gate approvals, then execute."""
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        task = self.engine.submit(request)
        # Idempotent re-submission returns the existing task; never silently rerun it.
        existing_events = self.engine.store.list_events(task.task_id)
        if any(e["event_type"] == "cognitive_loop_started" for e in existing_events):
            state = task.metadata.get(self.PLAN_METADATA_KEY, {})
            persisted_plan = state.get("plan") if isinstance(state, dict) else None
            if task.status in {TaskStatus.RUNNING, TaskStatus.READY} and isinstance(persisted_plan, dict):
                # The executor resumes from durable step_result_committed events.
                # Completed steps are reused; only steps without a committed result run again.
                plan = self._deserialize_plan(persisted_plan)
                steps = plan.to_task_steps(self.tools)
                task = self.executor.execute(task.task_id, steps)
                return self._finish_result(task, plan, state.get("memory_context", []))
            if task.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED,
                               TaskStatus.NEEDS_APPROVAL}:
                return self._result_for_existing(task.task_id)
            # Planning was interrupted before a complete plan was committed. Do not
            # guess which partial planning side effects occurred or loop forever.
            latest = self.engine.store.get_task(task.task_id)
            try:
                failed = self.engine.fail(
                    task.task_id,
                    "Execution interrupted before a resumable plan was committed; submit a new task to re-plan safely.",
                )
                self.engine.store.add_event(task.task_id, "recovery_stopped", {
                    "reason": "plan_not_committed", "previous_status": latest.status.value,
                })
                return self._finish_result(failed, self._load_plan(failed), state.get("memory_context", []))
            except Exception:
                return self._result_for_existing(task.task_id)

        memories: list[dict[str, Any]] = []
        if self.memory is not None:
            memories = self.memory.retrieve(namespace.strip(), request.goal, limit=self.memory_limit)
        task.metadata[self.PLAN_METADATA_KEY] = {
            "namespace": namespace.strip(),
            "memory_ids": [item["memory_id"] for item in memories],
            "memory_context": memories,
            "approval_required": False,
        }
        self.engine.store.save_task(task)
        self.engine.store.add_event(task.task_id, "cognitive_loop_started", {
            "namespace": namespace.strip(), "relevant_memory_count": len(memories),
            "memory_ids": [item["memory_id"] for item in memories],
        })
        try:
            self.engine.transition(task.task_id, TaskStatus.VALIDATING, "cognitive loop accepted request")
            self.engine.transition(task.task_id, TaskStatus.PLANNING, "retrieved context and preparing plan")
            plan = self.planning.create_plan(task.task_id, context={
                "memories": memories,
                "namespace": namespace.strip(),
                "task_id": task.task_id,
            })
            plan = self._attach_memory_context(plan, memories)
            critic_error = None
            if self.critic is not None:
                try:
                    plan, review = self.reviewer.review_and_repair(
                        plan, self.planner, self.tools,
                        constraints=task.constraints,
                        success_criteria=task.success_criteria,
                        backend=self.critic,
                        max_repairs=1,
                    )
                except Exception as exc:
                    # A failed optional critic must not silently replace the original plan;
                    # continue only with deterministic review and preserve the failure event.
                    critic_error = {"error_type": type(exc).__name__, "message": str(exc)[:500]}
                    review = self.reviewer.review(plan, self.tools, task.constraints, task.success_criteria)
            else:
                review = self.reviewer.review(plan, self.tools, task.constraints, task.success_criteria)
            review_payload = review.to_dict()
            if self.critic is not None:
                review_payload["independent_critique"] = getattr(self.critic, "last_critique", None)
                if critic_error is not None:
                    review_payload["critic_error"] = critic_error
            task = self.engine.store.get_task(task.task_id)
            task.plan = [step.name for step in plan.steps]
            state = task.metadata[self.PLAN_METADATA_KEY]
            review_gate = self.review_min_score is not None and review.score < self.review_min_score
            state["approval_required"] = plan.requires_approval or review_gate
            state["plan_review"] = review_payload
            state["review_gate"] = review_gate
            state["plan"] = self._serialize_plan(plan)
            self.engine.store.save_task(task)
            self.engine.store.add_event(task.task_id, "cognitive_plan_committed", {
                "planner": plan.planner_name,
                "steps": [self._serialize_step(step) for step in plan.steps],
                "requires_approval": plan.requires_approval or review_gate,
                "plan_review": review_payload,
                "memory_context_passed_to_planner": bool(memories),
                "memory_context_passed_to_handlers": bool(memories),
                "model_call": getattr(getattr(self.planner, "backend", None), "last_call_metadata", None),
                "critic_call": getattr(self.critic, "last_call_metadata", None),
                "independent_critique": getattr(self.critic, "last_critique", None),
            })
            if plan.requires_approval or review_gate:
                reason = "plan contains a tool requiring human approval" if plan.requires_approval else "plan quality score is below configured approval threshold"
                task = self.engine.transition(task.task_id, TaskStatus.NEEDS_APPROVAL, reason)
                self.engine.store.add_event(task.task_id, "approval_requested", {
                    "reason": reason,
                    "step_ids": [step.step_id for step in plan.steps if step.requires_approval],
                    "plan_review_score": review.score, "review_threshold": self.review_min_score,
                })
                return CognitiveRunResult(task, plan, tuple(memories), plan_review=review_payload)
            self.engine.transition(task.task_id, TaskStatus.READY, "plan validated and requires no approval")
            task = self.executor.execute(task.task_id, plan.to_task_steps(self.tools))
            return self._finish_result(task, plan, memories)
        except Exception as exc:
            latest = self.engine.store.get_task(task.task_id)
            self.engine.store.add_event(task.task_id, "cognitive_loop_error", {
                "error_type": type(exc).__name__, "message": str(exc),
            })
            if latest.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.NEEDS_APPROVAL}:
                try:
                    task = self.engine.fail(task.task_id, f"Cognitive loop error: {type(exc).__name__}: {exc}")
                except Exception:
                    raise exc
            else:
                task = self.engine.store.get_task(task.task_id)
            return self._finish_result(task, self._load_plan(task), memories)

    def approve(self, task_id: str) -> CognitiveRunResult:
        """Approve a paused plan and execute exactly the persisted, validated plan."""
        task = self.engine.store.get_task(task_id)
        if task.status != TaskStatus.NEEDS_APPROVAL:
            raise ValueError("only tasks awaiting approval can be approved")
        state = task.metadata.get(self.PLAN_METADATA_KEY, {})
        raw_plan = state.get("plan")
        if not isinstance(raw_plan, dict):
            raise ValueError("persisted approval plan is missing or invalid")
        plan = self._deserialize_plan(raw_plan)
        # Revalidate handler mapping and inputs at the trust boundary.
        steps = plan.to_task_steps(self.tools)
        for step in steps:
            step.validate()
        self.engine.store.add_event(task_id, "approval_granted", {"step_count": len(steps)})
        self.engine.transition(task_id, TaskStatus.READY, "human approved persisted plan")
        result = self.executor.execute(task_id, steps)
        memories = tuple(state.get("memory_context", []))
        return self._finish_result(result, plan, list(memories))

    def _finish_result(self, task: Task, plan: ExecutionPlan | None,
                       memories: Sequence[dict[str, Any]]) -> CognitiveRunResult:
        events = self.engine.store.list_events(task.task_id)
        verification = next((e["payload"] for e in reversed(events)
                             if e["event_type"] == "independent_verification"), None)
        if verification is None:
            verification = next((e["payload"] for e in reversed(events)
                                 if e["event_type"] == "executor_verification_result"), None)
        adaptation_payload = None
        if self.adaptation is not None and plan is not None and task.status in {
            TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.NEEDS_APPROVAL
        }:
            completed = tuple(e["payload"]["step_id"] for e in events
                              if e["event_type"] == "step_succeeded" and "step_id" in e["payload"])
            failed_event = next((e for e in reversed(events) if e["event_type"] == "step_attempt_failed"), None)
            gaps = tuple(c for c in task.success_criteria
                         if not any(item.get("criterion") == c for item in task.evidence))
            status = "succeeded" if task.status == TaskStatus.SUCCEEDED and not gaps else (
                "needs_approval" if task.status == TaskStatus.NEEDS_APPROVAL else "failed")
            feedback = ExecutionFeedback(
                status=status,
                summary=f"Task ended with status={task.status.value}; evidence gaps={list(gaps)}",
                completed_step_ids=tuple(dict.fromkeys(completed)),
                failed_step_id=failed_event["payload"].get("step_id") if failed_event else None,
                evidence_gaps=gaps,
                retryable=bool(failed_event and failed_event["payload"].get("will_retry")),
            )
            try:
                decision = self.adaptation.decide(
                    plan.goal, plan, feedback,
                    constraints=task.constraints,
                    success_criteria=task.success_criteria,
                    revision_number=0,
                )
                adaptation_payload = {
                    "action": decision.action, "rationale": decision.rationale,
                    "revision_number": decision.revision_number,
                    "unresolved_items": list(decision.unresolved_items),
                    "proposed_plan": self._serialize_plan(decision.plan) if decision.plan else None,
                }
                self.engine.store.add_event(task.task_id, "adaptation_decision", adaptation_payload)
            except Exception as exc:
                adaptation_payload = {"action": "error", "error_type": type(exc).__name__, "message": str(exc)}
                self.engine.store.add_event(task.task_id, "adaptation_error", adaptation_payload)
        state = self.engine.store.get_task(task.task_id).metadata.get(self.PLAN_METADATA_KEY, {})
        return CognitiveRunResult(self.engine.store.get_task(task.task_id), plan,
                                  tuple(memories), verification, adaptation_payload, state.get("plan_review"))

    def _result_for_existing(self, task_id: str) -> CognitiveRunResult:
        task = self.engine.store.get_task(task_id)
        state = task.metadata.get(self.PLAN_METADATA_KEY, {})
        plan = self._deserialize_plan(state["plan"]) if isinstance(state.get("plan"), dict) else None
        events = self.engine.store.list_events(task_id)
        verification = next((e["payload"] for e in reversed(events)
                             if e["event_type"] == "independent_verification"), None)
        if verification is None:
            verification = next((e["payload"] for e in reversed(events)
                                 if e["event_type"] == "executor_verification_result"), None)
        adaptation = next((e["payload"] for e in reversed(events)
                           if e["event_type"] == "adaptation_decision"), None)
        return CognitiveRunResult(task, plan, tuple(state.get("memory_context", [])), verification, adaptation, state.get("plan_review"))

    def _load_plan(self, task: Task) -> ExecutionPlan | None:
        state = task.metadata.get(self.PLAN_METADATA_KEY, {})
        return self._deserialize_plan(state["plan"]) if isinstance(state.get("plan"), dict) else None

    @staticmethod
    def _attach_memory_context(plan: ExecutionPlan, memories: Sequence[dict[str, Any]]) -> ExecutionPlan:
        """Attach retrieved, namespace-scoped context as data, never as executable instructions."""
        if not memories:
            return plan
        safe_context = [{
            "memory_id": item.get("memory_id"),
            "memory_type": item.get("memory_type"),
            "content": item.get("content"),
            "relevance_score": item.get("relevance_score"),
        } for item in memories]
        steps = tuple(PlannedStep(
            step_id=step.step_id, name=step.name, handler_key=step.handler_key,
            inputs={**step.inputs, "elyntro_context_memories": safe_context},
            depends_on=step.depends_on, requires_approval=step.requires_approval,
            rationale=step.rationale,
        ) for step in plan.steps)
        return ExecutionPlan(plan.goal, steps, plan.requires_approval, plan.planner_name, plan.rationale)

    @staticmethod
    def _serialize_step(step: PlannedStep) -> dict[str, Any]:
        return {
            "step_id": step.step_id, "name": step.name, "handler_key": step.handler_key,
            "inputs": step.inputs, "depends_on": list(step.depends_on),
            "requires_approval": step.requires_approval, "rationale": step.rationale,
        }

    @classmethod
    def _serialize_plan(cls, plan: ExecutionPlan) -> dict[str, Any]:
        return {"goal": plan.goal, "requires_approval": plan.requires_approval,
                "planner_name": plan.planner_name, "rationale": list(plan.rationale),
                "steps": [cls._serialize_step(step) for step in plan.steps]}

    @staticmethod
    def _deserialize_plan(raw: dict[str, Any]) -> ExecutionPlan:
        steps = tuple(PlannedStep(
            step_id=item["step_id"], name=item["name"], handler_key=item["handler_key"],
            inputs=item["inputs"], depends_on=tuple(item.get("depends_on", [])),
            requires_approval=bool(item.get("requires_approval", False)),
            rationale=item.get("rationale", ""),
        ) for item in raw["steps"])
        return ExecutionPlan(raw["goal"], steps, bool(raw.get("requires_approval", False)),
                             raw.get("planner_name", "persisted-plan"), tuple(raw.get("rationale", [])))
