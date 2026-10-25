"""Runnable executor demo: python -m elyntro.cli"""
import json
from .contracts import TaskRequest
from .engine import Engine
from .store import SQLiteStore
from .task_executor import StepResult, TaskExecutor, TaskStep


def main() -> None:
    engine = Engine(SQLiteStore("elyntro.db"))
    task = engine.submit(TaskRequest(
        goal="Demonstrate the Elyntro task executor",
        constraints=["Do not call external services"],
        success_criteria=["A demo artifact is created"],
    ))

    def create_demo_artifact(inputs, context):
        content = "Elyntro task executor completed this local demonstration."
        return StepResult(
            output={"message": content},
            artifacts=[{"name": "demo-result.json", "content": {"message": content}, "media_type": "application/json"}],
            evidence=[{
                "criterion": "A demo artifact is created",
                "detail": "The registered local handler returned the artifact payload.",
            }],
        )

    executor = TaskExecutor(engine, handlers={"create_demo_artifact": create_demo_artifact})
    completed = executor.execute(task.task_id, [TaskStep(
        step_id="create-demo",
        name="Create demonstration artifact",
        handler_key="create_demo_artifact",
    )])
    print(json.dumps(completed.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
