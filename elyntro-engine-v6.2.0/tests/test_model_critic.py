import json
import unittest
from types import SimpleNamespace

from elyntro.model_critic import ModelPlanCritic
from elyntro.plan_review import PlanQualityReviewer
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition


class FakeGateway:
    def __init__(self, text):
        self.text = text
        self.request = None
    def complete(self, request):
        self.request = request
        return SimpleNamespace(model_id="critic-model", provider="test", text=self.text,
                               input_tokens=200, output_tokens=100, latency_ms=15, estimated_cost=0.002)


class ModelCriticTests(unittest.TestCase):
    def setUp(self):
        self.tools = [
            ToolDefinition("writer", "Create report", ("create",)),
            ToolDefinition("checker", "Verify report", ("verify",)),
        ]
        self.plan = ExecutionPlan("Create report", (
            PlannedStep("s1", "Write report", "writer", {}, (), False, "Write the requested report."),
        ))

    def test_critic_returns_revision_and_sanitized_findings(self):
        response = {"critique": [{"severity": "warning", "category": "coverage",
                                  "finding": "No independent verification step", "affected_step_ids": ["s1"]}],
                    "steps": [{"step_id": "s1", "name": "Write report", "handler_key": "writer",
                               "inputs": {}, "depends_on": [], "rationale": "Write report"},
                              {"step_id": "s2", "name": "Verify report", "handler_key": "checker",
                               "inputs": {}, "depends_on": ["s1"], "rationale": "Verify the report"}]}
        gateway = FakeGateway(json.dumps(response))
        critic = ModelPlanCritic(gateway)
        proposed = critic.revise(self.plan.goal, self.plan, [], [], ["verify report"], self.tools)
        self.assertEqual(len(proposed), 2)
        self.assertEqual(critic.last_critique["findings"][0]["category"], "coverage")
        self.assertEqual(gateway.request.task_type, "plan_critique")
        self.assertIn("review_questions", json.loads(gateway.request.messages[1]["content"]))

    def test_critic_output_is_still_validated_and_only_better_plan_selected(self):
        response = {"critique": [{"severity": "warning", "category": "missing_check",
                                  "finding": "Add verification", "affected_step_ids": ["s1"]}],
                    "steps": [{"step_id": "s1", "name": "Write report", "handler_key": "writer",
                               "inputs": {}, "depends_on": [], "rationale": "Write report"},
                              {"step_id": "s2", "name": "Verify report", "handler_key": "checker",
                               "inputs": {}, "depends_on": ["s1"], "rationale": "Verify report output"}]}
        critic = ModelPlanCritic(FakeGateway(json.dumps(response)))
        selected, report = PlanQualityReviewer().review_and_repair(
            self.plan, RuleBasedPlanner(), self.tools,
            success_criteria=["verify output independently"], backend=critic)
        self.assertEqual(report.selected_plan, "repaired")
        self.assertEqual(len(selected.steps), 2)

    def test_critic_cannot_invent_tools(self):
        response = {"critique": [], "steps": [{"step_id": "s1", "name": "Run shell",
                    "handler_key": "shell", "inputs": {}, "depends_on": [], "rationale": "Run"}]}
        critic = ModelPlanCritic(FakeGateway(json.dumps(response)))
        with self.assertRaisesRegex(ValueError, "unregistered tool"):
            PlanQualityReviewer().review_and_repair(self.plan, RuleBasedPlanner(), self.tools, backend=critic)

    def test_invalid_json_fails_closed(self):
        critic = ModelPlanCritic(FakeGateway("not json"))
        with self.assertRaisesRegex(ValueError, "invalid JSON"):
            critic.revise(self.plan.goal, self.plan, [], [], [], self.tools)


if __name__ == "__main__":
    unittest.main()
