"""Safely translate an approved revised plan into a new durable workflow.

The original job is never mutated. Completed outputs are carried into the new
workflow as immutable input data, and completed steps are omitted from execution.
This is a local prototype: external side effects still need their own idempotency.
"""
from __future__ import annotations

from typing import Any, Sequence

from .durable_workflows import DurableWorkflowService
from .persistent_jobs import JobStep, PersistentJobStore
from .planner import ExecutionPlan, ToolDefinition


class ReplannedWorkflowAdapter:
    """Create a new durable job from a validated plan without replaying completed steps."""

    def __init__(self, store: PersistentJobStore, workflows: DurableWorkflowService) -> None:
        if workflows.store.path != store.path:
            raise ValueError("store and workflow service must use the same SQLite database")
        self.store, self.workflows = store, workflows

    @staticmethod
    def _rewrite_completed_refs(value: Any, completed: set[str]) -> Any:
        if isinstance(value, dict):
            if set(value) == {"$ref"} and isinstance(value["$ref"], str):
                ref = value["$ref"]
                parts = ref.split(".")
                if len(parts) >= 2 and parts[0] in completed and parts[1] == "output":
                    suffix = ".".join(parts[2:])
                    path = f"input._elyntro_completed_outputs.{parts[0]}"
                    if suffix:
                        path += f".{suffix}"
                    return {"$ref": path}
                return dict(value)
            return {k: ReplannedWorkflowAdapter._rewrite_completed_refs(v, completed) for k, v in value.items()}
        if isinstance(value, list):
            return [ReplannedWorkflowAdapter._rewrite_completed_refs(v, completed) for v in value]
        return value

    def submit(self, original_job_id: str, plan: ExecutionPlan, *, tools: Sequence[ToolDefinition],
               idempotency_key: str) -> str:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("a fresh non-empty idempotency_key is required")
        original = self.store.get_job(original_job_id)
        if original["state"] in {"running", "queued", "waiting_approval"}:
            raise ValueError("original job must be stopped or failed before replanning")
        completed_steps = {s["step_id"]: s for s in original["steps"] if s["state"] == "succeeded"}
        outputs = {sid: step["output"] for sid, step in completed_steps.items()}
        tool_map = {tool.key: tool for tool in tools}
        if len(tool_map) != len(tuple(tools)):
            raise ValueError("tool keys must be unique")
        plan_ids = [step.step_id for step in plan.steps]
        if len(set(plan_ids)) != len(plan_ids):
            raise ValueError("plan step IDs must be unique")
        # Preserve completed operations exactly; the revised plan cannot redefine them.
        old_by_id = {s["step_id"]: s for s in original["steps"]}
        new_by_id = {s.step_id: s for s in plan.steps}
        for sid, old in completed_steps.items():
            new = new_by_id.get(sid)
            if new is None or new.handler_key != old["handler_key"] or new.inputs != old["inputs"]:
                raise ValueError(f"revised plan must preserve completed step {sid!r} exactly")
        if not plan.steps:
            raise ValueError("revised plan must contain at least one step")

        remaining = []
        seen = set(completed_steps)
        for step in plan.steps:
            tool = tool_map.get(step.handler_key)
            if tool is None or step.handler_key not in self.workflows.handlers:
                raise ValueError(f"step {step.step_id!r} uses an unregistered tool/handler")
            if step.step_id in completed_steps:
                continue
            for dep in step.depends_on:
                if dep not in seen and dep not in plan_ids[:plan_ids.index(step.step_id)]:
                    raise ValueError(f"step {step.step_id!r} has an unresolved or forward dependency: {dep!r}")
            inputs = self._rewrite_completed_refs(step.inputs, set(completed_steps))
            # Keep original idempotency declaration when a step is reused; new steps
            # default to non-idempotent unless the application explicitly maps policy.
            old = old_by_id.get(step.step_id)
            idempotent = bool(old["idempotent"]) if old else False
            remaining.append(JobStep(step_id=step.step_id, handler_key=step.handler_key, inputs=inputs,
                                     idempotent=idempotent,
                                     requires_approval=bool(step.requires_approval or tool.requires_approval)))
            seen.add(step.step_id)
        if not remaining:
            raise ValueError("revised plan contains no unfinished steps to execute")
        payload = {
            "workflow_id": f"replan:{original_job_id}",
            "inputs": {"_elyntro_completed_outputs": outputs},
            "metadata": {"parent_job_id": original_job_id, "replanned": True,
                         "plan_goal": plan.goal, "planner_name": plan.planner_name,
                         "preserved_completed_steps": sorted(completed_steps)},
        }
        return self.store.create_job(idempotency_key=idempotency_key, task_type=self.workflows.TASK_TYPE,
                                     payload=payload, steps=remaining,
                                     require_approval=bool(plan.requires_approval))
