"""Plan a goal with CognitiveLoop's planner, then execute each step durably."""
from pathlib import Path
from tempfile import TemporaryDirectory
from elyntro import (CognitiveLoop, CognitiveWorkflowService, Engine, RuleBasedPlanner,
                     SQLiteStore, TaskExecutor, TaskRequest, ToolDefinition)
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import PersistentJobStore, TaskVerifierRegistry


def main():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        tools = [ToolDefinition("research", "Gather evidence", ("research",)),
                 ToolDefinition("writer", "Create a draft", ("create",)),
                 ToolDefinition("checker", "Check output", ("verify",))]
        handlers = {
            "research": lambda data: {"evidence": f"Evidence for: {data['goal']}"},
            "writer": lambda data: {"draft": f"Draft based on {data['_elyntro_dependency_outputs']['step-1-research']['evidence']}"},
            "checker": lambda data: {"checked": bool(data.get("success_criteria"))},
        }
        engine = Engine(SQLiteStore(root / "tasks.db"))
        loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, TaskExecutor(engine, handlers))
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "demo-criteria-v1", lambda result, ctx: {
            "passed": len(result.get("outputs", {})) == 3 and bool(ctx.get("metadata", {}).get("success_criteria")),
            "verifier_id": "demo-criteria-v1",
        })
        durable = DurableWorkflowService(PersistentJobStore(root / "jobs.db"), verifiers, handlers)
        service = CognitiveWorkflowService(loop, durable)
        job_id = service.submit(TaskRequest(goal="Create a report", success_criteria=["draft exists"], idempotency_key="demo-v25"))
        job = durable.process_one(job_id=job_id)
        print({"job_id": job_id, "state": job["state"], "steps": [s["state"] for s in job["steps"]], "verification": job["verification"]})


if __name__ == "__main__":
    main()
