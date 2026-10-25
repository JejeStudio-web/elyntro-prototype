"""Demonstrate task-aware routing using explicitly seeded evaluation evidence."""
import tempfile
from pathlib import Path
from elyntro import (
    AdaptiveModelRouter, EvaluationResult, EvaluationStore, ModelProfile,
    ModelRegistry, ModelRouter,
)

with tempfile.TemporaryDirectory() as folder:
    registry = ModelRegistry()
    registry.register(ModelProfile("code-model", "demo", frozenset({"coding", "reasoning"}), 1.0, 2.0, 16000, 2000))
    store = EvaluationStore(Path(folder) / "evaluations.db")
    base_router = ModelRouter(registry, store, minimum_samples=3)
    # Synthetic data only: replace with results from independently verified benchmarks.
    for _ in range(5):
        base_router.record(EvaluationResult("code-model", "coding", .91, 420, 500, 300, True, benchmark_id="synthetic-demo"))
    adaptive = AdaptiveModelRouter(base_router)
    result = adaptive.route("Debug this Python function", estimated_input_tokens=500,
                            max_output_tokens=1000, max_cost=.02)
    print("status:", result.status)
    print("task:", result.classification.task_type)
    print("decision:", result.decision)
    print("note: demonstration uses synthetic evaluation data, not a real provider benchmark")
