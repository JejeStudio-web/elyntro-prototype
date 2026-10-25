"""Repeated, paired comparisons of registered models on a fixed benchmark set.

This is a measurement harness, not a universal intelligence score. It reports
transparent deterministic-rubric metrics and uncertainty intervals without
third-party dependencies. Holdout discipline remains the caller's responsibility.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .benchmark import BenchmarkCase, BenchmarkRunner
from .model_evaluation import EvaluationStore
from .model_gateway import ProviderAdapter
from .model_registry import ModelProfile


@dataclass(frozen=True)
class ModelComparisonSummary:
    model_id: str
    provider: str
    split: str
    sample_count: int
    success_count: int
    success_rate: float
    success_rate_ci95: tuple[float, float]
    mean_quality: float
    quality_ci95: tuple[float, float]
    median_latency_ms: float
    mean_latency_ms: float
    median_estimated_cost: float
    mean_estimated_cost: float
    mean_input_tokens: float
    mean_output_tokens: float
    error_count: int


@dataclass(frozen=True)
class PairedModelDifference:
    model_a: str
    model_b: str
    paired_samples: int
    mean_quality_difference_a_minus_b: float
    mean_success_difference_a_minus_b: float
    note: str = "Descriptive paired difference; not a causal or universal ranking."


@dataclass(frozen=True)
class ComparisonReport:
    comparison_id: str
    split: str
    repeats: int
    case_ids: tuple[str, ...]
    summaries: tuple[ModelComparisonSummary, ...]
    paired_differences: tuple[PairedModelDifference, ...]
    raw_results: tuple[Mapping[str, object], ...]
    manifest: Mapping[str, object]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "comparison_id": self.comparison_id,
            "split": self.split,
            "repeats": self.repeats,
            "case_ids": list(self.case_ids),
            "manifest": dict(self.manifest),
            "manifest_sha256": self.manifest_sha256,
            "summaries": [asdict(x) for x in self.summaries],
            "paired_differences": [asdict(x) for x in self.paired_differences],
            "raw_results": [dict(x) for x in self.raw_results],
            "limitations": [
                "Scores reflect the configured deterministic grading rubric, not general intelligence.",
                "Confidence intervals use simple approximations and may be unreliable for small samples.",
                "Repeated calls to the same prompt may be correlated; intervals are descriptive.",
                "The caller must keep development and holdout cases separate and avoid tuning on holdout results.",
            ],
        }

    def export_json(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def export_csv(self, path: str | Path) -> None:
        fields = [
            "comparison_id", "manifest_sha256", "split", "repeat", "case_id", "model_id", "task_type",
            "quality", "success", "latency_ms", "input_tokens", "output_tokens",
            "estimated_cost", "error_type", "failures",
        ]
        with Path(path).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in self.raw_results:
                writer.writerow({**row, "failures": "; ".join(row.get("failures", ()))})


def _mean_ci95(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    mean = statistics.mean(values)
    if len(values) < 2:
        return (mean, mean)
    margin = 1.96 * statistics.stdev(values) / math.sqrt(len(values))
    return (max(0.0, mean - margin), min(1.0, mean + margin))


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    z = 1.96
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


class BenchmarkComparisonRunner:
    """Compare several models on identical cases, with repeated calls and raw exports."""
    def __init__(self, evaluation_store: EvaluationStore):
        self.runner = BenchmarkRunner(evaluation_store)

    def compare(
        self,
        *,
        profiles: Sequence[ModelProfile],
        adapters: Mapping[str, ProviderAdapter],
        cases: Sequence[BenchmarkCase],
        comparison_id: str,
        split: str = "validation",
        repeats: int = 1,
        max_output_tokens: int = 1000,
        timeout_seconds: float = 30.0,
    ) -> ComparisonReport:
        if not comparison_id.strip():
            raise ValueError("comparison_id is required")
        if split not in {"development", "validation", "holdout"}:
            raise ValueError("split must be development, validation, or holdout")
        if repeats < 1 or repeats > 20:
            raise ValueError("repeats must be between 1 and 20")
        if len(profiles) < 2:
            raise ValueError("At least two model profiles are required")
        if len({p.model_id for p in profiles}) != len(profiles):
            raise ValueError("Model IDs must be unique")
        if not cases or len({c.case_id for c in cases}) != len(cases):
            raise ValueError("Cases must be non-empty and case IDs unique")
        # BenchmarkCase has a split attribute in v1.5. getattr keeps this robust
        # if callers construct a compatible custom case class.
        wrong_split = [c.case_id for c in cases if getattr(c, "split", "validation") != split]
        if wrong_split:
            raise ValueError(f"Cases do not belong to requested split '{split}': {', '.join(wrong_split)}")
        for profile in profiles:
            if profile.provider not in adapters:
                raise ValueError(f"No adapter supplied for provider {profile.provider}")
        # The manifest fingerprints the exact prompts, grading rubric, split, model
        # profiles and execution limits. It stores hashes/metadata, not prompt text.
        manifest: dict[str, object] = {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "split": split,
            "repeats": repeats,
            "max_output_tokens": max_output_tokens,
            "timeout_seconds": timeout_seconds,
            "models": [
                {
                    "model_id": p.model_id,
                    "provider": p.provider,
                    "capabilities": sorted(p.capabilities),
                    "input_cost_per_million": p.input_cost_per_million,
                    "output_cost_per_million": p.output_cost_per_million,
                    "max_input_tokens": p.max_input_tokens,
                    "max_output_tokens": p.max_output_tokens,
                }
                for p in profiles
            ],
            "cases": [
                {
                    "case_id": c.case_id,
                    "task_type": c.task_type,
                    "messages": [dict(m) for m in c.messages],
                    "required_contains": list(c.required_contains),
                    "forbidden_contains": list(c.forbidden_contains),
                    "min_chars": c.min_chars,
                    "expected_exact": c.expected_exact,
                    "split": c.split,
                }
                for c in cases
            ],
            "reproducibility_note": "Same manifest means same declared inputs/configuration, not deterministic provider output.",
        }
        canonical_manifest = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        manifest_sha256 = hashlib.sha256(canonical_manifest.encode("utf-8")).hexdigest()
        raw: list[dict[str, object]] = []
        # Same fixed cases for every model and repeat; order is deterministic.
        for repeat in range(1, repeats + 1):
            for profile in profiles:
                report = self.runner.run(
                    profile=profile,
                    adapter=adapters[profile.provider],
                    cases=cases,
                    benchmark_id=f"{comparison_id}:{split}:r{repeat}",
                    max_output_tokens=max_output_tokens,
                    timeout_seconds=timeout_seconds,
                )
                for result in report.case_results:
                    raw.append({
                        "comparison_id": comparison_id,
                        "manifest_sha256": manifest_sha256,
                        "split": split,
                        "repeat": repeat,
                        "case_id": result.case_id,
                        "model_id": result.model_id,
                        "task_type": result.task_type,
                        "quality": result.quality,
                        "success": result.success,
                        "latency_ms": result.latency_ms,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "estimated_cost": result.estimated_cost,
                        "error_type": result.error_type or "",
                        "failures": result.failures,
                    })
        summaries: list[ModelComparisonSummary] = []
        for profile in profiles:
            rows = [r for r in raw if r["model_id"] == profile.model_id]
            successes = sum(bool(r["success"]) for r in rows)
            qualities = [float(r["quality"]) for r in rows]
            latencies = [float(r["latency_ms"]) for r in rows]
            costs = [float(r["estimated_cost"]) for r in rows]
            inputs = [float(r["input_tokens"]) for r in rows]
            outputs = [float(r["output_tokens"]) for r in rows]
            summaries.append(ModelComparisonSummary(
                model_id=profile.model_id, provider=profile.provider, split=split,
                sample_count=len(rows), success_count=successes,
                success_rate=successes / len(rows), success_rate_ci95=_wilson(successes, len(rows)),
                mean_quality=statistics.mean(qualities), quality_ci95=_mean_ci95(qualities),
                median_latency_ms=statistics.median(latencies), mean_latency_ms=statistics.mean(latencies),
                median_estimated_cost=statistics.median(costs), mean_estimated_cost=statistics.mean(costs),
                mean_input_tokens=statistics.mean(inputs), mean_output_tokens=statistics.mean(outputs),
                error_count=sum(bool(r["error_type"]) for r in rows),
            ))
        # Paired differences by (case, repeat), only when both models have a row.
        paired: list[PairedModelDifference] = []
        for i, a in enumerate(profiles):
            a_rows = {(r["case_id"], r["repeat"]): r for r in raw if r["model_id"] == a.model_id}
            for b in profiles[i + 1:]:
                b_rows = {(r["case_id"], r["repeat"]): r for r in raw if r["model_id"] == b.model_id}
                keys = sorted(set(a_rows) & set(b_rows))
                paired.append(PairedModelDifference(
                    a.model_id, b.model_id, len(keys),
                    statistics.mean([float(a_rows[k]["quality"]) - float(b_rows[k]["quality"]) for k in keys]) if keys else 0.0,
                    statistics.mean([int(bool(a_rows[k]["success"])) - int(bool(b_rows[k]["success"])) for k in keys]) if keys else 0.0,
                ))
        return ComparisonReport(comparison_id, split, repeats, tuple(c.case_id for c in cases),
                                tuple(summaries), tuple(paired), tuple(raw), manifest, manifest_sha256)
