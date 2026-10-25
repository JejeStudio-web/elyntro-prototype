import tempfile
import unittest
from pathlib import Path

from elyntro.evaluation_monitor import ModelHealthMonitor, RegressionPolicy
from elyntro.model_evaluation import EvaluationResult, EvaluationStore
from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_router import ModelRouter, RoutingRequest


class EvaluationMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = EvaluationStore(str(root / "eval.db"))
        self.monitor = ModelHealthMonitor(self.store, str(root / "health.db"),
            RegressionPolicy(window_size=3, min_quality_drop=.15, min_success_rate_drop=.2, max_latency_ratio=1.5))

    def tearDown(self):
        self.tmp.cleanup()

    def add_window(self, quality, success=True, latency=100, count=3):
        for i in range(count):
            self.store.add(EvaluationResult("m1", "coding", quality, latency, 100, 100, success,
                                             benchmark_id=f"b{i%2}"))

    def test_insufficient_data_does_not_claim_health(self):
        self.add_window(.9, count=3)
        self.assertEqual(self.monitor.assess("m1", "coding").status, "insufficient_data")

    def test_quality_regression_quarantines_model(self):
        self.add_window(.9)
        self.add_window(.6)
        assessment = self.monitor.assess_and_quarantine("m1", "coding")
        self.assertEqual(assessment.status, "regression")
        self.assertTrue(self.monitor.is_quarantined("m1", "coding"))
        self.assertIn("quality dropped", self.monitor.quarantine_reason("m1", "coding"))

    def test_latency_regression_quarantines_model(self):
        self.add_window(.9, latency=100)
        self.add_window(.9, latency=200)
        self.assertEqual(self.monitor.assess_and_quarantine("m1", "coding").status, "regression")

    def test_healthy_windows_do_not_quarantine(self):
        self.add_window(.9)
        self.add_window(.88, latency=110)
        self.assertEqual(self.monitor.assess_and_quarantine("m1", "coding").status, "healthy")
        self.assertFalse(self.monitor.is_quarantined("m1", "coding"))

    def test_manual_quarantine_persists_through_healthy_assessment(self):
        self.add_window(.9)
        self.add_window(.9)
        self.monitor.quarantine("m1", "coding", "operator requested review")
        self.monitor.assess_and_quarantine("m1", "coding")
        self.assertTrue(self.monitor.is_quarantined("m1", "coding"))
        self.monitor.release_quarantine("m1", "coding", reason="review completed")
        self.assertFalse(self.monitor.is_quarantined("m1", "coding"))

    def test_router_excludes_quarantined_model(self):
        self.add_window(.9, count=3)
        self.add_window(.9, count=3)
        registry = ModelRegistry()
        registry.register(ModelProfile("m1", "provider", frozenset({"text"}), 0, 0, 4000, 1000))
        router = ModelRouter(registry, self.store, minimum_samples=2, health_monitor=self.monitor)
        self.monitor.quarantine("m1", "coding", "manual review")
        request = RoutingRequest("coding", frozenset({"text"}), 100, 100, 10, min_quality=.5)
        with self.assertRaisesRegex(RuntimeError, "No model meets"):
            router.select(request)

    def test_invalid_policy_rejected(self):
        with self.assertRaises(ValueError): RegressionPolicy(window_size=1)
        with self.assertRaises(ValueError): RegressionPolicy(max_latency_ratio=.9)

    def test_assessment_history_is_persisted(self):
        self.add_window(.9)
        self.add_window(.6)
        self.monitor.assess_and_quarantine("m1", "coding")
        self.assertEqual(self.monitor.history("m1", "coding")[0]["status"], "regression")


if __name__ == "__main__":
    unittest.main()
