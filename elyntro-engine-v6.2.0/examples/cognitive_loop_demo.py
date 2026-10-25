"""Run a complete local cognitive-loop example with deterministic handlers."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro import (
    CognitiveLoop, Engine, MemoryStore, RuleBasedPlanner, SQLiteStore,
    StepResult, TaskExecutor, TaskRequest, ToolDefinition,
)


def main() -> None:
    with TemporaryDirectory(prefix="elyntro-v10-") as folder:
        root = Path(folder)
        engine = Engine(SQLiteStore(root / "tasks.db"))
        memory = MemoryStore(root / "memory.db")
        memory.add(namespace="demo", content="The report should use concise language", tags=["report", "style"])

        def capability(inputs, context):
            context.checkpoint()
            criteria = inputs.get("success_criteria") or ["report generated"]
            return StepResult(
                output={"step": context.step_id, "status": "demo-only"},
                evidence=[{"criterion": criteria[0], "detail": "Demo handler returned structured evidence."}],
            )

        tools = [
            ToolDefinition("research", "Gather information", ("research",)),
            ToolDefinition("create", "Create a report", ("create",)),
            ToolDefinition("verify", "Check report output", ("verify",)),
        ]
        executor = TaskExecutor(engine, {tool.key: capability for tool in tools})
        loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, executor, memory=memory)
        result = loop.run(TaskRequest(
            goal="Create a report",
            success_criteria=["report generated"],
        ), namespace="demo")
        print("Task:", result.task.task_id)
        print("Status:", result.task.status.value)
        print("Planned steps:", len(result.plan.steps) if result.plan else 0)
        print("Relevant memories:", len(result.relevant_memories))
        print("Artifacts:", len(result.task.artifacts))
        print("Note: handlers are simulated; this is an orchestration demo, not a real research system.")


if __name__ == "__main__":
    main()
