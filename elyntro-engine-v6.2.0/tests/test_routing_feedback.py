import tempfile
import unittest
from pathlib import Path

from elyntro.model_evaluation import EvaluationStore
from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_router import ModelRouter
from elyntro.routing_feedback import RoutingFeedback, RoutingFeedbackStore


class RoutingFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        registry = ModelRegistry()
        registry.register(ModelProfile("m1", "provider", frozenset({"text"}), 1, 1, 4000, 1000))
        self.evals = EvaluationStore(str(self.path / "eval.db"))
        self.router = ModelRouter(registry, self.evals, minimum_samples=2)
        self.feedback = RoutingFeedbackStore(str(self.path / "feedback.db"))

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **overrides):
        values = dict(model_id="m1", task_type="writing", predicted_quality=.9,
                      observed_quality=.85, success=True, verified=True,
                      verifier_id="human-review-v1", latency_ms=100,
                      input_tokens=50, output_tokens=80, benchmark_id="heldout-v1")
        values.update(overrides)
        return self.feedback.add(RoutingFeedback(**values))

    def test_unverified_feedback_not_counted_in_assessment(self):
        self.record(verified=False, verifier_id="")
        assessment = self.feedback.assessment("m1", "writing", min_verified_samples=1)
        self.assertEqual(assessment.verified_count, 0)
        self.assertEqual(assessment.status, "insufficient_data")

    def test_verified_feedback_assessed(self):
        for _ in range(5): self.record()
        assessment = self.feedback.assessment("m1", "writing")
        self.assertEqual(assessment.status, "healthy")
        self.assertEqual(assessment.verified_count, 5)

    def test_poor_success_rate_flags_investigation(self):
        for _ in range(5): self.record(success=False, observed_quality=.2, failure_category="incorrect")
        assessment = self.feedback.assessment("m1", "writing")
        self.assertEqual(assessment.status, "investigate")
        self.assertEqual(assessment.failure_categories["incorrect"], 5)

    def test_verified_record_can_be_promoted_once(self):
        fid = self.record()
        self.feedback.promote_verified(fid, self.router)
        self.assertEqual(len(self.evals.list(model_id="m1", task_type="writing")), 1)
        with self.assertRaises(ValueError): self.feedback.promote_verified(fid, self.router)

    def test_unverified_record_cannot_be_promoted(self):
        fid = self.record(verified=False, verifier_id="")
        with self.assertRaises(ValueError): self.feedback.promote_verified(fid, self.router)
        self.assertEqual(self.evals.list(), [])

    def test_verified_requires_verifier_identity(self):
        with self.assertRaises(ValueError): RoutingFeedback("m1", "writing", .8, .8, True, True)

    def test_missing_feedback_id_raises(self):
        with self.assertRaises(KeyError): self.feedback.get(999)


if __name__ == "__main__": unittest.main()
