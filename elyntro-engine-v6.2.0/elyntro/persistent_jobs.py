"""Persistent local job queue with explicit safe-resume semantics.

This is a single-host SQLite prototype. It stores job/step state and only resets an
interrupted in-flight step to pending when the application declared it idempotent.
Non-idempotent in-flight steps become `uncertain` and require manual reconciliation.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

JOB_STATES = {"queued", "running", "waiting_approval", "succeeded", "failed", "interrupted"}
STEP_STATES = {"pending", "running", "succeeded", "failed", "uncertain", "skipped"}
TERMINAL_JOB_STATES = {"succeeded", "failed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class JobStep:
    step_id: str
    handler_key: str
    inputs: dict[str, Any]
    idempotent: bool = False
    requires_approval: bool = False

    def validate(self) -> None:
        if not self.step_id or len(self.step_id) > 200:
            raise ValueError("step_id must contain 1–200 characters")
        if not self.handler_key or len(self.handler_key) > 200:
            raise ValueError("handler_key must contain 1–200 characters")
        if not isinstance(self.inputs, dict):
            raise ValueError("step inputs must be a dictionary")
        if not isinstance(self.idempotent, bool) or not isinstance(self.requires_approval, bool):
            raise ValueError("idempotent and requires_approval must be booleans")


class PersistentJobStore:
    """Durable job queue and step ledger backed by SQLite."""

    def __init__(self, path: str | Path = "elyntro_jobs.db") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                task_type TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                lease_owner TEXT, lease_until TEXT, result TEXT, error TEXT,
                verification TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_state_created ON jobs(state, created_at);
            CREATE TABLE IF NOT EXISTS job_steps (
                job_id TEXT NOT NULL, step_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                handler_key TEXT NOT NULL, inputs TEXT NOT NULL, idempotent INTEGER NOT NULL,
                requires_approval INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, output TEXT, error TEXT,
                started_at TEXT, finished_at TEXT,
                PRIMARY KEY(job_id, step_id), UNIQUE(job_id, ordinal),
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS job_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                event_type TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id,event_id);
            """)
        finally:
            db.close()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _event(db: sqlite3.Connection, job_id: str, event_type: str, detail: dict[str, Any]) -> None:
        db.execute("INSERT INTO job_events(job_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                   (job_id, event_type, _json(detail), _now()))

    def create_job(self, *, idempotency_key: str, task_type: str, payload: dict[str, Any],
                   steps: list[JobStep], require_approval: bool = False) -> str:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1–200 non-whitespace characters")
        if not isinstance(task_type, str) or not task_type.strip() or len(task_type) > 100:
            raise ValueError("task_type must contain 1–100 characters")
        if not isinstance(payload, dict) or not isinstance(steps, list) or not steps:
            raise ValueError("payload must be a dictionary and steps must be a non-empty list")
        if not isinstance(require_approval, bool):
            raise ValueError("require_approval must be a boolean")
        ids: set[str] = set()
        for step in steps:
            if not isinstance(step, JobStep):
                raise ValueError("steps must contain JobStep objects")
            step.validate()
            if step.step_id in ids:
                raise ValueError("step IDs must be unique")
            ids.add(step.step_id)
        job_id = str(uuid.uuid4())
        now = _now()
        state = "waiting_approval" if require_approval or any(s.requires_approval for s in steps) else "queued"
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO jobs(job_id,idempotency_key,task_type,payload,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                       (job_id, idempotency_key, task_type.strip(), _json(payload), state, now, now))
            for ordinal, step in enumerate(steps):
                db.execute("INSERT INTO job_steps(job_id,step_id,ordinal,handler_key,inputs,idempotent,requires_approval) VALUES(?,?,?,?,?,?,?)",
                           (job_id, step.step_id, ordinal, step.handler_key, _json(step.inputs), int(step.idempotent), int(step.requires_approval)))
            self._event(db, job_id, "job_created", {"state": state, "step_count": len(steps), "task_type": task_type.strip()})
            db.execute("COMMIT")
            return job_id
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def approve(self, job_id: str, *, approved_by: str) -> None:
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
            raise ValueError("approved_by must contain 1–200 characters")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != "waiting_approval":
                raise ValueError("Only jobs waiting for approval can be approved")
            db.execute("UPDATE jobs SET state='queued',updated_at=? WHERE job_id=?", (_now(), job_id))
            self._event(db, job_id, "job_approved", {"approved_by": approved_by.strip()})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def claim_next(self, *, worker_id: str, lease_seconds: float = 60.0, job_id: str | None = None) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
            raise ValueError("worker_id must contain 1–200 characters")
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        lease_until = (now_dt + timedelta(seconds=lease_seconds)).isoformat()
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            if job_id is None:
                row = db.execute("SELECT job_id FROM jobs WHERE state='queued' ORDER BY created_at,job_id LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT job_id FROM jobs WHERE state='queued' AND job_id=?", (job_id,)).fetchone()
            if row is None:
                db.execute("COMMIT")
                return None
            job_id = row["job_id"]
            db.execute("UPDATE jobs SET state='running',lease_owner=?,lease_until=?,updated_at=? WHERE job_id=? AND state='queued'",
                       (worker_id.strip(), lease_until, now, job_id))
            self._event(db, job_id, "job_claimed", {"worker_id": worker_id.strip(), "lease_until": lease_until})
            db.execute("COMMIT")
            return self.get_job(job_id)
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def start_step(self, job_id: str, step_id: str, *, worker_id: str) -> dict[str, Any]:
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state,lease_owner,lease_until FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "running" or job["lease_owner"] != worker_id:
                raise ValueError("Worker does not own a running job lease")
            if not job["lease_until"] or job["lease_until"] <= _now():
                raise ValueError("Worker lease has expired")
            row = db.execute("SELECT * FROM job_steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone()
            if row is None:
                raise KeyError(step_id)
            if row["state"] != "pending":
                raise ValueError(f"Step cannot start from state {row['state']}")
            now = _now()
            db.execute("UPDATE job_steps SET state='running',attempts=attempts+1,started_at=?,error=NULL WHERE job_id=? AND step_id=?",
                       (now, job_id, step_id))
            db.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (now, job_id))
            self._event(db, job_id, "step_started", {"step_id": step_id, "worker_id": worker_id})
            db.execute("COMMIT")
            return dict(db.execute("SELECT * FROM job_steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone())
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def finish_step(self, job_id: str, step_id: str, *, output: dict[str, Any]) -> None:
        if not isinstance(output, dict):
            raise ValueError("output must be a dictionary")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM job_steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone()
            if row is None:
                raise KeyError(step_id)
            if row["state"] != "running":
                raise ValueError("Only running steps can be completed")
            now = _now()
            db.execute("UPDATE job_steps SET state='succeeded',output=?,finished_at=?,error=NULL WHERE job_id=? AND step_id=?",
                       (_json(output), now, job_id, step_id))
            db.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (now, job_id))
            self._event(db, job_id, "step_succeeded", {"step_id": step_id})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def record_step_retry(self, job_id: str, step_id: str, *, worker_id: str, error: str) -> None:
        """Record a retry decision without leaving the current step's running state.

        Call only when the handler is declared idempotent and the failure is classified
        as transient by the workflow service.
        """
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state,lease_owner,lease_until FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "running" or job["lease_owner"] != worker_id:
                raise ValueError("Worker does not own a running job lease")
            if not job["lease_until"] or job["lease_until"] <= _now():
                raise ValueError("Worker lease has expired")
            row = db.execute("SELECT state,idempotent,attempts FROM job_steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone()
            if row is None:
                raise KeyError(step_id)
            if row["state"] != "running" or not row["idempotent"]:
                raise ValueError("Only running idempotent steps can be retried")
            now = _now()
            db.execute("UPDATE job_steps SET attempts=attempts+1,error=? WHERE job_id=? AND step_id=?",
                       (error[:1000], job_id, step_id))
            db.execute("UPDATE jobs SET updated_at=? WHERE job_id=?", (now, job_id))
            self._event(db, job_id, "step_retry_scheduled", {"step_id": step_id, "attempt": row["attempts"] + 1, "error": error[:300]})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def fail_step(self, job_id: str, step_id: str, *, error: str) -> None:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty")
        db = self._connect()
        try:
            with db:
                row = db.execute("SELECT state FROM job_steps WHERE job_id=? AND step_id=?", (job_id, step_id)).fetchone()
                if row is None:
                    raise KeyError(step_id)
                if row["state"] != "running":
                    raise ValueError("Only running steps can fail")
                db.execute("UPDATE job_steps SET state='failed',error=?,finished_at=? WHERE job_id=? AND step_id=?",
                           (error[:1000], _now(), job_id, step_id))
                db.execute("UPDATE jobs SET state='failed',error=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",
                           (error[:1000], _now(), job_id))
                self._event(db, job_id, "step_failed", {"step_id": step_id, "error": error[:300]})
        finally:
            db.close()

    def prepare_failed_step_retry(self, job_id: str, step_id: str, *, proposal_id: str, approved_by: str) -> dict[str, Any]:
        """Atomically requeue one failed idempotent step after explicit recovery approval.

        Previously successful steps are retained. The method does not execute the job;
        it only changes durable state, and refuses uncertain, non-idempotent or stale jobs.
        """
        for value, label, limit in ((proposal_id, "proposal_id", 200), (approved_by, "approved_by", 200)):
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError(f"{label} must contain 1–{limit} characters")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "failed":
                raise ValueError("Only failed jobs can use failed-step retry recovery")
            step = db.execute("SELECT state,idempotent FROM job_steps WHERE job_id=? AND step_id=?",
                              (job_id, step_id)).fetchone()
            if step is None:
                raise KeyError(step_id)
            if step["state"] != "failed":
                raise ValueError("The selected step must be in failed state")
            if not step["idempotent"]:
                raise ValueError("Non-idempotent steps cannot be automatically retried")
            unsafe = db.execute("SELECT COUNT(*) AS n FROM job_steps WHERE job_id=? AND state IN ('running','uncertain')",
                                (job_id,)).fetchone()["n"]
            if unsafe:
                raise ValueError("Job contains running or uncertain steps and requires reconciliation")
            db.execute("UPDATE job_steps SET state='pending',started_at=NULL,finished_at=NULL,error=NULL "
                       "WHERE job_id=? AND step_id=?", (job_id, step_id))
            pending_approval = db.execute(
                "SELECT COUNT(*) AS n FROM job_steps WHERE job_id=? AND state='pending' AND requires_approval=1",
                (job_id,)).fetchone()["n"]
            next_state = "waiting_approval" if pending_approval else "queued"
            now = _now()
            db.execute("UPDATE jobs SET state=?,error=NULL,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",
                       (next_state, now, job_id))
            self._event(db, job_id, "approved_recovery_retry_queued", {
                "step_id": step_id, "proposal_id": proposal_id,
                "approved_by": approved_by.strip(), "next_state": next_state,
                "successful_steps_preserved": True,
            })
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()
        return self.get_job(job_id)

    def pause_for_approval(self, job_id: str, *, detail: dict[str, Any]) -> None:
        """Pause a running job after its step has persisted an approval-required result."""
        if not isinstance(detail, dict):
            raise ValueError("detail must be a dictionary")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "running":
                raise ValueError("Only running jobs can pause for approval")
            counts = db.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN state='succeeded' THEN 1 ELSE 0 END) AS ok FROM job_steps WHERE job_id=?", (job_id,)).fetchone()
            if counts["total"] == 0 or counts["total"] != counts["ok"]:
                raise ValueError("All queue steps must be persisted before pausing for approval")
            db.execute("UPDATE jobs SET state='waiting_approval',updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?", (_now(), job_id))
            self._event(db, job_id, "job_waiting_approval", detail)
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def fail_job(self, job_id: str, *, error: str) -> None:
        """Mark a running job failed, including when its step has already returned a result."""
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must be non-empty")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["state"] != "running":
                raise ValueError("Only running jobs can be failed")
            db.execute("UPDATE jobs SET state='failed',error=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?", (error[:1000], _now(), job_id))
            self._event(db, job_id, "job_failed", {"error": error[:300]})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def complete_job(self, job_id: str, *, result: dict[str, Any], verification: dict[str, Any]) -> None:
        if not isinstance(result, dict) or not isinstance(verification, dict):
            raise ValueError("result and verification must be dictionaries")
        if verification.get("passed") is not True or not isinstance(verification.get("verifier_id"), str) or not verification["verifier_id"].strip():
            raise ValueError("Completion requires a passing verification with verifier_id")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "running":
                raise ValueError("Only running jobs can be completed")
            counts = db.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN state='succeeded' THEN 1 ELSE 0 END) AS ok FROM job_steps WHERE job_id=?", (job_id,)).fetchone()
            if counts["total"] == 0 or counts["total"] != counts["ok"]:
                raise ValueError("All job steps must succeed before job completion")
            now = _now()
            db.execute("UPDATE jobs SET state='succeeded',result=?,verification=?,updated_at=?,lease_owner=NULL,lease_until=NULL WHERE job_id=?",
                       (_json(result), _json(verification), now, job_id))
            self._event(db, job_id, "job_succeeded", {"verifier_id": verification["verifier_id"]})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def recover_expired_leases(self) -> list[dict[str, Any]]:
        """Interrupt expired jobs; safely requeue only idempotent in-flight steps."""
        now = _now()
        db = self._connect()
        recovered: list[dict[str, Any]] = []
        try:
            db.execute("BEGIN IMMEDIATE")
            jobs = db.execute("SELECT job_id FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
            for job in jobs:
                job_id = job["job_id"]
                running = db.execute("SELECT step_id,idempotent FROM job_steps WHERE job_id=? AND state='running'", (job_id,)).fetchall()
                safe_steps, uncertain_steps = [], []
                for step in running:
                    if step["idempotent"]:
                        db.execute("UPDATE job_steps SET state='pending',started_at=NULL WHERE job_id=? AND step_id=?", (job_id, step["step_id"]))
                        safe_steps.append(step["step_id"])
                    else:
                        db.execute("UPDATE job_steps SET state='uncertain',error=? WHERE job_id=? AND step_id=?",
                                   ("Worker lease expired during non-idempotent step; reconcile external effects before retry", job_id, step["step_id"]))
                        uncertain_steps.append(step["step_id"])
                # Preserve already completed steps. Jobs with uncertain steps stay interrupted;
                # jobs whose in-flight steps are all safe can be requeued for resumption.
                state = "interrupted" if uncertain_steps else "queued"
                db.execute("UPDATE jobs SET state=?,lease_owner=NULL,lease_until=NULL,updated_at=?,error=? WHERE job_id=?",
                           (state, now, "Non-idempotent step outcome requires reconciliation" if uncertain_steps else None, job_id))
                self._event(db, job_id, "lease_recovered", {"state": state, "safe_steps_requeued": safe_steps, "uncertain_steps": uncertain_steps})
                recovered.append({"job_id": job_id, "state": state, "safe_steps_requeued": safe_steps, "uncertain_steps": uncertain_steps})
            db.execute("COMMIT")
            return recovered
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def resume_interrupted(self, job_id: str, *, reconciled_steps: dict[str, dict[str, Any]] | None = None) -> None:
        """Resume after operator reconciliation; uncertain steps need explicit outcomes.

        `reconciled_steps` maps uncertain step IDs to either {"succeeded": true,
        "output": {...}} or {"succeeded": false, "retry_safe": true}. A step is never
        retried merely because an operator called this method.
        """
        reconciled_steps = reconciled_steps or {}
        if not isinstance(reconciled_steps, dict):
            raise ValueError("reconciled_steps must be a dictionary")
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if job["state"] != "interrupted":
                raise ValueError("Only interrupted jobs can be resumed")
            uncertain = db.execute("SELECT step_id FROM job_steps WHERE job_id=? AND state='uncertain'", (job_id,)).fetchall()
            expected = {r["step_id"] for r in uncertain}
            if expected != set(reconciled_steps):
                raise ValueError("Every uncertain step must have an explicit reconciliation outcome")
            for step_id, outcome in reconciled_steps.items():
                if not isinstance(outcome, dict):
                    raise ValueError("each reconciliation outcome must be a dictionary")
                if outcome.get("succeeded") is True:
                    output = outcome.get("output")
                    if not isinstance(output, dict):
                        raise ValueError("successful reconciliation requires an output dictionary")
                    db.execute("UPDATE job_steps SET state='succeeded',output=?,finished_at=?,error=NULL WHERE job_id=? AND step_id=?",
                               (_json(output), _now(), job_id, step_id))
                elif outcome.get("succeeded") is False and outcome.get("retry_safe") is True:
                    db.execute("UPDATE job_steps SET state='pending',started_at=NULL,error=NULL WHERE job_id=? AND step_id=?",
                               (job_id, step_id))
                else:
                    raise ValueError("uncertain step must be confirmed succeeded or explicitly marked retry_safe")
            failed = db.execute("SELECT COUNT(*) AS n FROM job_steps WHERE job_id=? AND state='failed'", (job_id,)).fetchone()["n"]
            if failed:
                raise ValueError("A job with failed steps cannot be resumed without a separate repair workflow")
            now = _now()
            db.execute("UPDATE jobs SET state='queued',error=NULL,updated_at=? WHERE job_id=?", (now, job_id))
            self._event(db, job_id, "job_resumed", {"reconciled_steps": sorted(expected)})
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def get_job(self, job_id: str) -> dict[str, Any]:
        db = self._connect()
        try:
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            job = dict(row)
            for key in ("payload", "result", "verification"):
                value = job.get(key)
                job[key] = json.loads(value) if value else ({} if key != "result" else None)
            job["steps"] = []
            for step in db.execute("SELECT * FROM job_steps WHERE job_id=? ORDER BY ordinal", (job_id,)).fetchall():
                item = dict(step)
                item["inputs"] = json.loads(item["inputs"])
                item["output"] = json.loads(item["output"]) if item["output"] else None
                item["idempotent"] = bool(item["idempotent"])
                item["requires_approval"] = bool(item["requires_approval"])
                job["steps"].append(item)
            job["events"] = [{**dict(e), "detail": json.loads(e["detail"])} for e in db.execute(
                "SELECT event_id,event_type,detail,created_at FROM job_events WHERE job_id=? ORDER BY event_id", (job_id,)).fetchall()]
            return job
        finally:
            db.close()

    def list_jobs(self, *, state: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if state is not None and state not in JOB_STATES:
            raise ValueError("invalid job state")
        db = self._connect()
        try:
            if state:
                rows = db.execute("SELECT job_id,state,task_type,created_at,updated_at FROM jobs WHERE state=? ORDER BY created_at DESC LIMIT ?", (state, limit)).fetchall()
            else:
                rows = db.execute("SELECT job_id,state,task_type,created_at,updated_at FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            db.close()


class TaskVerifierRegistry:
    """Task-type-specific result validators; missing validators fail closed."""
    def __init__(self) -> None:
        self._validators: dict[str, tuple[str, Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]]] = {}

    def register(self, task_type: str, verifier_id: str,
                 validator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]) -> None:
        if not task_type.strip() or not verifier_id.strip() or not callable(validator):
            raise ValueError("task_type, verifier_id and callable validator are required")
        if task_type in self._validators:
            raise ValueError(f"validator already registered for task type {task_type}")
        self._validators[task_type] = (verifier_id, validator)

    def verify(self, task_type: str, result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        item = self._validators.get(task_type)
        if item is None:
            return {"passed": False, "verifier_id": "unconfigured", "reason": "No task-specific verifier is registered", "evidence": []}
        verifier_id, validator = item
        verdict = validator(result, context)
        if not isinstance(verdict, dict) or not isinstance(verdict.get("passed"), bool):
            raise ValueError("validator must return a dictionary with boolean 'passed'")
        return {**verdict, "verifier_id": verifier_id}
