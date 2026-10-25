import tempfile, unittest
from pathlib import Path
from elyntro.model_registry import ModelProfile, ModelRegistry
from elyntro.model_evaluation import EvaluationResult, EvaluationStore, estimate_cost
from elyntro.model_router import ModelRouter, RoutingRequest

class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "test.db")
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("cheap", "provider-x", frozenset({"reasoning"}), 1, 2, 10000, 2000))
        self.registry.register(ModelProfile("fast", "provider-y", frozenset({"reasoning", "coding"}), 2, 3, 10000, 2000))
        self.store = EvaluationStore(self.db)
        self.router = ModelRouter(self.registry, self.store, minimum_samples=3)
    def tearDown(self): self.temp.cleanup()
    def add_samples(self, model, quality, latency, success=True, count=3):
        for _ in range(count):
            self.router.record(EvaluationResult(model, "reasoning", quality, latency, 1000, 500, success, benchmark_id="v1"))
    def request(self, **kwargs):
        values = dict(task_type="reasoning", required_capabilities=frozenset({"reasoning"}), estimated_input_tokens=1000, max_output_tokens=500, max_cost=1, min_quality=.8, max_latency_ms=1000, min_success_rate=.8)
        values.update(kwargs)
        return RoutingRequest(**values)
    def test_selects_lowest_cost_eligible_model(self):
        self.add_samples("cheap", .9, 500); self.add_samples("fast", .95, 400)
        self.assertEqual(self.router.select(self.request()).model_id, "cheap")
    def test_requires_minimum_evidence(self):
        self.router.record(EvaluationResult("cheap", "reasoning", .99, 10, 1, 1, True))
        with self.assertRaises(RuntimeError): self.router.select(self.request())
    def test_rejects_low_quality(self):
        self.add_samples("cheap", .4, 500)
        with self.assertRaises(RuntimeError): self.router.select(self.request())
    def test_rejects_low_reliability(self):
        self.add_samples("cheap", .95, 500, True, 2)
        self.router.record(EvaluationResult("cheap", "reasoning", .95, 500, 1, 1, False))
        with self.assertRaises(RuntimeError): self.router.select(self.request())
    def test_capability_filter(self):
        self.add_samples("cheap", .95, 500); self.add_samples("fast", .95, 400)
        self.assertEqual(self.router.select(self.request(required_capabilities=frozenset({"coding"}))).model_id, "fast")
    def test_token_limit_filter(self):
        self.add_samples("cheap", .95, 500)
        with self.assertRaises(RuntimeError): self.router.select(self.request(estimated_input_tokens=20000))
    def test_cost_formula(self):
        p = self.registry.get("cheap")
        r = EvaluationResult("cheap", "reasoning", .9, 10, 1000000, 1000000, True)
        self.assertEqual(estimate_cost(p, r), 3)
    def test_persistence_across_store_instances(self):
        self.add_samples("cheap", .9, 500)
        other = EvaluationStore(self.db)
        self.assertEqual(len(other.list(model_id="cheap", task_type="reasoning")), 3)
    def test_unknown_model_rejected(self):
        with self.assertRaises(KeyError): self.router.record(EvaluationResult("unknown", "reasoning", .9, 10, 1, 1, True))
    def test_invalid_quality_rejected(self):
        with self.assertRaises(ValueError): EvaluationResult("cheap", "reasoning", 1.1, 1, 1, 1, True)
    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError): self.registry.register(self.registry.get("cheap"))
    def test_disabled_models_not_selected(self):
        self.registry.register(ModelProfile("disabled", "p", frozenset({"reasoning"}), 0, 0, 10000, 2000, False))
        self.add_samples("cheap", .9, 500)
        self.add_samples("disabled", .99, 1)
        self.assertEqual(self.router.select(self.request()).model_id, "cheap")

if __name__ == "__main__": unittest.main()
