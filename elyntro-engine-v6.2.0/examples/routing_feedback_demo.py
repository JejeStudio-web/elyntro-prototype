"""Demonstrate feedback assessment without using unverified outcomes as evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro import (
    EvaluationStore, ModelProfile, ModelRegistry, ModelRouter,
    RoutingFeedback, RoutingFeedbackStore,
)

with TemporaryDirectory() as folder:
    root = Path(folder)
    registry = ModelRegistry()
    registry.register(ModelProfile("demo-model", "simulated", frozenset({"text"}), 0.2, 0.8, 8000, 2000))
    evaluation_store = EvaluationStore(str(root / "evaluations.db"))
    router = ModelRouter(registry, evaluation_store, minimum_samples=3)
    feedback_store = RoutingFeedbackStore(str(root / "feedback.db"))

    for index in range(5):
        feedback_store.add(RoutingFeedback(
            model_id="demo-model", task_type="writing",
            predicted_quality=0.88, observed_quality=0.84,
            success=True, verified=True, verifier_id="demo-independent-review",
            latency_ms=700 + index * 15, input_tokens=300, output_tokens=500,
            benchmark_id="demo-verified-set",
        ))

    assessment = feedback_store.assessment("demo-model", "writing")
    print("Feedback assessment:", assessment.status)
    print("Verified samples:", assessment.verified_count)
    print("Mean prediction error:", round(assessment.mean_prediction_error, 3))
    print("No benchmark evidence is changed until an application explicitly promotes a record.")
