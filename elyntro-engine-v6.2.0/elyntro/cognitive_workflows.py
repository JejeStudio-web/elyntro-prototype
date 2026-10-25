"""Bridge cognitive planning into durable, stepwise execution.

Uses the configured planner and registered handlers, then persists each operation
in DurableWorkflowService. This is a local prototype, not a distributed runtime.
"""
from __future__ import annotations
from .contracts import TaskRequest
from .cognitive_loop import CognitiveLoop
from .durable_workflows import DurableWorkflowService
from .persistent_jobs import JobStep

class CognitiveWorkflowService:
    """Connect a CognitiveLoop planner/catalog to durable stepwise execution."""
    def __init__(self, loop: CognitiveLoop, workflows: DurableWorkflowService) -> None:
        self.loop, self.workflows = loop, workflows

    def submit(self, request: TaskRequest, *, namespace: str = "default", idempotency_key: str | None = None) -> str:
        request.validate()
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("namespace must be a non-empty string")
        key = idempotency_key or request.idempotency_key
        if not isinstance(key, str) or not key.strip():
            raise ValueError("idempotency_key is required")
        memories = self.loop.memory.retrieve(namespace.strip(), request.goal, limit=self.loop.memory_limit) if self.loop.memory else []
        plan = self.loop.planner.plan(request.goal, request.constraints, request.success_criteria,
                                      self.loop.tools, context={"namespace": namespace.strip(), "memories": memories})
        review = self.loop.reviewer.review(plan, self.loop.tools, request.constraints, request.success_criteria)
        review_gate = self.loop.review_min_score is not None and review.score < self.loop.review_min_score
        steps = []
        for step in plan.steps:
            tool = self.loop.catalog[step.handler_key]
            inputs = dict(step.inputs)
            if step.depends_on:
                inputs["_elyntro_dependency_outputs"] = {dep: {"$ref": f"{dep}.output"} for dep in step.depends_on}
            steps.append(JobStep(step_id=step.step_id, handler_key=step.handler_key, inputs=inputs,
                                 idempotent=bool(getattr(tool, "idempotent", False)),
                                 requires_approval=bool(step.requires_approval or review_gate)))
        metadata = {
            "goal": request.goal, "constraints": list(request.constraints),
            "success_criteria": list(request.success_criteria), "namespace": namespace.strip(),
            "plan": {"goal": plan.goal, "planner_name": plan.planner_name,
                     "rationale": list(plan.rationale),
                     "steps": [{"step_id": s.step_id, "name": s.name, "handler_key": s.handler_key,
                                "depends_on": list(s.depends_on), "rationale": s.rationale} for s in plan.steps]},
            "plan_review": review.to_dict(),
            "relevant_memory_ids": [m.get("memory_id") for m in memories],
        }
        return self.workflows.submit(workflow_id="cognitive-goal", inputs={"goal": request.goal},
                                     idempotency_key=key, steps=steps, metadata=metadata)
