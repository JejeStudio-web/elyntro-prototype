"""Offline demonstration of adaptive route -> provider adapter -> verified feedback.

Uses synthetic evaluation evidence and a fake adapter. It makes no network calls.
"""
import tempfile
from pathlib import Path

from elyntro import (
    AdaptiveModelRouter, EvaluationResult, EvaluationStore, ModelProfile, ModelRegistry,
    ModelRouter, RoutingFeedbackStore, RoutedExecutionService, RoutedTaskRequest,
)


class DemoAdapter:
    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        return "A short response to the user's request.", 12, 10


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    registry = ModelRegistry()
    registry.register(ModelProfile("demo-text-model", "demo", frozenset({"text"}), 1.0, 1.0, 8000, 2000))
    evaluations = EvaluationStore(root / "evaluations.db")
    router = ModelRouter(registry, evaluations, minimum_samples=3)
    # Synthetic seed evidence for routing demonstration only.
    for _ in range(5):
        router.record(EvaluationResult("demo-text-model", "writing", .88, 120, 12, 10, True, benchmark_id="synthetic-seed"))
    feedback = RoutingFeedbackStore(root / "feedback.db")
    service = RoutedExecutionService(AdaptiveModelRouter(router), {"demo": DemoAdapter()}, feedback)
    request = RoutedTaskRequest(
        objective="Write a concise paragraph", task_type_override="writing",
        required_capabilities_override=frozenset({"text"}),
        messages=({"role": "user", "content": "Write a concise paragraph."},),
        estimated_input_tokens=12, max_output_tokens=100, max_cost=.01,
        benchmark_id="demo-run-001",
    )
    result = service.execute(request)
    print("execution status:", result.status)
    print("selected model:", result.response.model_id if result.response else None)
    if result.status == "succeeded":
        feedback_id = service.record_verified_outcome(
            result, quality=.9, success=True, verifier_id="demo-external-check-v1"
        )
        print("verified feedback recorded:", feedback_id)
        print("router evidence remains unchanged until explicit promotion")
        service.promote_verified_outcome(feedback_id)
        print("feedback explicitly promoted to router evidence")
    print("note: synthetic evidence and fake adapter; no real provider was called")
