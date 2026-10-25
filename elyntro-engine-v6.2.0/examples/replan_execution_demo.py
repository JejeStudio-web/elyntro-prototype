"""Demonstrate preserving a completed step output while running a revised plan."""
from tempfile import TemporaryDirectory
from pathlib import Path

from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.planner import ExecutionPlan, PlannedStep, ToolDefinition
from elyntro.replan_execution import ReplannedWorkflowAdapter


def main():
    with TemporaryDirectory() as tmp:
        store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "demo-v1", lambda result, ctx: {
            "passed": True, "verifier_id": "demo-v1"})
        workflow = DurableWorkflowService(store, verifiers, {
            "collect": lambda data: {"fact": "persisted evidence"},
            "summarize": lambda data: {"summary": f"Summary of {data['fact']}"},
        })
        original_id = store.create_job(idempotency_key="demo-original", task_type="durable_workflow",
            payload={"workflow_id": "original", "inputs": {}, "metadata": {}}, steps=[
                JobStep("collect", "collect", {}, idempotent=True),
                JobStep("summarize", "summarize", {"fact": {"$ref": "collect.output.fact"}}),
            ])
        store.claim_next(worker_id="demo", job_id=original_id)
        store.start_step(original_id, "collect", worker_id="demo")
        store.finish_step(original_id, "collect", output={"fact": "persisted evidence"})
        store.start_step(original_id, "summarize", worker_id="demo")
        store.fail_step(original_id, "summarize", error="simulated failure")

        tools = [ToolDefinition("collect", "Collect evidence", ("research",)),
                 ToolDefinition("summarize", "Summarize evidence", ("analyze",))]
        plan = ExecutionPlan("summarize evidence", (
            PlannedStep("collect", "Collect evidence", "collect", {}),
            PlannedStep("summarize", "Summarize evidence", "summarize",
                        {"fact": {"$ref": "collect.output.fact"}}, ("collect",)),
        ), planner_name="demo-replan")
        adapter = ReplannedWorkflowAdapter(store, workflow)
        new_id = adapter.submit(original_id, plan, tools=tools, idempotency_key="demo-replanned")
        result = workflow.process_one(job_id=new_id)
        print({"original_job": original_id, "replanned_job": new_id,
               "state": result["state"], "executed_step_ids": [s["step_id"] for s in result["steps"]],
               "result": result["result"]})


if __name__ == "__main__":
    main()
