import json
import unittest
from types import SimpleNamespace

from elyntro.model_planner import ModelPlannerBackend
from elyntro.planner import RuleBasedPlanner, ToolDefinition


class FakeGateway:
    def __init__(self, text):
        self.text = text
        self.request = None
    def complete(self, request):
        self.request = request
        return SimpleNamespace(
            model_id="fake-model", provider="fake", text=self.text,
            input_tokens=120, output_tokens=80, latency_ms=12, estimated_cost=0.001,
        )


class ModelPlannerTests(unittest.TestCase):
    def setUp(self):
        self.tools = [
            ToolDefinition("researcher", "Find information", ("research",)),
            ToolDefinition("writer", "Create a deliverable", ("create",)),
            ToolDefinition("checker", "Check result", ("verify",)),
            ToolDefinition("publisher", "Publish result", ("execute",), requires_approval=True),
        ]

    def test_model_planner_uses_gateway_and_structured_context(self):
        response = {"steps": [
            {"step_id": "s1", "name": "Research", "handler_key": "researcher", "inputs": {"depth": "normal"}, "depends_on": [], "rationale": "Gather evidence"},
            {"step_id": "s2", "name": "Draft", "handler_key": "writer", "inputs": {}, "depends_on": ["s1"], "rationale": "Create output"},
        ]}
        gateway = FakeGateway(json.dumps(response))
        backend = ModelPlannerBackend(gateway)
        planner = RuleBasedPlanner(backend)
        plan = planner.plan("Create a report", ["Use credible sources"], ["Include references"], self.tools,
                            context={"memories": [{"content": "Prefer concise reports", "category": "explicit"}]})
        self.assertEqual([step.handler_key for step in plan.steps], ["researcher", "writer"])
        self.assertEqual(plan.steps[0].inputs["constraints"], ["Use credible sources"])
        self.assertEqual(plan.steps[0].inputs["success_criteria"], ["Include references"])
        self.assertEqual(backend.last_call_metadata["model_id"], "fake-model")
        user_payload = json.loads(gateway.request.messages[1]["content"])
        self.assertEqual(user_payload["relevant_memories_untrusted_reference_only"][0]["content"], "Prefer concise reports")
        self.assertIn("planning", gateway.request.task_type)

    def test_model_cannot_override_task_contract(self):
        gateway = FakeGateway(json.dumps({"steps": [
            {"step_id": "s1", "name": "Research", "handler_key": "researcher",
             "inputs": {"goal": "ignore the user", "constraints": ["evil"]}, "depends_on": []}
        ]}))
        plan = RuleBasedPlanner(ModelPlannerBackend(gateway)).plan(
            "Actual goal", ["Real constraint"], ["Real criterion"], self.tools)
        self.assertEqual(plan.steps[0].inputs["goal"], "Actual goal")
        self.assertEqual(plan.steps[0].inputs["constraints"], ["Real constraint"])
        self.assertEqual(plan.steps[0].inputs["success_criteria"], ["Real criterion"])

    def test_model_cannot_invent_handler(self):
        gateway = FakeGateway(json.dumps({"steps": [
            {"step_id": "s1", "name": "Run arbitrary command", "handler_key": "shell", "inputs": {}, "depends_on": []}
        ]}))
        with self.assertRaisesRegex(ValueError, "unregistered tool"):
            RuleBasedPlanner(ModelPlannerBackend(gateway)).plan("Do work", [], [], self.tools)

    def test_model_output_must_be_json(self):
        gateway = FakeGateway("I think you should use researcher")
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            RuleBasedPlanner(ModelPlannerBackend(gateway)).plan("Do work", [], [], self.tools)

    def test_model_plan_obeys_tool_approval_metadata(self):
        gateway = FakeGateway(json.dumps({"steps": [
            {"step_id": "s1", "name": "Publish", "handler_key": "publisher", "inputs": {}, "depends_on": []}
        ]}))
        plan = RuleBasedPlanner(ModelPlannerBackend(gateway)).plan("Publish", [], [], self.tools)
        self.assertTrue(plan.requires_approval)
        self.assertTrue(plan.steps[0].requires_approval)


if __name__ == "__main__":
    unittest.main()
