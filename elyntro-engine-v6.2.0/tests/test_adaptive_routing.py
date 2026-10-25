import tempfile
import unittest
from pathlib import Path

from elyntro.adaptive_routing import AdaptiveModelRouter, RoutingPolicy, TaskClassifier
from elyntro.model_evaluation import EvaluationResult, EvaluationStore
from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_router import ModelRouter


class AdaptiveRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("code-model", "p", frozenset({"coding", "reasoning"}), 1, 2, 10000, 2000))
        self.registry.register(ModelProfile("text-model", "p", frozenset({"text"}), 1, 1, 10000, 2000))
        self.store = EvaluationStore(str(Path(self.tmp.name) / "eval.db"))
        self.base = ModelRouter(self.registry, self.store, minimum_samples=3)
        self.adaptive = AdaptiveModelRouter(self.base)

    def tearDown(self): self.tmp.cleanup()

    def add(self, model, task, quality=.9, success=True):
        for _ in range(4):
            self.base.record(EvaluationResult(model, task, quality, 300, 100, 100, success, benchmark_id="test"))

    def test_classifier_identifies_code(self):
        result = TaskClassifier().classify("Debug this Python function")
        self.assertEqual(result.task_type, "coding")
        self.assertIn("coding", result.required_capabilities)

    def test_unknown_task_requests_clarification(self):
        result = self.adaptive.route("Please help me", estimated_input_tokens=100, max_output_tokens=100, max_cost=1)
        self.assertEqual(result.status, "needs_classification")

    def test_no_evidence_never_guesses_a_model(self):
        result = self.adaptive.route("Debug this Python function", estimated_input_tokens=100, max_output_tokens=100, max_cost=1)
        self.assertEqual(result.status, "needs_evaluation")
        self.assertIsNone(result.decision)

    def test_selects_based_on_task_specific_evidence(self):
        self.add("code-model", "coding")
        result = self.adaptive.route("Debug this Python function", estimated_input_tokens=100, max_output_tokens=100, max_cost=1)
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.decision.model_id, "code-model")

    def test_ineligible_capabilities_do_not_pass(self):
        self.add("text-model", "coding")
        result = self.adaptive.route("Debug this Python function", estimated_input_tokens=100, max_output_tokens=100, max_cost=1)
        self.assertEqual(result.status, "needs_evaluation")

    def test_policy_override_can_raise_quality_bar(self):
        self.add("code-model", "coding", quality=.85)
        result = self.adaptive.route("Debug this Python function", estimated_input_tokens=100, max_output_tokens=100, max_cost=1,
            policy_override=RoutingPolicy(min_quality=.95))
        self.assertEqual(result.status, "needs_evaluation")

    def test_explicit_task_override_supports_custom_benchmark_names(self):
        self.add("code-model", "custom_task")
        result = self.adaptive.route("Help me", estimated_input_tokens=100, max_output_tokens=100, max_cost=1,
            task_type_override="custom_task", required_capabilities_override=frozenset({"coding", "reasoning"}))
        self.assertEqual(result.status, "selected")

    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError): RoutingPolicy(min_quality=1.2)

    def test_empty_objective_rejected(self):
        with self.assertRaises(ValueError): self.adaptive.route(" ", estimated_input_tokens=1, max_output_tokens=1, max_cost=1)


if __name__ == "__main__": unittest.main()
