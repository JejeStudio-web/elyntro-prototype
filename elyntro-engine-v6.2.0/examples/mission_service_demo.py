"""End-to-end MissionService demo with local deterministic handlers."""
import tempfile
from pathlib import Path

from elyntro import Engine, MissionService, SQLiteStore, StepResult, TaskRequest, ToolDefinition


def handler(stage):
    def run(inputs, context):
        criterion = inputs['success_criteria'][0]
        return StepResult(
            output={'stage': stage, 'goal': inputs['goal']},
            evidence=[{'criterion': criterion, 'detail': f'{stage} produced a reviewable result'}],
        )
    return run


with tempfile.TemporaryDirectory() as directory:
    engine = Engine(SQLiteStore(Path(directory) / 'mission.db'))
    tools = [
        ToolDefinition('researcher', 'Collect evidence', ('research',)),
        ToolDefinition('analyst', 'Analyze evidence', ('analyze',)),
        ToolDefinition('checker', 'Check deliverable', ('verify',)),
    ]
    service = MissionService(engine, tools, {tool.key: handler(tool.key) for tool in tools})
    prepared = service.prepare(TaskRequest(
        goal='Research and analyze a topic, then verify the result',
        success_criteria=['reviewable-result'],
    ))
    result = service.execute(prepared)
    print({'task_id': result.task_id, 'status': result.status.value,
           'steps': len(prepared.plan.steps), 'artifacts': len(result.artifacts),
           'evidence': len(result.evidence)})
