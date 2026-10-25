"""Bounded, auditable runtime for routed model execution.

Provides persistent idempotency keys, bounded model failover, an atomic UTC-day
budget reservation, a concurrency cap, and an append-only attempt history.
It remains a local single-host runtime: SQLite does not replace distributed
leases, provider-side idempotency, or a process sandbox.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .routed_execution import RoutedExecutionService, RoutedTaskRequest, RoutedTaskResult


class DuplicateExecutionError(RuntimeError):
    """An idempotency key has already been used; execution was not repeated."""


class DailyBudgetExceeded(RuntimeError):
    """The daily budget cannot safely reserve the requested maximum cost."""


@dataclass(frozen=True)
class RuntimeAttempt:
    attempt_number: int
    model_id: str | None
    status: str
    reason: str
    estimated_or_actual_cost: float


@dataclass(frozen=True)
class ReliableRunResult:
    run_id: str
    idempotency_key: str
    status: str  # succeeded | needs_classification | needs_evaluation | failed
    result: RoutedTaskResult | None
    attempts: tuple[RuntimeAttempt, ...]
    charged_cost: float
    reason: str


class ExecutionLedger:
    """SQLite ledger for idempotency, daily budget reservations, and attempts."""

    def __init__(self, path: str | Path = "elyntro_runtime.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runtime_runs (
                run_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL,
                reserved_cost REAL NOT NULL,
                charged_cost REAL NOT NULL DEFAULT 0,
                result_json TEXT NOT NULL DEFAULT '{}'
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS runtime_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                attempt_number INTEGER NOT NULL,
                model_id TEXT,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                cost REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, attempt_number),
                FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_runs_day ON runtime_runs(created_at, status)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_attempts_run ON runtime_attempts(run_id, attempt_number)")
            db.execute("""CREATE TABLE IF NOT EXISTS runtime_verifications (
                verification_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                verifier_id TEXT NOT NULL,
                passed INTEGER NOT NULL,
                evidence TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES runtime_runs(run_id)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runtime_verifications_run ON runtime_verifications(run_id, verification_id)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def begin(self, idempotency_key: str, reserve_cost: float, daily_budget: float) -> str:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1–200 non-whitespace characters")
        if reserve_cost < 0 or daily_budget <= 0:
            raise ValueError("reserve_cost must be non-negative and daily_budget must be positive")
        if reserve_cost > daily_budget:
            raise DailyBudgetExceeded("Request maximum cost exceeds the configured daily budget")
        now = self._now()
        day = now[:10]
        run_id = str(uuid.uuid4())
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT run_id FROM runtime_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if existing:
                db.execute("ROLLBACK")
                raise DuplicateExecutionError("This idempotency key has already been used; inspect the existing run instead of repeating it")
            row = db.execute("""SELECT COALESCE(SUM(CASE WHEN status='running' THEN reserved_cost ELSE charged_cost END),0) AS used
                FROM runtime_runs WHERE substr(created_at,1,10)=?""", (day,)).fetchone()
            used = float(row["used"] or 0.0)
            if used + reserve_cost > daily_budget + 1e-12:
                db.execute("ROLLBACK")
                raise DailyBudgetExceeded(f"Daily budget reservation denied: {used:.8f} already reserved or charged")
            db.execute("INSERT INTO runtime_runs(run_id,idempotency_key,created_at,status,reserved_cost) VALUES(?,?,?,?,?)",
                       (run_id, idempotency_key, now, "running", reserve_cost))
            db.execute("COMMIT")
            return run_id
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def add_attempt(self, run_id: str, attempt_number: int, model_id: str | None,
                    status: str, reason: str, cost: float = 0.0) -> None:
        if attempt_number < 1 or cost < 0:
            raise ValueError("attempt number must be positive and cost non-negative")
        db = self._connect()
        try:
            with db:
                db.execute("INSERT INTO runtime_attempts(run_id,attempt_number,model_id,status,reason,cost,created_at) VALUES(?,?,?,?,?,?,?)",
                           (run_id, attempt_number, model_id, status, reason[:500], cost, self._now()))
        finally:
            db.close()

    def finish(self, run_id: str, status: str, charged_cost: float, summary: dict[str, Any]) -> None:
        if charged_cost < 0:
            raise ValueError("charged_cost cannot be negative")
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT status,reserved_cost FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                if row["status"] != "running":
                    raise ValueError("Run is already terminal")
                # A completed successful run pays actual reported cost. If a provider was
                # selected but failed before usage was reported, conservatively retain the
                # reserved maximum to avoid undercounting unknown provider charges.
                if charged_cost > float(row["reserved_cost"]) + 1e-12:
                    raise ValueError("charged cost cannot exceed the reserved request maximum")
                db.execute("UPDATE runtime_runs SET status=?,charged_cost=?,result_json=? WHERE run_id=?",
                           (status, charged_cost, json.dumps(summary, sort_keys=True), run_id))
        finally:
            db.close()

    def get_run(self, idempotency_key: str) -> dict[str, Any]:
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM runtime_runs WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            data = dict(row)
            data["result"] = json.loads(data.pop("result_json"))
            data["attempts"] = [dict(r) for r in db.execute(
                "SELECT attempt_number,model_id,status,reason,cost,created_at FROM runtime_attempts WHERE run_id=? ORDER BY attempt_number",
                (data["run_id"],)).fetchall()]
            data["verifications"] = [dict(r) for r in db.execute(
                "SELECT verification_id,verifier_id,passed,evidence,reason,created_at FROM runtime_verifications WHERE run_id=? ORDER BY verification_id",
                (data["run_id"],)).fetchall()]
            return data
        finally:
            db.close()

    def recover_stale_runs(self, *, older_than_seconds: float = 300.0) -> list[dict[str, Any]]:
        """Mark old in-flight runs interrupted after a process restart.

        This deliberately does not replay provider calls: a crash may have happened
        after the provider completed but before the result was committed. The full
        reservation is charged conservatively because actual usage may be unknown.
        """
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must be non-negative")
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - older_than_seconds, timezone.utc
        ).isoformat()
        db = self._connect()
        recovered: list[dict[str, Any]] = []
        try:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT run_id,idempotency_key,reserved_cost FROM runtime_runs WHERE status='running' AND created_at<=?",
                (cutoff,),
            ).fetchall()
            for row in rows:
                summary = {
                    "status": "interrupted",
                    "reason": "Stale in-flight run recovered after interruption; not automatically replayed",
                    "recovery_policy": "conservative_full_reservation",
                }
                db.execute(
                    "UPDATE runtime_runs SET status='interrupted',charged_cost=reserved_cost,result_json=? WHERE run_id=? AND status='running'",
                    (json.dumps(summary, sort_keys=True), row["run_id"]),
                )
                db.execute(
                    "INSERT INTO runtime_attempts(run_id,attempt_number,model_id,status,reason,cost,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (row["run_id"], 999, None, "interrupted", summary["reason"],
                     float(row["reserved_cost"]), self._now()),
                )
                recovered.append({"run_id": row["run_id"], "idempotency_key": row["idempotency_key"],
                                  "status": "interrupted", "charged_cost": float(row["reserved_cost"])})
            db.execute("COMMIT")
            return recovered
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def record_verification(self, run_id: str, *, verifier_id: str, passed: bool,
                            evidence: str, reason: str = "") -> int:
        """Audit an external verification decision for a completed run.

        The caller must actually run an appropriate validator against the returned
        output before calling this method. This method stores the claim; it does not
        independently establish that the validator is trustworthy.
        """
        if not isinstance(verifier_id, str) or not verifier_id.strip() or len(verifier_id) > 200:
            raise ValueError("verifier_id must contain 1–200 non-whitespace characters")
        if not isinstance(passed, bool):
            raise ValueError("passed must be a boolean")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4000:
            raise ValueError("evidence must contain 1–4000 characters")
        if not isinstance(reason, str) or len(reason) > 1000:
            raise ValueError("reason must be at most 1000 characters")
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT status FROM runtime_runs WHERE run_id=?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                if row["status"] != "succeeded":
                    raise ValueError("Only succeeded runs can receive a verification record")
                cursor = db.execute(
                    "INSERT INTO runtime_verifications(run_id,verifier_id,passed,evidence,reason,created_at) VALUES(?,?,?,?,?,?)",
                    (run_id, verifier_id.strip(), int(bool(passed)), evidence, reason, self._now()),
                )
                return int(cursor.lastrowid)
        finally:
            db.close()

    def list_runs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """List recent runs for operational inspection; never returns raw model output."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        db = self._connect()
        try:
            if status is None:
                rows = db.execute("SELECT run_id,idempotency_key,created_at,status,reserved_cost,charged_cost,result_json "
                                  "FROM runtime_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = db.execute("SELECT run_id,idempotency_key,created_at,status,reserved_cost,charged_cost,result_json "
                                  "FROM runtime_runs WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit)).fetchall()
            return [{**{k: r[k] for k in ("run_id", "idempotency_key", "created_at", "status", "reserved_cost", "charged_cost")},
                     "result": json.loads(r["result_json"])} for r in rows]
        finally:
            db.close()

    def daily_usage(self) -> dict[str, float | str]:
        now = self._now()
        db = self._connect()
        try:
            row = db.execute("""SELECT COALESCE(SUM(CASE WHEN status='running' THEN reserved_cost ELSE charged_cost END),0) AS committed,
                COALESCE(SUM(CASE WHEN status='running' THEN reserved_cost ELSE 0 END),0) AS reserved
                FROM runtime_runs WHERE substr(created_at,1,10)=?""", (now[:10],)).fetchone()
            return {"utc_date": now[:10], "committed_or_reserved": float(row["committed"]), "currently_reserved": float(row["reserved"])}
        finally:
            db.close()


class ReliableRoutedExecutionService:
    """Adds bounded failover and operational guardrails to routed model execution."""

    def __init__(self, routed_service: RoutedExecutionService, ledger: ExecutionLedger,
                 *, max_concurrent: int = 3, max_attempts: int = 2) -> None:
        if max_concurrent < 1 or not 1 <= max_attempts <= 5:
            raise ValueError("max_concurrent must be positive and max_attempts must be between 1 and 5")
        self.routed_service = routed_service
        self.ledger = ledger
        self.max_attempts = max_attempts
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    def execute(self, request: RoutedTaskRequest, *, idempotency_key: str,
                daily_budget: float) -> ReliableRunResult:
        run_id = self.ledger.begin(idempotency_key, request.max_cost, daily_budget)
        attempts: list[RuntimeAttempt] = []
        excluded = set(request.excluded_model_ids)
        final: RoutedTaskResult | None = None
        charged_cost = 0.0
        failed_attempt_reserve = 0.0
        per_attempt_budget = request.max_cost / self.max_attempts
        reason = "No execution attempt completed"
        try:
            with self._semaphore:
                for number in range(1, self.max_attempts + 1):
                    attempt_request = RoutedTaskRequest(
                        objective=request.objective,
                        messages=request.messages,
                        estimated_input_tokens=request.estimated_input_tokens,
                        max_output_tokens=request.max_output_tokens,
                        max_cost=per_attempt_budget,
                        task_type_override=request.task_type_override,
                        required_capabilities_override=request.required_capabilities_override,
                        policy_override=request.policy_override,
                        benchmark_id=request.benchmark_id,
                        timeout_seconds=request.timeout_seconds,
                        excluded_model_ids=frozenset(excluded),
                    )
                    final = self.routed_service.execute(attempt_request)
                    model_id = final.decision.model_id if final.decision else None
                    if final.status == "succeeded" and final.response is not None:
                        attempt_cost = final.response.estimated_cost
                        charged_cost = min(request.max_cost, failed_attempt_reserve + attempt_cost)
                        attempt = RuntimeAttempt(number, model_id, "succeeded", final.reason, attempt_cost)
                        attempts.append(attempt)
                        self.ledger.add_attempt(run_id, number, model_id, "succeeded", final.reason, attempt_cost)
                        reason = "Execution succeeded within the configured request and daily budget reservations"
                        break
                    # If a model was selected, a provider call may have incurred charges even
                    # if usage was not returned. Reserve conservatively for that failure.
                    attempt_cost = per_attempt_budget if final.decision is not None else 0.0
                    attempts.append(RuntimeAttempt(number, model_id, final.status, final.reason, attempt_cost))
                    self.ledger.add_attempt(run_id, number, model_id, final.status, final.reason, attempt_cost)
                    if final.decision is None:
                        reason = final.reason
                        break
                    failed_attempt_reserve = min(request.max_cost, failed_attempt_reserve + attempt_cost)
                    charged_cost = failed_attempt_reserve
                    excluded.add(final.decision.model_id)
                    reason = final.reason
                    if number == self.max_attempts:
                        break
                if final is None:
                    status = "failed"
                elif final.status == "succeeded":
                    status = "succeeded"
                elif final.status in {"needs_classification", "needs_evaluation"}:
                    status = final.status
                else:
                    status = "failed"
                # Total charge is bounded by the request maximum, split across bounded attempts.
                self.ledger.finish(run_id, status, charged_cost,
                                   {"status": status, "reason": reason,
                                    "models_attempted": [a.model_id for a in attempts]})
                return ReliableRunResult(run_id, idempotency_key, status, final, tuple(attempts), charged_cost, reason)
        except Exception as exc:
            # Avoid leaking exception details. Close out the ledger before propagating
            # explicit policy/idempotency errors; run-level execution errors become safe results.
            try:
                self.ledger.finish(run_id, "failed", min(request.max_cost, charged_cost),
                                   {"status": "failed", "reason": type(exc).__name__})
            except Exception:
                pass
            if isinstance(exc, (DailyBudgetExceeded, DuplicateExecutionError, ValueError)):
                raise
            return ReliableRunResult(run_id, idempotency_key, "failed", final, tuple(attempts),
                                     min(request.max_cost, charged_cost), f"Runtime failed safely: {type(exc).__name__}")
