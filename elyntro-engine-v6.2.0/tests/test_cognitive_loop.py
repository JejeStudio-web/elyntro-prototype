import tempfile
import unittest
from pathlib import Path

from elyntro import (
    CognitiveLoop, Engine, EvidenceVerifier, MemoryStore, RuleBasedPlanner,
    SQLiteStore, StepResult, TaskExecutor, TaskRequest, TaskStatus, ToolDefinition,
)


class CognitiveLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "tasks.db")
        self.engine = Engine(self.store)
        self.memory = MemoryStore(Path(self.tmp.name) / "memory.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_goal_to_verified_result_with_memory_context(self):
        self.memory.add(namespace="project-a", content="Create report using metric units", tags=["report"])
        seen = []
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create report", ("create",)),
            ToolDefinition("checker", "Check report", ("verify",)),
        ]
        def handler(inputs, context):
            seen.append((context.step_id, inputs.get("elyntro_context_memories", [])))
            criterion = inputs.get("success_criteria", ["report produced"])[0]
            return StepResult(output={"done": True}, evidence=[{"criterion": criterion, "detail": "handler returned structured result"}])
        executor = TaskExecutor(self.engine, {"researcher": handler, "writer": handler, "checker": handler})
        verifier = EvidenceVerifier()
        verifier.register("report produced", lambda criterion, evidence, context: bool(context.get("artifacts")))
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor, memory=self.memory, verifier=verifier)
        result = loop.run(TaskRequest(goal="Create a report", success_criteria=["report produced"]), namespace="project-a")
        self.assertEqual(result.task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(len(result.plan.steps), 3)
        self.assertTrue(any(memories for _, memories in seen))
        event_types = [event["event_type"] for event in self.store.list_events(result.task.task_id)]
        self.assertIn("cognitive_plan_committed", event_types)
        self.assertIn("independent_verification", event_types) if result.verification else self.assertIn("evidence_recorded", event_types)

    def test_approval_required_plan_pauses_then_approved_plan_runs(self):
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create report", ("create",), requires_approval=True),
            ToolDefinition("checker", "Check report", ("verify",)),
        ]
        def handler(inputs, context):
            return StepResult(output="done", evidence=[{"criterion": "report produced", "detail": "completed"}])
        def research_handler(inputs, context):
            return StepResult(output={"prepared": True})
        executor = TaskExecutor(self.engine, {"researcher": handler, "writer": handler, "checker": handler})
        verifier = EvidenceVerifier()
        verifier.register("report produced", lambda criterion, evidence, context: True)
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor, verifier=verifier)
        first = loop.run(TaskRequest(goal="Create a report", success_criteria=["report produced"]))
        self.assertEqual(first.task.status, TaskStatus.NEEDS_APPROVAL)
        self.assertTrue(first.awaiting_approval)
        final = loop.approve(first.task.task_id)
        self.assertEqual(final.task.status, TaskStatus.SUCCEEDED)
        events = [event["event_type"] for event in self.store.list_events(first.task.task_id)]
        self.assertIn("approval_granted", events)
        self.assertIn("approved_execution_started", events)

    def test_low_review_score_can_require_approval(self):
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create report", ("create",)),
            ToolDefinition("checker", "Check report", ("verify",)),
        ]
        def handler(inputs, context):
            return StepResult(output="done", evidence=[{"criterion": "report produced", "detail": "completed"}])
        executor = TaskExecutor(self.engine, {tool.key: handler for tool in tools})
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor, review_min_score=100)
        result = loop.run(TaskRequest(goal="Create a report", success_criteria=["include source citations"]))
        self.assertEqual(result.task.status, TaskStatus.NEEDS_APPROVAL)
        self.assertIsNotNone(result.plan_review)
        self.assertTrue(result.plan_review["score"] < 100)
        self.assertTrue(any(event["event_type"] == "approval_requested"
                            for event in self.store.list_events(result.task.task_id)))

    def test_default_cognitive_loop_does_not_trust_handler_claim_as_proof(self):
        tools = [
            ToolDefinition("researcher", "Prepare task context", ("research",)),
            ToolDefinition("analyzer", "Analyze task", ("analyze",)),
            ToolDefinition("checker", "Check task", ("verify",)),
        ]
        def handler(inputs, context):
            criterion = inputs.get("success_criteria", ["result is correct"])[0]
            return StepResult(output={"claimed_success": True}, evidence=[
                {"criterion": criterion, "detail": "handler claims success without independent proof"}
            ])
        def research_handler(inputs, context):
            return StepResult(output={"prepared": True})
        executor = TaskExecutor(self.engine, {"researcher": research_handler, "analyzer": handler, "checker": handler})
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor)
        result = loop.run(TaskRequest(goal="Analyze this task", success_criteria=["result is correct"]))
        self.assertEqual(result.task.status, TaskStatus.NEEDS_APPROVAL)
        self.assertEqual(result.verification["status"], "inconclusive")
        self.assertEqual(result.verification["checks"][0]["method"], "no-validator")

    def test_default_cognitive_loop_accepts_independently_verified_result(self):
        tools = [
            ToolDefinition("researcher", "Prepare task context", ("research",)),
            ToolDefinition("analyzer", "Analyze task", ("analyze",)),
            ToolDefinition("checker", "Check task", ("verify",)),
        ]
        def handler(inputs, context):
            criterion = inputs.get("success_criteria", ["result is correct"])[0]
            return StepResult(output={"answer": 42}, evidence=[
                {"criterion": criterion, "detail": "structured result produced"}
            ], artifacts=[{"name": "answer.json", "content": {"answer": 42}, "media_type": "application/json"}])
        def research_handler(inputs, context):
            return StepResult(output={"prepared": True})
        executor = TaskExecutor(self.engine, {"researcher": research_handler, "analyzer": handler, "checker": handler})
        verifier = EvidenceVerifier()
        verifier.register("result is correct", lambda criterion, evidence, context: any(
            artifact.get("name") == "answer.json" and artifact.get("content", {}).get("answer") == 42
            for artifact in context.get("artifacts", [])
        ))
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor, verifier=verifier)
        result = loop.run(TaskRequest(goal="Analyze this task", success_criteria=["result is correct"]))
        self.assertEqual(result.task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(result.verification["status"], "verified")

    def test_unregistered_handler_fails_before_accepting_loop(self):
        tools = [ToolDefinition("researcher", "Gather source material", ("research",))]
        executor = TaskExecutor(self.engine, {})
        with self.assertRaisesRegex(ValueError, "registered executor handlers"):
            CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor)

    def test_memory_is_namespace_scoped(self):
        self.memory.add(namespace="private-a", content="Use metric units in reports")
        tools = [
            ToolDefinition("researcher", "Gather source material", ("research",)),
            ToolDefinition("writer", "Create report", ("create",)),
            ToolDefinition("checker", "Check report", ("verify",)),
        ]
        executor = TaskExecutor(self.engine, {tool.key: (lambda inputs, context: StepResult(output="ok", evidence=[{"criterion":"done", "detail":"ok"}])) for tool in tools})
        loop = CognitiveLoop(self.engine, RuleBasedPlanner(), tools, executor, memory=self.memory)
        result = loop.run(TaskRequest(goal="Create a report", success_criteria=["done"]), namespace="private-b")
        self.assertEqual(result.relevant_memories, ())


if __name__ == "__main__":
    unittest.main()
