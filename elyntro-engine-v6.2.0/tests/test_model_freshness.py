import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from elyntro.model_evaluation import EvaluationResult, EvaluationStore
from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_router import ModelRouter, RoutingRequest


class ModelEvaluationFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("fresh-model", "provider", frozenset({"text"}), 1, 1, 10000, 1000))
        self.store = EvaluationStore(str(Path(self.temp.name) / "eval.db"))
        self.request = RoutingRequest("chat", frozenset({"text"}), 100, 100, 1.0, min_quality=.8, max_latency_ms=1000, min_success_rate=.8)

    def add(self, timestamp, benchmark):
        self.store.add(EvaluationResult("fresh-model", "chat", .95, 100, 100, 100, True, timestamp=timestamp, benchmark_id=benchmark))

    def test_stale_evaluations_are_not_used_when_freshness_is_configured(self):
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        for index in range(4):
            self.add(old, f"old-{index}")
        router = ModelRouter(self.registry, self.store, minimum_samples=3, max_evaluation_age_days=30)
        with self.assertRaisesRegex(RuntimeError, "freshness"):
            router.select(self.request)

    def test_recent_evaluations_can_be_selected(self):
        now = datetime.now(timezone.utc).isoformat()
        for index in range(3):
            self.add(now, f"benchmark-{index}")
        router = ModelRouter(self.registry, self.store, minimum_samples=3, max_evaluation_age_days=30, minimum_distinct_benchmarks=2)
        decision = router.select(self.request)
        self.assertEqual(decision.model_id, "fresh-model")
        self.assertIn("3 benchmark(s)", decision.reason)

    def test_single_benchmark_can_be_rejected_to_reduce_benchmark_overfit(self):
        now = datetime.now(timezone.utc).isoformat()
        for _ in range(3):
            self.add(now, "same-benchmark")
        router = ModelRouter(self.registry, self.store, minimum_samples=3, max_evaluation_age_days=30, minimum_distinct_benchmarks=2)
        with self.assertRaisesRegex(RuntimeError, "benchmark diversity"):
            router.select(self.request)

    def test_invalid_timestamp_fails_closed_when_freshness_is_enabled(self):
        for index in range(3):
            self.add("not-a-date", f"benchmark-{index}")
        router = ModelRouter(self.registry, self.store, minimum_samples=3, max_evaluation_age_days=30)
        with self.assertRaises(RuntimeError):
            router.select(self.request)


if __name__ == "__main__":
    unittest.main()
