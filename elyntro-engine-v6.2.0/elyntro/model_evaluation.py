"""Persisted evaluation records and transparent cost/quality measurements."""
from __future__ import annotations
from .sqlite_utils import ClosingConnection
import json, sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Iterable

@dataclass(frozen=True)
class EvaluationResult:
    model_id: str
    task_type: str
    quality: float
    latency_ms: int
    input_tokens: int
    output_tokens: int
    success: bool
    timestamp: str = ""
    benchmark_id: str = "unspecified"

    def __post_init__(self):
        if not self.model_id.strip() or not self.task_type.strip():
            raise ValueError("model_id and task_type are required")
        if not 0.0 <= self.quality <= 1.0:
            raise ValueError("quality must be between 0 and 1")
        if self.latency_ms < 0 or self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("latency and token counts cannot be negative")
        if not self.timestamp:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc).isoformat())

def estimate_cost(profile, result: EvaluationResult) -> float:
    return (result.input_tokens * profile.input_cost_per_million + result.output_tokens * profile.output_cost_per_million) / 1_000_000

def summarize(results: Iterable[EvaluationResult], profile) -> dict:
    rows = list(results)
    if not rows:
        raise ValueError("Cannot summarize an empty evaluation set")
    good = [r for r in rows if r.success]
    return {
        "model_id": profile.model_id,
        "task_type": rows[0].task_type,
        "sample_count": len(rows),
        "success_count": len(good),
        "success_rate": len(good) / len(rows),
        "median_quality": median([r.quality for r in good]) if good else 0.0,
        "median_latency_ms": median([r.latency_ms for r in rows]),
        "median_estimated_cost": median([estimate_cost(profile, r) for r in rows]),
        "benchmark_ids": sorted({r.benchmark_id for r in rows}),
    }

class EvaluationStore:
    """Small SQLite store; can share the same DB file as Elyntro's task store."""
    def __init__(self, path: str | Path = "elyntro.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS model_evaluations (evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL, task_type TEXT NOT NULL, timestamp TEXT NOT NULL, data_json TEXT NOT NULL)")
                db.execute("CREATE INDEX IF NOT EXISTS idx_model_eval_lookup ON model_evaluations(model_id, task_type, evaluation_id)")
        finally:
            db.close()

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def add(self, result: EvaluationResult) -> None:
        payload = {k: getattr(result, k) for k in result.__dataclass_fields__}
        db = self._connect()
        try:
            with db:
                db.execute("INSERT INTO model_evaluations(model_id, task_type, timestamp, data_json) VALUES (?, ?, ?, ?)", (result.model_id, result.task_type, result.timestamp, json.dumps(payload)))
        finally:
            db.close()

    def list(self, *, model_id: str | None = None, task_type: str | None = None) -> list[EvaluationResult]:
        query = "SELECT data_json FROM model_evaluations WHERE 1=1"
        params = []
        if model_id is not None:
            query += " AND model_id = ?"; params.append(model_id)
        if task_type is not None:
            query += " AND task_type = ?"; params.append(task_type)
        query += " ORDER BY evaluation_id"
        db = self._connect()
        try:
            rows = db.execute(query, params).fetchall()
            return [EvaluationResult(**json.loads(row["data_json"])) for row in rows]
        finally:
            db.close()
