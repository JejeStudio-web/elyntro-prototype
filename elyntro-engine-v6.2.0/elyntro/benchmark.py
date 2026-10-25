"""Reproducible, locally graded model benchmark harness.

Benchmarks call an explicitly supplied adapter directly, so they can generate the
first evaluation samples needed by the evidence-based router. Nothing runs unless
BenchmarkRunner.run() is explicitly invoked by the application.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from .model_evaluation import EvaluationResult, EvaluationStore, summarize
from .model_gateway import ProviderAdapter
from .model_registry import ModelProfile


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    task_type: str
    messages: tuple[Mapping[str, str], ...]
    required_contains: tuple[str, ...] = ()
    forbidden_contains: tuple[str, ...] = ()
    min_chars: int = 1
    expected_exact: str | None = None
    split: str = "validation"

    def __post_init__(self) -> None:
        if not self.case_id.strip() or not self.task_type.strip():
            raise ValueError("case_id and task_type are required")
        if not self.messages:
            raise ValueError("messages cannot be empty")
        if self.min_chars < 0:
            raise ValueError("min_chars cannot be negative")
        if self.split not in {"development", "validation", "holdout"}:
            raise ValueError("split must be development, validation, or holdout")
        for message in self.messages:
            if set(message) - {"role", "content"}:
                raise ValueError("Messages may contain only role and content")
            if message.get("role") not in {"system", "user", "assistant"} or not isinstance(message.get("content"), str):
                raise ValueError("Each message requires a supported role and text content")
        if any(not isinstance(x, str) for x in (*self.required_contains, *self.forbidden_contains)):
            raise ValueError("grader phrases must be strings")

    def grade(self, answer: str) -> tuple[float, bool, tuple[str, ...]]:
        """Return deterministic rubric score, pass flag, and failed checks."""
        normalized = answer.casefold()
        checks: list[tuple[bool, str]] = []
        for phrase in self.required_contains:
            checks.append((phrase.casefold() in normalized, f"required phrase missing: {phrase[:100]}"))
        for phrase in self.forbidden_contains:
            checks.append((phrase.casefold() not in normalized, f"forbidden phrase present: {phrase[:100]}"))
        checks.append((len(answer.strip()) >= self.min_chars, f"answer shorter than {self.min_chars} characters"))
        if self.expected_exact is not None:
            checks.append((answer.strip() == self.expected_exact, "answer did not match expected exact text"))
        score = sum(ok for ok, _ in checks) / len(checks)
        failures = tuple(message for ok, message in checks if not ok)
        return score, not failures, failures


@dataclass(frozen=True)
class BenchmarkCaseResult:
    case_id: str
    model_id: str
    task_type: str
    quality: float
    success: bool
    latency_ms: int
    input_tokens: int
    output_tokens: int
    estimated_cost: float
    failures: tuple[str, ...] = ()
    error_type: str | None = None


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark_id: str
    model_id: str
    case_results: tuple[BenchmarkCaseResult, ...]
    summary: Mapping[str, object]


class BenchmarkRunner:
    """Runs a fixed case set against one registered profile and explicit adapter."""
    def __init__(self, evaluation_store: EvaluationStore):
        self.evaluation_store = evaluation_store

    def run(
        self,
        *,
        profile: ModelProfile,
        adapter: ProviderAdapter,
        cases: Sequence[BenchmarkCase],
        benchmark_id: str,
        max_output_tokens: int = 1000,
        timeout_seconds: float = 30.0,
        before_record: Callable[[BenchmarkCaseResult], None] | None = None,
    ) -> BenchmarkReport:
        if not benchmark_id.strip():
            raise ValueError("benchmark_id is required")
        if not cases:
            raise ValueError("At least one benchmark case is required")
        if max_output_tokens < 1 or max_output_tokens > profile.max_output_tokens:
            raise ValueError("max_output_tokens exceeds profile limits or is invalid")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        results: list[BenchmarkCaseResult] = []
        eval_rows: list[EvaluationResult] = []
        for case in cases:
            estimated_input = max(1, sum(len(m["content"]) for m in case.messages) // 4)
            if estimated_input > profile.max_input_tokens:
                raise ValueError(f"Benchmark case {case.case_id} exceeds model input limit")
            started = time.monotonic()
            text = ""
            input_tokens = output_tokens = 0
            error_type = None
            try:
                text, input_tokens, output_tokens = adapter.complete(
                    profile.model_id, case.messages, max_output_tokens, timeout_seconds
                )
                if not isinstance(text, str) or input_tokens < 0 or output_tokens < 0:
                    raise ValueError("Adapter returned invalid response values")
                if input_tokens > profile.max_input_tokens or output_tokens > max_output_tokens:
                    raise ValueError("Adapter reported usage above configured limits")
                quality, success, failures = case.grade(text)
            except Exception as exc:
                # Store exception type only; do not persist arbitrary exception text or response content.
                error_type = type(exc).__name__
                quality, success, failures = 0.0, False, ("model call or grading failed",)
            latency_ms = int((time.monotonic() - started) * 1000)
            cost = (input_tokens * profile.input_cost_per_million + output_tokens * profile.output_cost_per_million) / 1_000_000
            row = EvaluationResult(
                model_id=profile.model_id, task_type=case.task_type, quality=quality,
                latency_ms=latency_ms, input_tokens=input_tokens, output_tokens=output_tokens,
                success=success, benchmark_id=benchmark_id,
            )
            self.evaluation_store.add(row)
            item = BenchmarkCaseResult(case.case_id, profile.model_id, case.task_type, quality,
                                       success, latency_ms, input_tokens, output_tokens, cost, failures, error_type)
            results.append(item)
            eval_rows.append(row)
            if before_record is not None:
                before_record(item)
        # Summarize each task type separately; avoid mixing unlike task distributions.
        summaries = {}
        for task_type in sorted({c.task_type for c in cases}):
            task_rows = [r for r in eval_rows if r.task_type == task_type]
            summaries[task_type] = summarize(task_rows, profile)
        return BenchmarkReport(benchmark_id, profile.model_id, tuple(results), summaries)


def basic_text_cases() -> tuple[BenchmarkCase, ...]:
    """Tiny smoke-test suite, not a representative or authoritative benchmark."""
    return (
        BenchmarkCase("capital-france", "knowledge", ({"role": "user", "content": "What is the capital of France? Answer in one sentence."},), required_contains=("Paris",), min_chars=5),
        BenchmarkCase("simple-arithmetic", "reasoning", ({"role": "user", "content": "What is 17 + 25? Give the numeric answer."},), required_contains=("42",), min_chars=2),
        BenchmarkCase("uncertainty", "instruction_following", ({"role": "user", "content": "If you cannot determine a fact from the information given, say you do not have enough information. What is my favorite color?"},), required_contains=("enough information",), min_chars=10),
    )
