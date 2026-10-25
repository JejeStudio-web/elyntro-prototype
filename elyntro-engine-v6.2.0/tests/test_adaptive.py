import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy, ExecutionFeedback
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition


class FakeBackend:
    name = "fake-adapter"

    def __init__(self, proposal=None):
        self.proposal = proposal or []

    def revise(self, *args, **kwargs):
        return self.proposal


def setup_plan():
    tools = [
        ToolDefinition("researcher", "Research source material", ("research",)),
        ToolDefinition("writer", "Write a report", ("create",)),
        ToolDefinition("checker", "Check the report", ("verify",)),
    ]
    plan = ExecutionPlan("Create a report", (
        PlannedStep("research", "Research", "researcher", {"topic": "energy", "goal": "Create a report"}),
        PlannedStep("write", "Write", "writer", {"topic": "energy"}, ("research",)),
        PlannedStep("verify", "Verify", "checker", {"topic": "energy"}, ("write",)),
    ))
    return tools, plan


class AdaptiveLoopTests(unittest.TestCase):
    def test_success_finishes(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        result = loop.decide(plan.goal, plan, ExecutionFeedback("succeeded", "All checks passed", ("research", "write", "verify")))
        self.assertEqual(result.action, "finish")

    def test_retryable_failure_defers_to_executor(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        result = loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Temporary network issue", ("research",), "write", retryable=True))
        self.assertEqual(result.action, "continue")

    def test_failure_without_backend_stops_safely(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        result = loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Writer failed", ("research",), "write"))
        self.assertEqual(result.action, "stop")

    def test_evidence_gap_requires_revision_or_stop(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        result = loop.decide(plan.goal, plan, ExecutionFeedback("succeeded", "Report created", ("research", "write", "verify"), evidence_gaps=("Missing source citation",)))
        self.assertEqual(result.action, "stop")
        self.assertIn("Missing source citation", result.unresolved_items)

    def test_approval_gate(self):
        tools, plan = setup_plan()
        gated = ExecutionPlan(plan.goal, plan.steps, requires_approval=True)
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        result = loop.decide(plan.goal, gated, ExecutionFeedback("failed", "Need approval"))
        self.assertEqual(result.action, "needs_approval")

    def test_max_revisions(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, AdaptationPolicy(max_revisions=1))
        result = loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Failure"), revision_number=1)
        self.assertEqual(result.action, "stop")

    def test_valid_replan_preserves_completed_step(self):
        tools, plan = setup_plan()
        proposal = [
            {"step_id": "research", "name": "Research", "handler_key": "researcher", "inputs": {"topic": "energy"}},
            {"step_id": "write", "name": "Rewrite report", "handler_key": "writer", "inputs": {"topic": "energy", "include_sources": True}, "depends_on": ["research"]},
            {"step_id": "verify", "name": "Verify", "handler_key": "checker", "inputs": {"topic": "energy"}, "depends_on": ["write"]},
        ]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, backend=FakeBackend(proposal))
        result = loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Report needs citations", ("research",), "write"))
        self.assertEqual(result.action, "replan")
        self.assertEqual(result.plan.planner_name, "fake-adapter")

    def test_replan_cannot_change_completed_step(self):
        tools, plan = setup_plan()
        proposal = [
            {"step_id": "research", "name": "Research again", "handler_key": "researcher", "inputs": {"topic": "different"}},
            {"step_id": "write", "name": "Write", "handler_key": "writer", "inputs": {"topic": "energy"}, "depends_on": ["research"]},
        ]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, backend=FakeBackend(proposal))
        with self.assertRaisesRegex(ValueError, "changed completed step"):
            loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Writer failed", ("research",), "write"))

    def test_unknown_tool_in_revision_rejected(self):
        tools, plan = setup_plan()
        proposal = [{"step_id": "x", "name": "Bad", "handler_key": "shell", "inputs": {}}]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, backend=FakeBackend(proposal))
        with self.assertRaisesRegex(ValueError, "unregistered tool"):
            loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Failed"))

    def test_unknown_completed_step_rejected(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        with self.assertRaisesRegex(ValueError, "not present"):
            loop.decide(plan.goal, plan, ExecutionFeedback("failed", "Failed", ("ghost",)))

    def test_invalid_feedback_rejected(self):
        tools, plan = setup_plan()
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools)
        with self.assertRaisesRegex(ValueError, "unsupported feedback"):
            loop.decide(plan.goal, plan, ExecutionFeedback("maybe", "Unsure"))


if __name__ == "__main__":
    unittest.main()
