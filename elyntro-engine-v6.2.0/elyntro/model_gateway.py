"""Provider-neutral model execution with explicit routing and budget guards.

No provider is called unless its adapter is explicitly registered. API keys are
read from environment variables by adapters and are never stored in this module.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .model_evaluation import EvaluationResult
from .model_registry import ModelProfile
from .model_router import ModelRouter, RoutingDecision, RoutingRequest


class ModelCallError(RuntimeError):
    """Safe-to-display model call error without credentials or response bodies."""


@dataclass(frozen=True)
class ModelCallRequest:
    task_type: str
    messages: tuple[Mapping[str, str], ...]
    required_capabilities: frozenset[str]
    estimated_input_tokens: int
    max_output_tokens: int
    max_cost: float
    min_quality: float = 0.75
    max_latency_ms: int = 30_000
    min_success_rate: float = 0.8

    def __post_init__(self) -> None:
        if not self.task_type.strip():
            raise ValueError("task_type is required")
        if not self.messages:
            raise ValueError("At least one message is required")
        if self.estimated_input_tokens < 0 or self.max_output_tokens < 1:
            raise ValueError("Invalid token limits")
        if self.max_cost < 0:
            raise ValueError("max_cost cannot be negative")
        for message in self.messages:
            if set(message) - {"role", "content"}:
                raise ValueError("Messages may contain only role and content")
            if message.get("role") not in {"system", "user", "assistant"}:
                raise ValueError("Unsupported message role")
            if not isinstance(message.get("content"), str):
                raise ValueError("Message content must be text")


@dataclass(frozen=True)
class ModelCallResponse:
    model_id: str
    provider: str
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost: float
    routing_decision: RoutingDecision


class ProviderAdapter(Protocol):
    def complete(
        self,
        model_id: str,
        messages: Sequence[Mapping[str, str]],
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> tuple[str, int, int]:
        """Return (text, input_tokens, output_tokens)."""


class OpenAICompatibleAdapter:
    """Adapter for chat-completions-compatible APIs using only stdlib.

    Configure endpoint and key through constructor or environment. The default
    endpoint is the OpenAI API; use a provider-specific base URL for compatible
    services. This adapter does not retry POST requests automatically.
    """

    def __init__(
        self,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_key_env = api_key_env
        self.base_url = (base_url or os.getenv("ELYNTRO_OPENAI_COMPATIBLE_BASE_URL")
                         or "https://api.openai.com/v1").rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelCallError(f"Missing API key in environment variable {self.api_key_env}")
        payload = json.dumps({
            "model": model_id,
            "messages": list(messages),
            "max_tokens": max_output_tokens,
        }).encode("utf-8")
        req = Request(
            self.base_url + "/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=min(self.timeout_seconds, timeout_seconds)) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Deliberately avoid surfacing body text which may contain sensitive data.
            raise ModelCallError(f"Provider returned HTTP {exc.code}") from None
        except (URLError, TimeoutError) as exc:
            raise ModelCallError(f"Provider request failed: {type(exc).__name__}") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelCallError("Provider returned invalid JSON") from None
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError):
            raise ModelCallError("Provider response has an unexpected shape") from None
        if not isinstance(text, str) or input_tokens < 0 or output_tokens < 0:
            raise ModelCallError("Provider response contains invalid values")
        return text, input_tokens, output_tokens


class ModelGateway:
    def __init__(self, router: ModelRouter, adapters: Mapping[str, ProviderAdapter], timeout_seconds: float = 45.0):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.router = router
        self.adapters = dict(adapters)
        self.timeout_seconds = timeout_seconds

    def complete(self, request: ModelCallRequest) -> ModelCallResponse:
        routing_request = RoutingRequest(
            task_type=request.task_type,
            required_capabilities=request.required_capabilities,
            estimated_input_tokens=request.estimated_input_tokens,
            max_output_tokens=request.max_output_tokens,
            max_cost=request.max_cost,
            min_quality=request.min_quality,
            max_latency_ms=request.max_latency_ms,
            min_success_rate=request.min_success_rate,
        )
        decision = self.router.select(routing_request)
        profile: ModelProfile = self.router.registry.get(decision.model_id)
        adapter = self.adapters.get(profile.provider)
        if adapter is None:
            raise ModelCallError(f"No adapter registered for provider {profile.provider}")

        # Conservative preflight cap: assume the full requested output budget is used.
        upper_bound_cost = (
            request.estimated_input_tokens * profile.input_cost_per_million
            + request.max_output_tokens * profile.output_cost_per_million
        ) / 1_000_000
        if upper_bound_cost > request.max_cost:
            raise ModelCallError("Selected model's conservative cost estimate exceeds the request budget")

        started = time.monotonic()
        text, input_tokens, output_tokens = adapter.complete(
            profile.model_id, request.messages, request.max_output_tokens, self.timeout_seconds
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if input_tokens > profile.max_input_tokens or output_tokens > request.max_output_tokens:
            raise ModelCallError("Provider usage exceeded the configured token limit")
        cost = (input_tokens * profile.input_cost_per_million + output_tokens * profile.output_cost_per_million) / 1_000_000
        if cost > request.max_cost:
            # The call has already happened; report this rather than pretending it was prevented.
            raise ModelCallError("Actual reported cost exceeded the request budget")
        return ModelCallResponse(profile.model_id, profile.provider, text, input_tokens, output_tokens, latency_ms, cost, decision)

    def record_verified_result(self, response: ModelCallResponse, *, task_type: str, quality: float, success: bool, benchmark_id: str) -> None:
        """Record only after an external verifier has judged the result."""
        self.router.record(EvaluationResult(
            model_id=response.model_id,
            task_type=task_type,
            quality=quality,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            success=success,
            benchmark_id=benchmark_id,
        ))
