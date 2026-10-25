"""Integrated mission lifecycle: plan, approval gate, execution, and verification.

This facade composes existing Elyntro components. It does not execute arbitrary
plan text: every step must map to a registered ToolDefinition and handler.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import Task, TaskRequest, TaskStatus
from .engine import Engine
from .execution_control import CancellationToken
from .planner import ExecutionPlan, PlanningService, RuleBasedPlanner, ToolDefinition
from .task_executor import Handler, TaskExecutor, TaskStep


@dataclass(frozen=True)
class MissionPreparation:
    task: Task
    plan: ExecutionPlan

    @property
    def requires_approval(self) -> bool:
        return self.plan.requires_approval

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "requires_approval": self.requires_approval,
            "plan": {
                "planner": self.plan.planner_name,
                "rationale": list(self.plan.rationale),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "name": step.name,
                        "handler_key": step.handler_key,
                        "depends_on": list(step.depends_on),
                        "requires_approval": step.requires_approval,
                    }
                    for step in self.plan.steps
                ],
            },
        }


class MissionService:
    """End-to-end local orchestration with an explicit human approval gate.

    ``prepare`` persists a task and plan but never runs handlers. If any selected
    tool requires approval, execution remains blocked until ``approve`` is called.
    ``execute`` uses the registered TaskExecutor and its evidence verification.
    """

    def __init__(
        self,
        engine: Engine,
        tools: list[ToolDefinition],
        handlers: dict[str, Handler],
        *,
        planner: RuleBasedPlanner | None = None,
        verifier: Any = None,
    ) -> None:
        if not tools:
            raise ValueError("at least one tool must be registered")
        self.engine = engine
        self.tools = tuple(tools)
        self.catalog = {tool.key: tool for tool in tools}
        if len(self.catalog) != len(self.tools):
            raise ValueError("tool keys must be unique")
        missing = sorted(set(self.catalog) - set(handlers))
        if missing:
            raise ValueError("missing handlers for registered tools: " + ", ".join(missing))
        unknown = sorted(set(handlers) - set(self.catalog))
        if unknown:
            raise ValueError("handlers have no matching tool definitions: " + ", ".join(unknown))
        self.planning = PlanningService(engine, planner or RuleBasedPlanner(), self.tools)
        self.executor = TaskExecutor(engine, handlers, verifier=verifier)

    def prepare(self, request: TaskRequest, *, context: dict[str, Any] | None = None) -> MissionPreparation:
        task = self.engine.submit(request)
        if task.status != TaskStatus.CREATED:
            raise ValueError("mission preparation requires a newly created task")
        self.engine.transition(task.task_id, TaskStatus.VALIDATING, "mission request accepted")
        self.engine.transition(task.task_id, TaskStatus.PLANNING, "creating a registered-tool execution plan")
        try:
            plan = self.planning.create_plan(task.task_id, context=context)
            task_steps = plan.to_task_steps(self.tools)
            self.engine.set_plan(task.task_id, [step.name for step in task_steps])
            self.engine.store.add_event(task.task_id, "mission_prepared", {
                "planner": plan.planner_name,
                "step_count": len(task_steps),
                "requires_approval": plan.requires_approval,
            })
            if plan.requires_approval:
                self.engine.transition(task.task_id, TaskStatus.NEEDS_APPROVAL,
                                       "one or more planned tools require explicit approval")
            else:
                self.engine.transition(task.task_id, TaskStatus.READY, "plan prepared; no approval-required tools selected")
            return MissionPreparation(self.engine.store.get_task(task.task_id), plan)
        except Exception as exc:
            latest = self.engine.store.get_task(task.task_id)
            if latest.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUCCEEDED}:
                self.engine.fail(task.task_id, f"Mission preparation failed: {exc}")
            raise

    def approve(self, task_id: str, *, approver: str, reason: str = "approved by authorized caller") -> Task:
        if not isinstance(approver, str) or not approver.strip():
            raise ValueError("approver must be a non-empty identifier")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("approval reason must be non-empty")
        task = self.engine.store.get_task(task_id)
        if task.status != TaskStatus.NEEDS_APPROVAL:
            raise ValueError("only missions awaiting approval can be approved")
        self.engine.store.add_event(task_id, "mission_approved", {
            "approver": approver.strip()[:200], "reason": reason.strip()[:1000],
        })
        return self.engine.transition(task_id, TaskStatus.READY, "explicit approval recorded")

    def execute(
        self,
        preparation: MissionPreparation,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> Task:
        if not isinstance(preparation, MissionPreparation):
            raise TypeError("execute requires the MissionPreparation returned by prepare")
        task_id = preparation.task.task_id
        task = self.engine.store.get_task(task_id)
        if task.status == TaskStatus.NEEDS_APPROVAL:
            raise PermissionError("mission is blocked until approve(task_id, approver=...) is called")
        if task.status != TaskStatus.READY:
            raise ValueError(f"mission must be ready to execute; current status is {task.status.value}")
        steps = preparation.plan.to_task_steps(self.tools)
        self.engine.store.add_event(task_id, "mission_execution_started", {
            "step_count": len(steps), "planner": preparation.plan.planner_name,
        })
        result = self.executor.execute(task_id, steps, cancellation_token=cancellation_token)
        self.engine.store.add_event(task_id, "mission_execution_finished", {
            "status": result.status.value,
            "artifact_count": len(result.artifacts),
            "evidence_count": len(result.evidence),
            "error_count": len(result.errors),
        })
        return result
