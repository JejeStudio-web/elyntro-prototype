"""End-to-end adaptive routing, provider execution, and verified feedback.

The service joins existing modules without treating model self-ratings as evidence.
Provider calls happen only after evidence-based routing selects a model and budget
preflight passes. Outcomes enter feedback only after an external verifier reports them.
Promotion to the router's evaluation store remains a separate explicit action.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping, Sequence
from uuid import uuid4

from .governance import ActionRequest, PolicyEngine, PolicyDecision, RiskLevel

from .adaptive_routing import AdaptiveModelRouter, RoutingPolicy, TaskClassification
from .model_gateway import ModelCallError, ModelCallResponse, ProviderAdapter
from .model_router import RoutingDecision
from .routing_feedback import RoutingFeedback, RoutingFeedbackStore


@dataclass(frozen=True)
class RoutedTaskRequest:
    objective: str
    messages: tuple[Mapping[str, str], ...]
    estimated_input_tokens: int
    max_output_tokens: int
    max_cost: float
    task_type_override: str | None = None
    required_capabilities_override: frozenset[str] | None = None
    policy_override: RoutingPolicy | None = None
    benchmark_id: str = "routed-task"
    timeout_seconds: float = 45.0
    excluded_model_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("objective is required")
        if not self.messages:
            raise ValueError("At least one message is required")
        if self.estimated_input_tokens < 0 or self.max_output_tokens < 1:
            raise ValueError("Invalid token limits")
        if self.max_cost < 0 or self.timeout_seconds <= 0:
            raise ValueError("max_cost must be non-negative and timeout_seconds positive")
        for message in self.messages:
            if set(message) - {"role", "content"}:
                raise ValueError("Messages may contain only role and content")
            if message.get("role") not in {"system", "user", "assistant"}:
                raise ValueError("Unsupported message role")
            if not isinstance(message.get("content"), str):
                raise ValueError("Message content must be text")


@dataclass(frozen=True)
class RoutedTaskResult:
    status: str  # succeeded | needs_classification | needs_evaluation | failed
    classification: TaskClassification
    decision: RoutingDecision | None
    response: ModelCallResponse | None
    reason: str
    feedback_id: int | None = None
    benchmark_id: str = "routed-task"
    governance_decision: PolicyDecision | None = None


class RoutedExecutionService:
    """Connect adaptive router -> selected provider -> external verification feedback."""

    def __init__(self, adaptive_router: AdaptiveModelRouter,
                 adapters: Mapping[str, ProviderAdapter], feedback_store: RoutingFeedbackStore,
                 governance: PolicyEngine | None = None):
        self.adaptive_router = adaptive_router
        self.adapters = dict(adapters)
        self.feedback_store = feedback_store
        # Optional for backwards compatibility; when supplied, policy runs before provider calls.
        self.governance = governance

    def execute(self, request: RoutedTaskRequest) -> RoutedTaskResult:
        routed = self.adaptive_router.route(
            request.objective,
            estimated_input_tokens=request.estimated_input_tokens,
            max_output_tokens=request.max_output_tokens,
            max_cost=request.max_cost,
            task_type_override=request.task_type_override,
            required_capabilities_override=request.required_capabilities_override,
            policy_override=request.policy_override,
            excluded_model_ids=request.excluded_model_ids,
        )
        if routed.decision is None:
            return RoutedTaskResult(routed.status, routed.classification, None, None, routed.reason, benchmark_id=request.benchmark_id)

        decision = routed.decision
        profile = self.adaptive_router.router.registry.get(decision.model_id)
        adapter = self.adapters.get(profile.provider)
        if adapter is None:
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    f"No adapter registered for provider {profile.provider}", benchmark_id=request.benchmark_id)
        upper_bound_cost = (
            request.estimated_input_tokens * profile.input_cost_per_million
            + request.max_output_tokens * profile.output_cost_per_million
        ) / 1_000_000
        if upper_bound_cost > request.max_cost:
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    "Selected model's conservative cost estimate exceeds the request budget", benchmark_id=request.benchmark_id)

        governance_decision = None
        if self.governance is not None:
            action = ActionRequest(
                action_id=f"model-call-{uuid4().hex}",
                tool_key=f"model:{profile.provider}",
                description=f"Invoke provider model {profile.model_id} for routed task",
                risk=RiskLevel.MEDIUM,
                estimated_cost_usd=upper_bound_cost,
                estimated_tokens=request.estimated_input_tokens + request.max_output_tokens,
                requires_network=True,
                writes_external_state=False,
            )
            governance_decision = self.governance.authorize(action)
            if not governance_decision.allowed:
                return RoutedTaskResult(
                    "failed", routed.classification, decision, None,
                    f"Governance blocked provider call: {governance_decision.outcome} ({governance_decision.reason})",
                    benchmark_id=request.benchmark_id, governance_decision=governance_decision,
                )

        started = time.monotonic()
        try:
            text, input_tokens, output_tokens = adapter.complete(
                profile.model_id, request.messages, request.max_output_tokens, request.timeout_seconds
            )
        except Exception as exc:
            # Do not expose provider response bodies, credentials, or arbitrary exception messages.
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    f"Provider call failed: {type(exc).__name__}", benchmark_id=request.benchmark_id,
                                    governance_decision=governance_decision)
        latency_ms = int((time.monotonic() - started) * 1000)
        if not isinstance(text, str) or input_tokens < 0 or output_tokens < 0:
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    "Provider returned invalid output or token counts", benchmark_id=request.benchmark_id,
                                    governance_decision=governance_decision)
        if input_tokens > profile.max_input_tokens or output_tokens > request.max_output_tokens:
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    "Provider usage exceeded the configured token limit", benchmark_id=request.benchmark_id,
                                    governance_decision=governance_decision)
        actual_cost = (input_tokens * profile.input_cost_per_million
                       + output_tokens * profile.output_cost_per_million) / 1_000_000
        if actual_cost > request.max_cost:
            return RoutedTaskResult("failed", routed.classification, decision, None,
                                    "Actual reported cost exceeded the request budget", benchmark_id=request.benchmark_id,
                                    governance_decision=governance_decision)
        response = ModelCallResponse(profile.model_id, profile.provider, text, input_tokens,
                                     output_tokens, latency_ms, actual_cost, decision)
        return RoutedTaskResult("succeeded", routed.classification, decision, response,
                                "Selected model executed within configured limits; outcome awaits verification",
                                benchmark_id=request.benchmark_id, governance_decision=governance_decision)

    def record_verified_outcome(self, result: RoutedTaskResult, *, quality: float, success: bool,
                                verifier_id: str, failure_category: str = "") -> int:
        """Persist an outcome after an external verifier has independently judged it.

        This records feedback but does not promote it into routing evidence. Call
        promote_verified_outcome explicitly after the application's review policy passes.
        """
        if result.status != "succeeded" or result.response is None or result.decision is None:
            raise ValueError("Only successful provider executions can receive outcome feedback")
        if not verifier_id.strip():
            raise ValueError("verifier_id is required for verified outcome feedback")
        record = RoutingFeedback(
            model_id=result.response.model_id,
            task_type=result.classification.task_type,
            predicted_quality=result.decision.observed_quality,
            observed_quality=quality,
            success=success,
            verified=True,
            verifier_id=verifier_id,
            failure_category=failure_category,
            latency_ms=result.response.latency_ms,
            input_tokens=result.response.input_tokens,
            output_tokens=result.response.output_tokens,
            benchmark_id=result.benchmark_id,
        )
        return self.feedback_store.add(record)

    def promote_verified_outcome(self, feedback_id: int) -> None:
        """Explicitly add a verified feedback record to the model router's evidence store."""
        self.feedback_store.promote_verified(feedback_id, self.adaptive_router.router)
