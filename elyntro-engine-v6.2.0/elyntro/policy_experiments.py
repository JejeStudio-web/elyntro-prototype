"""Offline policy experiments and guarded activation for adaptive model routing.

This module never changes the active policy during simulation. Holdout outcomes
must be independently verified and are kept separate from development samples.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable


@dataclass(frozen=True)
class RoutingObservation:
    model_id: str
    task_type: str
    quality: float
    success: bool
    latency_ms: int
    cost: float = 0.0
    split: str = "development"  # development | validation | holdout
    verified: bool = False
    observation_id: str = ""

    def __post_init__(self):
        if not self.model_id.strip() or not self.task_type.strip():
            raise ValueError("model_id and task_type are required")
        if not 0 <= self.quality <= 1:
            raise ValueError("quality must be between 0 and 1")
        if self.latency_ms < 0 or self.cost < 0:
            raise ValueError("latency and cost cannot be negative")
        if self.split not in {"development", "validation", "holdout"}:
            raise ValueError("split must be development, validation, or holdout")


@dataclass(frozen=True)
class PolicySpec:
    name: str
    min_quality: float = 0.80
    min_success_rate: float = 0.85
    max_latency_ms: int = 30_000
    max_cost: float = 1.0
    min_samples: int = 3

    def __post_init__(self):
        if not self.name.strip():
            raise ValueError("policy name is required")
        if not 0 <= self.min_quality <= 1 or not 0 <= self.min_success_rate <= 1:
            raise ValueError("quality and success thresholds must be between 0 and 1")
        if self.max_latency_ms < 0 or self.max_cost < 0 or self.min_samples < 1:
            raise ValueError("latency/cost must be non-negative and min_samples >= 1")


@dataclass(frozen=True)
class PolicyMetrics:
    selected_models: dict[str, str]
    task_count: int
    success_rate: float
    mean_quality: float
    mean_latency_ms: float
    mean_cost: float
    eligible_task_count: int


@dataclass(frozen=True)
class PolicyExperimentReport:
    baseline: PolicyMetrics
    candidate: PolicyMetrics
    quality_delta: float
    success_rate_delta: float
    latency_delta_ms: float
    cost_delta: float
    holdout_count: int
    eligible_for_promotion: bool
    reasons: tuple[str, ...]


class PolicyExperimentRunner:
    """Select models from development evidence and score selections on holdout."""
    @staticmethod
    def _choose(policy: PolicySpec, rows: list[RoutingObservation], task_type: str) -> str | None:
        summaries = {}
        for model in sorted({r.model_id for r in rows if r.task_type == task_type}):
            sample = [r for r in rows if r.task_type == task_type and r.model_id == model]
            if len(sample) < policy.min_samples:
                continue
            success_rate = mean(1.0 if r.success else 0.0 for r in sample)
            quality = mean(r.quality for r in sample if r.success) if any(r.success for r in sample) else 0.0
            latency = mean(r.latency_ms for r in sample)
            cost = mean(r.cost for r in sample)
            if (quality >= policy.min_quality and success_rate >= policy.min_success_rate
                    and latency <= policy.max_latency_ms and cost <= policy.max_cost):
                # Prefer quality; use latency and cost as deterministic tie-breakers.
                summaries[model] = (quality, -latency, -cost, success_rate)
        return max(summaries, key=lambda m: summaries[m]) if summaries else None

    def _evaluate(self, policy: PolicySpec, development: list[RoutingObservation], holdout: list[RoutingObservation]) -> PolicyMetrics:
        tasks = sorted({r.task_type for r in development} & {r.task_type for r in holdout})
        chosen = {}
        scored = []
        for task in tasks:
            model = self._choose(policy, development, task)
            if model is None:
                continue
            outcomes = [r for r in holdout if r.task_type == task and r.model_id == model]
            if not outcomes:
                continue
            chosen[task] = model
            scored.extend(outcomes)
        if not scored:
            return PolicyMetrics(chosen, 0, 0.0, 0.0, 0.0, 0.0, 0)
        return PolicyMetrics(
            selected_models=chosen, task_count=len(scored),
            success_rate=mean(1.0 if r.success else 0.0 for r in scored),
            mean_quality=mean(r.quality for r in scored),
            mean_latency_ms=mean(r.latency_ms for r in scored),
            mean_cost=mean(r.cost for r in scored), eligible_task_count=len(chosen),
        )

    def run(self, baseline: PolicySpec, candidate: PolicySpec,
            observations: Iterable[RoutingObservation], *, min_quality_gain: float = 0.0,
            max_success_drop: float = 0.02, max_latency_regression_ms: float = 1000.0,
            max_cost_regression: float = 0.05, min_holdout_samples: int = 5) -> PolicyExperimentReport:
        rows = list(observations)
        development = [r for r in rows if r.split in {"development", "validation"} and r.verified]
        holdout = [r for r in rows if r.split == "holdout" and r.verified]
        if not development:
            raise ValueError("No verified development/validation observations available")
        if not holdout:
            raise ValueError("No verified holdout observations available")
        base = self._evaluate(baseline, development, holdout)
        cand = self._evaluate(candidate, development, holdout)
        reasons = []
        if cand.task_count < min_holdout_samples:
            reasons.append(f"Only {cand.task_count} scored holdout observations; need {min_holdout_samples}")
        q_delta = cand.mean_quality - base.mean_quality
        s_delta = cand.success_rate - base.success_rate
        l_delta = cand.mean_latency_ms - base.mean_latency_ms
        c_delta = cand.mean_cost - base.mean_cost
        if q_delta < min_quality_gain:
            reasons.append("Candidate does not meet minimum mean-quality gain")
        if s_delta < -max_success_drop:
            reasons.append("Candidate reduces holdout success rate beyond allowed tolerance")
        if l_delta > max_latency_regression_ms:
            reasons.append("Candidate latency regression exceeds limit")
        if c_delta > max_cost_regression:
            reasons.append("Candidate cost regression exceeds limit")
        if not cand.selected_models:
            reasons.append("Candidate selected no models from available evidence")
        return PolicyExperimentReport(base, cand, q_delta, s_delta, l_delta, c_delta,
                                     cand.task_count, not reasons, tuple(reasons))


class PolicyRegistry:
    """Versioned SQLite policy registry with explicit activation and rollback."""
    def __init__(self, path: str | Path = "elyntro_policies.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS policies (
                version INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                policy_json TEXT NOT NULL, created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0, experiment_json TEXT)""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def add_candidate(self, policy: PolicySpec) -> int:
        db = self._connect()
        try:
            with db:
                cur = db.execute("INSERT INTO policies(name, policy_json, created_at, active) VALUES (?, ?, ?, 0)",
                    (policy.name, json.dumps(asdict(policy), sort_keys=True), datetime.now(timezone.utc).isoformat()))
                return int(cur.lastrowid)
        finally:
            db.close()

    def activate(self, version: int, report: PolicyExperimentReport) -> None:
        if not report.eligible_for_promotion:
            raise ValueError("Policy experiment did not pass promotion criteria")
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT version FROM policies WHERE version = ?", (version,)).fetchone()
                if row is None:
                    raise KeyError(version)
                db.execute("UPDATE policies SET active = 0")
                db.execute("UPDATE policies SET active = 1, experiment_json = ? WHERE version = ?",
                           (json.dumps(asdict(report), sort_keys=True), version))
        finally:
            db.close()

    def active(self) -> tuple[int, PolicySpec] | None:
        db = self._connect()
        try:
            row = db.execute("SELECT version, policy_json FROM policies WHERE active = 1 ORDER BY version DESC LIMIT 1").fetchone()
            return (int(row["version"]), PolicySpec(**json.loads(row["policy_json"]))) if row else None
        finally:
            db.close()

    def rollback(self, version: int) -> None:
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT version FROM policies WHERE version = ?", (version,)).fetchone()
                if row is None:
                    raise KeyError(version)
                db.execute("UPDATE policies SET active = 0")
                db.execute("UPDATE policies SET active = 1 WHERE version = ?", (version,))
        finally:
            db.close()

    def history(self) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT version, name, created_at, active, experiment_json FROM policies ORDER BY version").fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()
