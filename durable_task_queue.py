"""SQLite-backed task queue with leases, heartbeats, and bounded recovery.

Execution is at-least-once around process crashes. Handlers that mutate external state
must be idempotent or use Elyntro's OperationJournal; an expired lease alone cannot
prove whether an external side effect happened.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DurableTaskQueue:
    def __init__(self, db_path: str | Path, handler: Callable[[dict[str, Any]], dict[str, Any]],
                 *, lease_seconds: int = 180, max_recoveries: int = 5, poll_seconds: float = 1.0):
        self.db_path = str(db_path)
        self.handler = handler
        self.lease_seconds = max(30, int(lease_seconds))
        self.max_recoveries = max(1, int(max_recoveries))
        self.poll_seconds = max(0.1, float(poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=15000")
        return db

    def _initialize(self):
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS durable_task_jobs (
                    task_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL DEFAULT 'queued',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lease_until REAL,
                    worker_id TEXT,
                    result_json TEXT,
                    error TEXT
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim ON durable_task_jobs(state, lease_until, created_at)")

    def enqueue(self, *, task_id: str, goal: str, constraints: list[str],
                success_criteria: list[str], namespace: str, idempotency_key: str) -> dict[str, Any]:
        now = time.time()
        with self._connect() as db:
            db.execute("""
                INSERT OR IGNORE INTO durable_task_jobs
                (task_id, goal, constraints_json, criteria_json, namespace, idempotency_key,
                 state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """, (task_id, goal, json.dumps(constraints, ensure_ascii=False),
                  json.dumps(success_criteria, ensure_ascii=False), namespace,
                  idempotency_key, now, now))
            row = db.execute("SELECT * FROM durable_task_jobs WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise RuntimeError("No se pudo persistir el trabajo.")
            if row["idempotency_key"] != idempotency_key:
                raise ValueError("La tarea ya está asociada a otra clave de idempotencia.")
            return self._public(row)

    @staticmethod
    def _public(row) -> dict[str, Any]:
        return {
            "task_id": row["task_id"], "state": row["state"],
            "attempt_count": row["attempt_count"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "lease_until": row["lease_until"],
            "error": row["error"], "result": json.loads(row["result_json"]) if row["result_json"] else None,
        }

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM durable_task_jobs WHERE task_id=?", (task_id,)).fetchone()
        return self._public(row) if row else None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="elyntro-durable-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _claim(self) -> dict[str, Any] | None:
        now = time.time()
        worker_id = f"{os.getpid()}:{threading.get_ident()}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT * FROM durable_task_jobs
                WHERE state='queued' OR (state='running' AND lease_until < ?)
                ORDER BY created_at ASC LIMIT 1
            """, (now,)).fetchone()
            if row is None:
                db.commit()
                return None
            if row["attempt_count"] >= self.max_recoveries:
                db.execute("""
                    UPDATE durable_task_jobs SET state='failed', error=?, updated_at=?, lease_until=NULL, worker_id=NULL
                    WHERE task_id=?
                """, ("Se agotó el límite de recuperaciones del trabajo.", now, row["task_id"]))
                db.commit()
                return None
            changed = db.execute("""
                UPDATE durable_task_jobs
                SET state='running', attempt_count=attempt_count+1, updated_at=?, lease_until=?, worker_id=?, error=NULL
                WHERE task_id=? AND (state='queued' OR (state='running' AND lease_until < ?))
            """, (now, now + self.lease_seconds, worker_id, row["task_id"], now)).rowcount
            if not changed:
                db.commit()
                return None
            claimed = db.execute("SELECT * FROM durable_task_jobs WHERE task_id=?", (row["task_id"],)).fetchone()
            db.commit()
            return {
                "task_id": claimed["task_id"], "goal": claimed["goal"],
                "constraints": json.loads(claimed["constraints_json"]),
                "success_criteria": json.loads(claimed["criteria_json"]),
                "namespace": claimed["namespace"], "idempotency_key": claimed["idempotency_key"],
                "worker_id": worker_id,
            }

    def _heartbeat(self, task_id: str, worker_id: str):
        now = time.time()
        with self._connect() as db:
            db.execute("""
                UPDATE durable_task_jobs SET lease_until=?, updated_at=?
                WHERE task_id=? AND state='running' AND worker_id=?
            """, (now + self.lease_seconds, now, task_id, worker_id))

    def _finish(self, task_id: str, worker_id: str, state: str, result=None, error=None):
        now = time.time()
        with self._connect() as db:
            db.execute("""
                UPDATE durable_task_jobs
                SET state=?, result_json=?, error=?, updated_at=?, lease_until=NULL, worker_id=NULL
                WHERE task_id=? AND state='running' AND worker_id=?
            """, (state, json.dumps(result, ensure_ascii=False) if result is not None else None,
                  str(error)[:1000] if error else None, now, task_id, worker_id))

    def _run_job(self, job: dict[str, Any]):
        task_id, worker_id = job["task_id"], job["worker_id"]
        heartbeat_stop = threading.Event()

        def heartbeat_loop():
            while not heartbeat_stop.wait(max(5.0, self.lease_seconds / 3)):
                try:
                    self._heartbeat(task_id, worker_id)
                except Exception:
                    logger.exception("Durable task lease heartbeat failed for %s", task_id)

        heartbeat = threading.Thread(target=heartbeat_loop, name=f"elyntro-heartbeat-{task_id[:8]}", daemon=True)
        heartbeat.start()
        try:
            result = self.handler(job)
            state = str(result.get("queue_state", "succeeded"))
            if state not in {"succeeded", "needs_approval", "failed", "cancelled"}:
                state = "failed"
            self._finish(task_id, worker_id, state, result=result)
        except Exception as exc:
            logger.exception("Durable task worker failed for %s", task_id)
            self._finish(task_id, worker_id, "failed", error=f"{type(exc).__name__}: {exc}")
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)

    def _run(self):
        while not self._stop.is_set():
            try:
                job = self._claim()
                if job:
                    self._run_job(job)
                else:
                    self._stop.wait(self.poll_seconds)
            except Exception:
                logger.exception("Durable task queue loop error")
                self._stop.wait(self.poll_seconds)
