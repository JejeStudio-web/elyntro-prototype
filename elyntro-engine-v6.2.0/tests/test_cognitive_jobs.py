import tempfile
import unittest
from pathlib import Path

from elyntro import (
    CognitiveJobService, CognitiveLoop, Engine, EvidenceVerifier, PersistentJobStore, RuleBasedPlanner,
    SQLiteStore, StepResult, TaskExecutor, TaskRequest, TaskStatus, TaskVerifierRegistry,
    ToolDefinition,
)


class CognitiveJobServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        engine = Engine(SQLiteStore(root / "tasks.db"))
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create output", ("create",)),
            ToolDefinition("checker", "Check output", ("verify",)),
        ]
        def handler(inputs, context):
            criterion = inputs.get("success_criteria", ["result produced"])[0]
            return StepResult(output={"ok": True}, evidence=[{"criterion": criterion, "detail": "handler completed"}])
        executor = TaskExecutor(engine, {tool.key: handler for tool in tools})
        task_verifier = EvidenceVerifier()
        task_verifier.register("result produced", lambda criterion, evidence, context: True)
        self.loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, executor, verifier=task_verifier)
        self.store = PersistentJobStore(root / "jobs.db")
        self.verifiers = TaskVerifierRegistry()
        self.verifiers.register("cognitive_goal", "task-status-check-v1",
            lambda result, context: {"passed": result.get("task", {}).get("status") == "succeeded", "evidence": ["task status checked"]})
        self.service = CognitiveJobService(self.store, self.loop, self.verifiers)

    def tearDown(self):
        self.tmp.cleanup()

    def test_goal_runs_through_persistent_queue_and_verifies(self):
        job_id = self.service.submit(TaskRequest(goal="Create a report", success_criteria=["result produced"], idempotency_key="goal-1"))
        job = self.service.process_one()
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(job["verification"]["verifier_id"], "task-status-check-v1")
        self.assertEqual(job["steps"][0]["state"], "succeeded")
        self.assertEqual(job["result"]["task"]["status"], TaskStatus.SUCCEEDED.value)

    def test_approval_pauses_job_and_resumes_persisted_plan(self):
        # Force the plan to require approval by marking a registered tool accordingly.
        engine = self.loop.engine
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create output", ("create",), requires_approval=True),
            ToolDefinition("checker", "Check output", ("verify",)),
        ]
        def handler(inputs, context):
            criterion = inputs.get("success_criteria", ["result produced"])[0]
            return StepResult(output={"ok": True}, evidence=[{"criterion": criterion, "detail": "handler completed"}])
        task_verifier = EvidenceVerifier()
        task_verifier.register("result produced", lambda criterion, evidence, context: True)
        loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, TaskExecutor(engine, {t.key: handler for t in tools}), verifier=task_verifier)
        service = CognitiveJobService(self.store, loop, self.verifiers)
        job_id = service.submit(TaskRequest(goal="Create a report", success_criteria=["result produced"], idempotency_key="goal-approval"))
        paused = service.process_one()
        self.assertEqual(paused["state"], "waiting_approval")
        task_id = paused["steps"][0]["output"]["cognitive_task_id"]
        finished = service.approve(job_id, approved_by="user")
        self.assertEqual(finished["state"], "succeeded")
        self.assertEqual(finished["result"]["task"]["task_id"], task_id)

    def test_missing_verifier_fails_closed(self):
        empty = TaskVerifierRegistry()
        service = CognitiveJobService(self.store, self.loop, empty)
        job_id = service.submit(TaskRequest(goal="Create a report", success_criteria=["result produced"], idempotency_key="no-verifier"))
        job = service.process_one()
        self.assertEqual(job["job_id"], job_id)
        self.assertEqual(job["state"], "failed")


if __name__ == "__main__":
    unittest.main()
