"""Regression detection and conservative model quarantine from persisted evaluations.

The monitor compares adjacent evaluation windows. It is a guardrail, not a
statistical proof that a model has regressed; production use should validate
thresholds against representative, independently graded workloads.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Sequence

from .model_evaluation import EvaluationResult, EvaluationStore


@dataclass(frozen=True)
class RegressionPolicy:
    window_size: int = 5
    min_quality_drop: float = 0.15
    min_success_rate_drop: float = 0.20
    max_latency_ratio: float = 1.50

    def __post_init__(self) -> None:
        if self.window_size < 2:
            raise ValueError("window_size must be at least 2")
        if not 0 <= self.min_quality_drop <= 1:
            raise ValueError("min_quality_drop must be between 0 and 1")
        if not 0 <= self.min_success_rate_drop <= 1:
            raise ValueError("min_success_rate_drop must be between 0 and 1")
        if self.max_latency_ratio < 1:
            raise ValueError("max_latency_ratio must be at least 1")


@dataclass(frozen=True)
class ModelHealthAssessment:
    model_id: str
    task_type: str
    status: str  # insufficient_data | healthy | regression
    baseline_samples: int
    recent_samples: int
    baseline_success_rate: float | None
    recent_success_rate: float | None
    baseline_median_quality: float | None
    recent_median_quality: float | None
    baseline_median_latency_ms: float | None
    recent_median_latency_ms: float | None
    reasons: tuple[str, ...]


def _metrics(rows: Sequence[EvaluationResult]) -> dict[str, float | int | None]:
    successful = [r for r in rows if r.success]
    return {
        "success_rate": sum(r.success for r in rows) / len(rows) if rows else None,
        "quality": float(median([r.quality for r in successful])) if successful else None,
        "latency": float(median([r.latency_ms for r in rows])) if rows else None,
    }


class ModelHealthMonitor:
    """Detect evaluation regressions and persist fail-closed quarantine state."""

    def __init__(self, evaluation_store: EvaluationStore, path: str | Path = "elyntro_health.db",
                 policy: RegressionPolicy | None = None):
        self.evaluation_store = evaluation_store
        self.path = str(path)
        self.policy = policy or RegressionPolicy()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS model_quarantine (
                model_id TEXT NOT NULL, task_type TEXT NOT NULL, reason TEXT NOT NULL,
                source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(model_id, task_type))""")
            db.execute("""CREATE TABLE IF NOT EXISTS model_health_assessments (
                assessment_id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
                task_type TEXT NOT NULL, status TEXT NOT NULL, data_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def assess(self, model_id: str, task_type: str) -> ModelHealthAssessment:
        if not model_id.strip() or not task_type.strip():
            raise ValueError("model_id and task_type are required")
        rows = self.evaluation_store.list(model_id=model_id, task_type=task_type)
        # Stored evaluation IDs reflect insertion order, so recent observations are last.
        n = self.policy.window_size
        if len(rows) < n * 2:
            return ModelHealthAssessment(model_id, task_type, "insufficient_data",
                min(n, len(rows)), max(0, len(rows) - n), None, None, None, None, None, None,
                (f"Need at least {n * 2} evaluation records; have {len(rows)}.",))
        baseline, recent = rows[-2*n:-n], rows[-n:]
        b, r = _metrics(baseline), _metrics(recent)
        reasons: list[str] = []
        if b["quality"] is not None and r["quality"] is not None:
            drop = float(b["quality"]) - float(r["quality"])
            if drop >= self.policy.min_quality_drop:
                reasons.append(f"Median successful quality dropped by {drop:.3f}.")
        if b["success_rate"] is not None and r["success_rate"] is not None:
            drop = float(b["success_rate"]) - float(r["success_rate"])
            if drop >= self.policy.min_success_rate_drop:
                reasons.append(f"Success rate dropped by {drop:.3f}.")
        if b["latency"] is not None and r["latency"] is not None and float(b["latency"]) > 0:
            ratio = float(r["latency"]) / float(b["latency"])
            if ratio >= self.policy.max_latency_ratio:
                reasons.append(f"Median latency increased to {ratio:.2f}x baseline.")
        return ModelHealthAssessment(model_id, task_type, "regression" if reasons else "healthy",
            len(baseline), len(recent), b["success_rate"], r["success_rate"],
            b["quality"], r["quality"], b["latency"], r["latency"], tuple(reasons))

    def assess_and_quarantine(self, model_id: str, task_type: str) -> ModelHealthAssessment:
        assessment = self.assess(model_id, task_type)
        payload = json.dumps({k: getattr(assessment, k) for k in assessment.__dataclass_fields__},
                             sort_keys=True)
        db = self._connect()
        try:
            with db:
                db.execute("INSERT INTO model_health_assessments(model_id, task_type, status, data_json) VALUES (?, ?, ?, ?)",
                           (model_id, task_type, assessment.status, payload))
                if assessment.status == "regression":
                    db.execute("""INSERT INTO model_quarantine(model_id, task_type, reason, source)
                        VALUES (?, ?, ?, 'automatic') ON CONFLICT(model_id, task_type) DO UPDATE SET
                        reason=excluded.reason, source=CASE WHEN model_quarantine.source='manual' THEN 'manual' ELSE 'automatic' END""",
                        (model_id, task_type, " ".join(assessment.reasons)))
        finally:
            db.close()
        return assessment

    def quarantine(self, model_id: str, task_type: str, reason: str) -> None:
        if not model_id.strip() or not task_type.strip() or not reason.strip():
            raise ValueError("model_id, task_type and reason are required")
        db = self._connect()
        try:
            with db:
                db.execute("""INSERT INTO model_quarantine(model_id, task_type, reason, source)
                    VALUES (?, ?, ?, 'manual') ON CONFLICT(model_id, task_type) DO UPDATE SET
                    reason=excluded.reason, source='manual'""", (model_id, task_type, reason))
        finally:
            db.close()

    def is_quarantined(self, model_id: str, task_type: str) -> bool:
        db = self._connect()
        try:
            return db.execute("SELECT 1 FROM model_quarantine WHERE model_id=? AND task_type=?",
                              (model_id, task_type)).fetchone() is not None
        finally:
            db.close()

    def quarantine_reason(self, model_id: str, task_type: str) -> str | None:
        db = self._connect()
        try:
            row = db.execute("SELECT reason FROM model_quarantine WHERE model_id=? AND task_type=?",
                             (model_id, task_type)).fetchone()
            return str(row["reason"]) if row else None
        finally:
            db.close()

    def release_quarantine(self, model_id: str, task_type: str, *, reason: str) -> None:
        """Explicit release; caller should require appropriate authorization."""
        if not reason.strip():
            raise ValueError("release reason is required")
        db = self._connect()
        try:
            with db:
                db.execute("DELETE FROM model_quarantine WHERE model_id=? AND task_type=?",
                           (model_id, task_type))
        finally:
            db.close()

    def history(self, model_id: str, task_type: str) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT data_json FROM model_health_assessments WHERE model_id=? AND task_type=? ORDER BY assessment_id",
                              (model_id, task_type)).fetchall()
            return [json.loads(row["data_json"]) for row in rows]
        finally:
            db.close()
