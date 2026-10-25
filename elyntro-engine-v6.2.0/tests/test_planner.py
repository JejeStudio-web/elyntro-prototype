import tempfile
import unittest
from pathlib import Path

from elyntro import (
    Engine, SQLiteStore, TaskRequest, ToolDefinition, RuleBasedPlanner,
    PlanningService, TaskExecutor, StepResult,
)


class FakeBackend:
    name = "fake-model-backend"
    def __init__(self, proposal):
        self.proposal = proposal
    def propose(self, goal, constraints, success_criteria, tools):
        return self.proposal


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "elyntro.db")
        self.engine = Engine(SQLiteStore(self.db))
        self.tools = [
            ToolDefinition("researcher", "Search trusted sources", ("research",), {"source_limit": 5}),
            ToolDefinition("analyst", "Compare evidence and options", ("analyze",)),
            ToolDefinition("writer", "Create a structured report", ("create",)),
            ToolDefinition("checker", "Check output against criteria", ("verify",)),
            ToolDefinition("publisher", "Publish an approved deliverable", ("execute",), requires_approval=True),
        ]

    def tearDown(self):
        self.temp.cleanup()

    def test_create_report_plan_has_ordered_stages_and_context(self):
        task = self.engine.submit(TaskRequest(
            goal="Research and create a report about renewable energy",
            constraints=["Use reliable sources"], success_criteria=["report includes citations"],
        ))
        service = PlanningService(self.engine, RuleBasedPlanner(), self.tools)
        plan = service.create_plan(task.task_id)
        self.assertEqual([s.handler_key for s in plan.steps], ["researcher", "writer", "checker"])
        self.assertEqual(plan.steps[1].depends_on, (plan.steps[0].step_id,))
        self.assertEqual(plan.steps[0].inputs["goal"], task.goal)
        self.assertEqual(plan.steps[0].inputs["constraints"], task.constraints)
        self.assertFalse(plan.requires_approval)
        events = self.engine.store.list_events(task.task_id)
        self.assertTrue(any(e["event_type"] == "intelligent_plan_created" for e in events))

    def test_sensitive_execution_tool_requires_approval(self):
        task = self.engine.submit(TaskRequest(goal="Publish the report"))
        service = PlanningService(self.engine, RuleBasedPlanner(), self.tools)
        plan = service.create_plan(task.task_id)
        self.assertTrue(plan.requires_approval)
        self.assertTrue(any(step.handler_key == "publisher" and step.requires_approval for step in plan.steps))
        # Planning only records a proposal; it never executes a side effect.
        self.assertEqual(self.engine.store.get_task(task.task_id).status.value, "created")

    def test_fails_closed_if_required_capability_missing(self):
        task = self.engine.submit(TaskRequest(goal="Research this topic"))
        with self.assertRaisesRegex(ValueError, "required capability"):
            RuleBasedPlanner().plan(task.goal, [], [], [self.tools[1]])

    def test_rejects_unknown_handler_from_model_backend(self):
        backend = FakeBackend([{"step_id": "s1", "name": "Run", "handler_key": "shell", "inputs": {}}])
        with self.assertRaisesRegex(ValueError, "unregistered tool"):
            RuleBasedPlanner(backend).plan("Do work", [], [], self.tools)

    def test_rejects_dependency_cycle_from_model_backend(self):
        backend = FakeBackend([
            {"step_id": "a", "name": "A", "handler_key": "researcher", "depends_on": ["b"]},
            {"step_id": "b", "name": "B", "handler_key": "checker", "depends_on": ["a"]},
        ])
        with self.assertRaisesRegex(ValueError, "cycle"):
            RuleBasedPlanner(backend).plan("Do work", [], [], self.tools)

    def test_rejects_unknown_dependency(self):
        backend = FakeBackend([{"step_id": "a", "name": "A", "handler_key": "researcher", "depends_on": ["missing"]}])
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            RuleBasedPlanner(backend).plan("Do work", [], [], self.tools)

    def test_duplicate_tool_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            PlanningService(self.engine, RuleBasedPlanner(), [self.tools[0], self.tools[0]])

    def test_planned_steps_convert_to_executor_steps(self):
        plan = RuleBasedPlanner().plan("Create a report", [], [], self.tools)
        steps = plan.to_task_steps(self.tools)
        self.assertEqual([s.handler_key for s in steps], ["researcher", "writer", "checker"])
        self.assertGreater(steps[0].timeout_seconds, 0)


if __name__ == "__main__":
    unittest.main()
