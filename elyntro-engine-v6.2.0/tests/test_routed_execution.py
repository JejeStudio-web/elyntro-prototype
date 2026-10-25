import tempfile
import unittest
from pathlib import Path

from elyntro import (
    AdaptiveModelRouter, EvaluationResult, EvaluationStore, ModelProfile, ModelRegistry,
    ModelRouter, RoutingFeedbackStore, RoutedExecutionService, RoutedTaskRequest,
)
from elyntro.governance import GovernancePolicy, PolicyEngine


class FakeAdapter:
    def __init__(self, text="verified candidate output", input_tokens=10, output_tokens=20):
        self.text, self.input_tokens, self.output_tokens = text, input_tokens, output_tokens
        self.calls = 0

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        return self.text, self.input_tokens, self.output_tokens


class FailingAdapter:
    def complete(self, *args, **kwargs):
        raise RuntimeError("provider secret body should not escape")


class RoutedExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("demo-model", "demo", frozenset({"text"}), 1, 1, 4000, 1000))
        self.evaluations = EvaluationStore(root / "eval.db")
        self.router = ModelRouter(self.registry, self.evaluations, minimum_samples=2)
        for _ in range(3):
            self.router.record(EvaluationResult("demo-model", "writing", .9, 50, 10, 10, True, benchmark_id="seed"))
        self.adaptive = AdaptiveModelRouter(self.router)
        self.feedback = RoutingFeedbackStore(root / "feedback.db")
        self.adapter = FakeAdapter()
        self.service = RoutedExecutionService(self.adaptive, {"demo": self.adapter}, self.feedback)
        self.request = RoutedTaskRequest(
            objective="write a short paragraph", messages=({"role": "user", "content": "write something"},),
            estimated_input_tokens=10, max_output_tokens=100, max_cost=1,
            task_type_override="writing", required_capabilities_override=frozenset({"text"}),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_route_execute_and_feedback_stays_separate_until_explicit_promotion(self):
        result = self.service.execute(self.request)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.response.model_id, "demo-model")
        self.assertEqual(self.adapter.calls, 1)
        before = len(self.evaluations.list(model_id="demo-model", task_type="writing"))
        fid = self.service.record_verified_outcome(result, quality=.95, success=True, verifier_id="external-test-v1")
        self.assertEqual(len(self.evaluations.list(model_id="demo-model", task_type="writing")), before)
        self.service.promote_verified_outcome(fid)
        self.assertEqual(len(self.evaluations.list(model_id="demo-model", task_type="writing")), before + 1)

    def test_no_evidence_does_not_call_provider(self):
        empty_store = EvaluationStore(Path(self.tmp.name) / "empty.db")
        empty_router = ModelRouter(self.registry, empty_store, minimum_samples=2)
        service = RoutedExecutionService(AdaptiveModelRouter(empty_router), {"demo": self.adapter}, self.feedback)
        result = service.execute(self.request)
        self.assertEqual(result.status, "needs_evaluation")
        self.assertEqual(self.adapter.calls, 0)

    def test_budget_preflight_does_not_call_provider(self):
        request = RoutedTaskRequest(
            objective="write a short paragraph", messages=({"role": "user", "content": "write"},),
            estimated_input_tokens=10, max_output_tokens=100, max_cost=0.00005,
            task_type_override="writing", required_capabilities_override=frozenset({"text"}),
        )
        result = self.service.execute(request)
        self.assertEqual(result.status, "failed")
        self.assertEqual(self.adapter.calls, 0)

    def test_provider_exception_is_sanitized(self):
        service = RoutedExecutionService(self.adaptive, {"demo": FailingAdapter()}, self.feedback)
        result = service.execute(self.request)
        self.assertEqual(result.status, "failed")
        self.assertNotIn("secret body", result.reason)


    def test_governance_denial_blocks_provider_call(self):
        policy = GovernancePolicy(allowed_tools=frozenset(), allow_network=True)
        service = RoutedExecutionService(self.adaptive, {"demo": self.adapter}, self.feedback, PolicyEngine(policy))
        result = service.execute(self.request)
        self.assertEqual(result.status, "failed")
        self.assertIn("Governance blocked", result.reason)
        self.assertIsNotNone(result.governance_decision)
        self.assertEqual(result.governance_decision.outcome, "deny")
        self.assertEqual(self.adapter.calls, 0)

    def test_governance_network_policy_blocks_before_provider_call(self):
        policy = GovernancePolicy(allowed_tools=frozenset({"model:demo"}), allow_network=False)
        service = RoutedExecutionService(self.adaptive, {"demo": self.adapter}, self.feedback, PolicyEngine(policy))
        result = service.execute(self.request)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.governance_decision.outcome, "deny")
        self.assertEqual(self.adapter.calls, 0)

    def test_governance_allows_and_records_provider_call(self):
        policy = GovernancePolicy(allowed_tools=frozenset({"model:demo"}))
        governance = PolicyEngine(policy)
        service = RoutedExecutionService(self.adaptive, {"demo": self.adapter}, self.feedback, governance)
        result = service.execute(self.request)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.governance_decision.outcome, "allow")
        self.assertEqual(self.adapter.calls, 1)
        self.assertEqual(governance.budget.snapshot().actions_reserved, 1)

    def test_unverified_feedback_cannot_be_promoted(self):
        result = self.service.execute(self.request)
        fid = self.feedback.add(__import__("elyntro").RoutingFeedback(
            model_id="demo-model", task_type="writing", predicted_quality=.9,
            observed_quality=.8, success=True, verified=False,
        ))
        with self.assertRaises(ValueError):
            self.service.promote_verified_outcome(fid)

    def test_invalid_verifier_identity_rejected(self):
        result = self.service.execute(self.request)
        with self.assertRaises(ValueError):
            self.service.record_verified_outcome(result, quality=.9, success=True, verifier_id="")


if __name__ == "__main__":
    unittest.main()
