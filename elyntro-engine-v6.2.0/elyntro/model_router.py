"""Evidence-based model routing; this module selects but does not call providers."""
from __future__ import annotations
from dataclasses import dataclass
from statistics import median
from datetime import datetime, timedelta, timezone
from .model_registry import ModelRegistry
from .model_evaluation import EvaluationResult, EvaluationStore, estimate_cost

@dataclass(frozen=True)
class RoutingRequest:
    task_type: str
    required_capabilities: frozenset[str]
    estimated_input_tokens: int
    max_output_tokens: int
    max_cost: float
    min_quality: float = 0.75
    max_latency_ms: int = 30_000
    min_success_rate: float = 0.8

@dataclass(frozen=True)
class RoutingDecision:
    model_id: str
    provider: str
    estimated_cost: float
    observed_quality: float
    observed_latency_ms: float
    observed_success_rate: float
    sample_count: int
    reason: str

class ModelRouter:
    def __init__(
        self,
        registry: ModelRegistry,
        evaluation_store: EvaluationStore,
        minimum_samples: int = 5,
        *,
        max_evaluation_age_days: int | None = None,
        minimum_distinct_benchmarks: int = 1,
        health_monitor=None,
    ):
        if minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        if max_evaluation_age_days is not None and max_evaluation_age_days < 1:
            raise ValueError("max_evaluation_age_days must be positive or None")
        if minimum_distinct_benchmarks < 1:
            raise ValueError("minimum_distinct_benchmarks must be positive")
        self.registry, self.evaluation_store, self.minimum_samples = registry, evaluation_store, minimum_samples
        self.max_evaluation_age_days = max_evaluation_age_days
        self.minimum_distinct_benchmarks = minimum_distinct_benchmarks
        self.health_monitor = health_monitor

    def _eligible_evaluations(self, rows):
        """Return eligible rows; invalid timestamps fail closed when freshness is enabled."""
        if self.max_evaluation_age_days is None:
            return rows
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.max_evaluation_age_days)
        eligible = []
        for row in rows:
            try:
                stamp = datetime.fromisoformat(row.timestamp.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
            except (AttributeError, TypeError, ValueError):
                continue
            if stamp >= cutoff:
                eligible.append(row)
        return eligible

    def record(self, result: EvaluationResult) -> None:
        self.registry.get(result.model_id)
        self.evaluation_store.add(result)

    def select(self, request: RoutingRequest, *, excluded_model_ids: frozenset[str] = frozenset()) -> RoutingDecision:
        if not request.task_type.strip(): raise ValueError("task_type is required")
        if request.estimated_input_tokens < 0 or request.max_output_tokens < 1: raise ValueError("Invalid token limits")
        if request.max_cost < 0: raise ValueError("max_cost cannot be negative")
        if not 0 <= request.min_quality <= 1 or not 0 <= request.min_success_rate <= 1: raise ValueError("Quality and success thresholds must be between 0 and 1")
        if request.max_latency_ms < 0: raise ValueError("max_latency_ms cannot be negative")
        candidates = []
        for model in self.registry.list_enabled():
            if model.model_id in excluded_model_ids: continue
            if self.health_monitor is not None and self.health_monitor.is_quarantined(model.model_id, request.task_type): continue
            if not request.required_capabilities.issubset(model.capabilities): continue
            if request.estimated_input_tokens > model.max_input_tokens or request.max_output_tokens > model.max_output_tokens: continue
            stored_rows = self.evaluation_store.list(model_id=model.model_id, task_type=request.task_type)
            all_rows = self._eligible_evaluations(stored_rows)
            if len(all_rows) < self.minimum_samples: continue
            if len({r.benchmark_id for r in all_rows}) < self.minimum_distinct_benchmarks: continue
            successful = [r for r in all_rows if r.success]
            success_rate = len(successful) / len(all_rows)
            if len(successful) < self.minimum_samples or success_rate < request.min_success_rate: continue
            quality = float(median(r.quality for r in successful))
            latency = float(median(r.latency_ms for r in all_rows))
            cost = float(median(estimate_cost(model, r) for r in all_rows))
            if quality < request.min_quality or latency > request.max_latency_ms or cost > request.max_cost: continue
            candidates.append(RoutingDecision(model.model_id, model.provider, cost, quality, latency, success_rate, len(all_rows), f"Based on {len(all_rows)} eligible persisted samples across {len({r.benchmark_id for r in all_rows})} benchmark(s) for {request.task_type}"))
        if not candidates:
            raise RuntimeError("No model meets the requirements with sufficient eligible evaluation evidence; check freshness, benchmark diversity, quality, reliability, latency, capabilities, tokens and cost")
        return min(candidates, key=lambda c: (c.estimated_cost, -c.observed_quality, c.observed_latency_ms))
