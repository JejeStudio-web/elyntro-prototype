"""Verified outcome feedback for auditing and improving model routing.

Feedback is kept separate from benchmark evidence until an explicitly verified
record is promoted. No model self-rating is treated as independent evidence.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable

from .model_evaluation import EvaluationResult, EvaluationStore
from .model_router import ModelRouter


@dataclass(frozen=True)
class RoutingFeedback:
    model_id: str
    task_type: str
    predicted_quality: float
    observed_quality: float
    success: bool
    verified: bool
    verifier_id: str = ""
    failure_category: str = ""
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    benchmark_id: str = "production-feedback"
    timestamp: str = ""

    def __post_init__(self):
        if not self.model_id.strip() or not self.task_type.strip():
            raise ValueError("model_id and task_type are required")
        if not 0 <= self.predicted_quality <= 1 or not 0 <= self.observed_quality <= 1:
            raise ValueError("quality values must be between 0 and 1")
        if min(self.latency_ms, self.input_tokens, self.output_tokens) < 0:
            raise ValueError("latency and token counts cannot be negative")
        if self.verified and not self.verifier_id.strip():
            raise ValueError("verified feedback requires a verifier_id")
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class FeedbackAssessment:
    model_id: str
    task_type: str
    sample_count: int
    verified_count: int
    success_rate: float
    mean_observed_quality: float
    mean_prediction_error: float
    failure_categories: dict[str, int]
    status: str  # insufficient_data | healthy | investigate
    reasons: tuple[str, ...]


class RoutingFeedbackStore:
    """SQLite ledger for post-task outcomes, verification and promotion history."""

    def __init__(self, path: str | Path = "elyntro_feedback.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS routing_feedback (
                feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id TEXT NOT NULL, task_type TEXT NOT NULL,
                timestamp TEXT NOT NULL, data_json TEXT NOT NULL,
                promoted INTEGER NOT NULL DEFAULT 0,
                UNIQUE(model_id, task_type, timestamp, data_json))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_feedback_model_task ON routing_feedback(model_id, task_type, feedback_id)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _payload(record: RoutingFeedback) -> dict:
        return {name: getattr(record, name) for name in record.__dataclass_fields__}

    def add(self, record: RoutingFeedback) -> int:
        payload = json.dumps(self._payload(record), sort_keys=True)
        db = self._connect()
        try:
            with db:
                cur = db.execute(
                    "INSERT INTO routing_feedback(model_id, task_type, timestamp, data_json) VALUES (?, ?, ?, ?)",
                    (record.model_id, record.task_type, record.timestamp, payload),
                )
                return int(cur.lastrowid)
        finally:
            db.close()

    def get(self, feedback_id: int) -> RoutingFeedback:
        db = self._connect()
        try:
            row = db.execute("SELECT data_json FROM routing_feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
            if row is None:
                raise KeyError(feedback_id)
            return RoutingFeedback(**json.loads(row["data_json"]))
        finally:
            db.close()

    def list(self, *, model_id: str | None = None, task_type: str | None = None,
             verified_only: bool = False) -> list[tuple[int, RoutingFeedback]]:
        query = "SELECT feedback_id, data_json FROM routing_feedback WHERE 1=1"
        params: list[object] = []
        if model_id is not None:
            query += " AND model_id = ?"; params.append(model_id)
        if task_type is not None:
            query += " AND task_type = ?"; params.append(task_type)
        query += " ORDER BY feedback_id"
        db = self._connect()
        try:
            rows = db.execute(query, params).fetchall()
            parsed = [(int(r["feedback_id"]), RoutingFeedback(**json.loads(r["data_json"]))) for r in rows]
            return [(i, record) for i, record in parsed if not verified_only or record.verified]
        finally:
            db.close()

    def assessment(self, model_id: str, task_type: str, *, min_verified_samples: int = 5,
                   min_success_rate: float = 0.8, max_mean_prediction_error: float = 0.2) -> FeedbackAssessment:
        if min_verified_samples < 1:
            raise ValueError("min_verified_samples must be positive")
        if not 0 <= min_success_rate <= 1 or not 0 <= max_mean_prediction_error <= 1:
            raise ValueError("thresholds must be between 0 and 1")
        records = [r for _, r in self.list(model_id=model_id, task_type=task_type)]
        verified = [r for r in records if r.verified]
        categories: dict[str, int] = {}
        for r in verified:
            if r.failure_category:
                categories[r.failure_category] = categories.get(r.failure_category, 0) + 1
        reasons: list[str] = []
        if len(verified) < min_verified_samples:
            status = "insufficient_data"
            reasons.append(f"Need {min_verified_samples} independently verified outcomes; have {len(verified)}.")
        else:
            success_rate = sum(r.success for r in verified) / len(verified)
            prediction_error = mean(abs(r.predicted_quality - r.observed_quality) for r in verified)
            if success_rate < min_success_rate:
                reasons.append(f"Verified success rate {success_rate:.3f} is below {min_success_rate:.3f}.")
            if prediction_error > max_mean_prediction_error:
                reasons.append(f"Mean prediction error {prediction_error:.3f} exceeds {max_mean_prediction_error:.3f}.")
            status = "investigate" if reasons else "healthy"
        success_rate = sum(r.success for r in verified) / len(verified) if verified else 0.0
        quality = mean(r.observed_quality for r in verified) if verified else 0.0
        error = mean(abs(r.predicted_quality - r.observed_quality) for r in verified) if verified else 0.0
        return FeedbackAssessment(model_id, task_type, len(records), len(verified), success_rate,
                                  quality, error, categories, status, tuple(reasons))

    def promote_verified(self, feedback_id: int, router: ModelRouter) -> None:
        """Explicitly promote one independently verified outcome to router evidence.

        Repeat calls are rejected after a successful promotion. Promotion is explicit;
        unverified feedback cannot enter the evaluation store. The feedback and evaluation stores
        are separate SQLite databases, so this operation is not crash-atomic across both files.
        """
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT data_json, promoted FROM routing_feedback WHERE feedback_id = ?", (feedback_id,)).fetchone()
                if row is None:
                    raise KeyError(feedback_id)
                record = RoutingFeedback(**json.loads(row["data_json"]))
                if not record.verified:
                    raise ValueError("Unverified feedback cannot be promoted to model evaluation evidence")
                if row["promoted"]:
                    raise ValueError("Feedback has already been promoted")
                # Validate model identity and persist the evaluation before marking promotion.
                router.record(EvaluationResult(
                    model_id=record.model_id, task_type=record.task_type,
                    quality=record.observed_quality, latency_ms=record.latency_ms,
                    input_tokens=record.input_tokens, output_tokens=record.output_tokens,
                    success=record.success, timestamp=record.timestamp,
                    benchmark_id=record.benchmark_id,
                ))
                db.execute("UPDATE routing_feedback SET promoted = 1 WHERE feedback_id = ?", (feedback_id,))
        finally:
            db.close()
