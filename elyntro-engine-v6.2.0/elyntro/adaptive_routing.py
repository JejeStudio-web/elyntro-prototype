"""Task-aware routing policy built on measured model evidence.

Classification is intentionally transparent and overridable. It never silently
chooses a model when the evaluation store lacks enough evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .model_router import ModelRouter, RoutingDecision, RoutingRequest


@dataclass(frozen=True)
class TaskClassification:
    task_type: str
    required_capabilities: frozenset[str]
    confidence: float
    signals: tuple[str, ...]

    def __post_init__(self):
        if not self.task_type.strip():
            raise ValueError("task_type is required")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class RoutingPolicy:
    min_quality: float = 0.80
    min_success_rate: float = 0.85
    max_latency_ms: int = 30_000
    min_classification_confidence: float = 0.40
    require_evidence: bool = True

    def __post_init__(self):
        if not 0 <= self.min_quality <= 1 or not 0 <= self.min_success_rate <= 1:
            raise ValueError("Quality and success thresholds must be between 0 and 1")
        if self.max_latency_ms < 0:
            raise ValueError("max_latency_ms cannot be negative")
        if not 0 <= self.min_classification_confidence <= 1:
            raise ValueError("confidence threshold must be between 0 and 1")


@dataclass(frozen=True)
class AdaptiveRoutingResult:
    status: str  # selected | needs_classification | needs_evaluation
    classification: TaskClassification
    decision: RoutingDecision | None
    reason: str


class TaskClassifier:
    """Small deterministic baseline classifier; callers can replace it."""
    RULES: tuple[tuple[str, tuple[str, ...], frozenset[str]], ...] = (
        ("coding", ("code", "coding", "debug", "python", "javascript", "function", "bug", "program"), frozenset({"coding", "reasoning"})),
        ("reasoning", ("reason", "analyze", "analyse", "logic", "compare", "evaluate", "decision", "plan"), frozenset({"reasoning"})),
        ("summarization", ("summarize", "summarise", "summary", "summarize", "resum", "resume", "síntesis"), frozenset({"text"})),
        ("translation", ("translate", "translation", "traduce", "traducir", "traducción", "traduccion"), frozenset({"text"})),
        ("creative", ("story", "creative", "brainstorm", "fiction", "poem", "ideas", "creativo", "historia"), frozenset({"text", "creative"})),
        ("research", ("research", "sources", "cite", "investigate", "investiga", "fuentes", "actualidad"), frozenset({"reasoning", "web"})),
    )

    def classify(self, text: str) -> TaskClassification:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text must be a non-empty string")
        normalized = text.casefold()
        matches = []
        for task_type, keywords, capabilities in self.RULES:
            hits = tuple(word for word in keywords if word in normalized)
            if hits:
                matches.append((len(hits), task_type, capabilities, hits))
        if not matches:
            return TaskClassification("general", frozenset({"text"}), 0.35, ())
        matches.sort(key=lambda item: (-item[0], item[1]))
        count, task_type, capabilities, hits = matches[0]
        # Confidence is a transparent heuristic, not a calibrated probability.
        confidence = min(0.95, 0.55 + 0.12 * (count - 1))
        return TaskClassification(task_type, capabilities, confidence, hits)


class AdaptiveModelRouter:
    """Classify an objective, apply policy constraints, and route on evidence."""
    def __init__(
        self,
        router: ModelRouter,
        *,
        classifier: TaskClassifier | None = None,
        policies: Mapping[str, RoutingPolicy] | None = None,
        default_policy: RoutingPolicy | None = None,
    ):
        self.router = router
        self.classifier = classifier or TaskClassifier()
        self.policies = dict(policies or {})
        self.default_policy = default_policy or RoutingPolicy()

    def route(
        self,
        objective: str,
        *,
        estimated_input_tokens: int,
        max_output_tokens: int,
        max_cost: float,
        task_type_override: str | None = None,
        required_capabilities_override: frozenset[str] | None = None,
        policy_override: RoutingPolicy | None = None,
        excluded_model_ids: frozenset[str] = frozenset(),
    ) -> AdaptiveRoutingResult:
        classification = self.classifier.classify(objective)
        if task_type_override:
            classification = TaskClassification(task_type_override, required_capabilities_override or classification.required_capabilities, 1.0, ("explicit_task_type_override",))
        elif required_capabilities_override is not None:
            classification = TaskClassification(classification.task_type, required_capabilities_override, classification.confidence, classification.signals)
        policy = policy_override or self.policies.get(classification.task_type, self.default_policy)
        if classification.confidence < policy.min_classification_confidence and not task_type_override:
            return AdaptiveRoutingResult("needs_classification", classification, None,
                "Task classification confidence is below policy threshold; supply a task_type_override or clarify the objective.")
        request = RoutingRequest(
            task_type=classification.task_type,
            required_capabilities=classification.required_capabilities,
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            max_cost=max_cost,
            min_quality=policy.min_quality,
            max_latency_ms=policy.max_latency_ms,
            min_success_rate=policy.min_success_rate,
        )
        try:
            decision = self.router.select(request, excluded_model_ids=excluded_model_ids)
        except RuntimeError as exc:
            status = "needs_evaluation" if policy.require_evidence else "needs_evaluation"
            return AdaptiveRoutingResult(status, classification, None, str(exc))
        return AdaptiveRoutingResult("selected", classification, decision,
            f"Selected {decision.model_id} using {decision.sample_count} persisted samples for {classification.task_type}; quality, reliability, latency, capability, token and cost limits passed.")
