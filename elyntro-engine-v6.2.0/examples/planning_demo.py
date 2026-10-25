"""Offline end-to-end demo: goal -> plan -> allowlisted execution -> verification."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro import (
    Engine, SQLiteStore, TaskRequest, ToolDefinition, RuleBasedPlanner,
    PlanningService, TaskExecutor, StepResult,
)


def research(inputs, context):
    context.checkpoint()
    return StepResult(output={"topic": inputs["goal"], "sources_found": 3})


def create_report(inputs, context):
    context.checkpoint()
    return StepResult(output={"report": "Demo report", "constraints": inputs["constraints"]})


def verify(inputs, context):
    context.checkpoint()
    return StepResult(
        output={"verified": True},
        evidence=[{
            "criterion": criterion,
            "detail": "Demo verifier checked the generated report structure.",
        } for criterion in inputs["success_criteria"]],
    )


def main():
    with TemporaryDirectory() as directory:
        engine = Engine(SQLiteStore(str(Path(directory) / "elyntro.db")))
        task = engine.submit(TaskRequest(
            goal="Create a report about renewable energy",
            constraints=["Keep the report concise"],
            success_criteria=["report prepared"],
        ))
        tools = [
            ToolDefinition("researcher", "Gather background information", ("research",)),
            ToolDefinition("writer", "Draft the report", ("create",)),
            ToolDefinition("checker", "Verify the report", ("verify",)),
        ]
        plan = PlanningService(engine, RuleBasedPlanner(), tools).create_plan(task.task_id)
        print("Generated plan:")
        for step in plan.steps:
            print(f"- {step.step_id}: {step.name} (depends on: {', '.join(step.depends_on) or 'none'})")
        if plan.requires_approval:
            print("Approval required; refusing automatic execution.")
            return
        executor = TaskExecutor(engine, {
            "researcher": research,
            "writer": create_report,
            "checker": verify,
        })
        result = executor.execute(task.task_id, plan.to_task_steps(tools))
        print(f"Final task status: {result.status.value}")
        print(f"Recorded evidence items: {len(result.evidence)}")


if __name__ == "__main__":
    main()
