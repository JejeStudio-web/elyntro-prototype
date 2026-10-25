"""Run an integrated CognitiveLoop request through the durable job queue."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro import (
    CognitiveJobService, CognitiveLoop, Engine, PersistentJobStore,
    RuleBasedPlanner, SQLiteStore, StepResult, TaskExecutor, TaskRequest,
    TaskVerifierRegistry, ToolDefinition,
)

with TemporaryDirectory() as tmp:
    root = Path(tmp)
    engine = Engine(SQLiteStore(root / "tasks.db"))
    tools = [
        ToolDefinition("researcher", "Gather information", ("research",)),
        ToolDefinition("writer", "Create the result", ("create",)),
        ToolDefinition("checker", "Check the result", ("verify",)),
    ]

    def registered_handler(inputs, context):
        criterion = inputs.get("success_criteria", ["result prepared"])[0]
        return StepResult(
            output={"result": "Demo result"},
            evidence=[{"criterion": criterion, "detail": "Registered handler completed"}],
        )

    executor = TaskExecutor(engine, {tool.key: registered_handler for tool in tools})
    loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, executor)
    queue = PersistentJobStore(root / "jobs.db")
    validators = TaskVerifierRegistry()
    validators.register(
        "cognitive_goal", "task-status-check-v1",
        lambda result, context: {
            "passed": result.get("task", {}).get("status") == "succeeded",
            "evidence": ["CognitiveLoop terminal status checked"],
        },
    )
    service = CognitiveJobService(queue, loop, validators)
    job_id = service.submit(TaskRequest(
        goal="Create a short report", success_criteria=["result prepared"],
        idempotency_key="demo-report-001",
    ))
    completed = service.process_one(job_id=job_id)
    print({"job_id": completed["job_id"], "state": completed["state"],
           "task_status": completed["result"]["task"]["status"],
           "verifier": completed["verification"]["verifier_id"]})
