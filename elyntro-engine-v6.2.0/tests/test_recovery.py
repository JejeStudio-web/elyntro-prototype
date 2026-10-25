import unittest
from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.failure_diagnosis import FailureCategory
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.recovery import AdaptiveRecoveryCoordinator


class RecoveryCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tools = [ToolDefinition(key="lookup", description="Look up data", capabilities=("research",))]
        self.planner = RuleBasedPlanner()
        self.loop = AdaptivePlanningLoop(self.planner, self.tools, policy=AdaptationPolicy(max_revisions=2))
        self.coordinator = AdaptiveRecoveryCoordinator(self.loop)
        self.plan = ExecutionPlan(goal="research something", steps=(
            PlannedStep("s1", "Look up", "lookup", {"q": "x"}),
        ))

    def test_transient_error_requires_explicit_idempotency(self):
        unsafe = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          error=TimeoutError("timed out"))
        self.assertEqual(unsafe.decision.action, "stop")
        self.assertFalse(unsafe.feedback.retryable)
        safe = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                        error=TimeoutError("timed out"), operation_idempotent=True)
        self.assertEqual(safe.decision.action, "continue")
        self.assertTrue(safe.feedback.retryable)
        self.assertFalse(safe.execution_authorized)

    def test_unknown_error_stops(self):
        result = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          error=RuntimeError("unknown"), operation_idempotent=True)
        self.assertEqual(result.diagnosis.category, FailureCategory.UNKNOWN)
        self.assertEqual(result.decision.action, "stop")

    def test_approval_gate_never_retries(self):
        result = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          error=TimeoutError(), approval_required=True,
                                          operation_idempotent=True)
        self.assertEqual(result.decision.action, "needs_approval")
        self.assertFalse(result.feedback.retryable)

    def test_evidence_gap_does_not_retry(self):
        result = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          evidence_gaps=["missing source"],
                                          operation_idempotent=True)
        self.assertEqual(result.diagnosis.category, FailureCategory.MISSING_EVIDENCE)
        self.assertFalse(result.feedback.retryable)
        self.assertEqual(result.decision.action, "stop")

    def test_completed_steps_are_passed_through(self):
        result = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          error=RuntimeError("unknown"), completed_step_ids=["s1"])
        self.assertEqual(result.feedback.completed_step_ids, ("s1",))

    def test_serialization_is_structured_and_never_authorizes_execution(self):
        result = self.coordinator.propose(goal="research something", current_plan=self.plan,
                                          error=TimeoutError(), operation_idempotent=True)
        data = result.to_dict()
        self.assertEqual(data["decision"]["action"], "continue")
        self.assertFalse(data["execution_authorized"])
        self.assertIn("diagnosis", data)


if __name__ == "__main__":
    unittest.main()
