"""End-to-end, auditable facade for reviewed persistent recovery cycles.

This facade does not bypass policy. It dispatches only proposals that have already
been reviewed, delegates execution to PersistentRecoveryService, and classifies the
result using the durable job state plus its configured verifier report.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from .persistent_jobs import PersistentJobStore
from .persistent_recovery import PersistentRecoveryService
from .planner import RuleBasedPlanner, ToolDefinition


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RecoveryCycleResult:
    proposal_id: str
    original_job_id: str
    action: str
    status: str
    resulting_job_id: str | None
    execution_succeeded: bool
    verification_passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "original_job_id": self.original_job_id,
            "action": self.action,
            "status": self.status,
            "resulting_job_id": self.resulting_job_id,
            "execution_succeeded": self.execution_succeeded,
            "verification_passed": self.verification_passed,
            "detail": self.detail,
        }


class RecoveryCycleCoordinator:
    """Run one explicitly reviewed recovery decision and record its outcome.

    `continue` invokes the bounded retry path; `replan` invokes the approved replan
    path. `stop` and `needs_approval` remain non-executable. This is an orchestration
    facade, not a distributed transaction or a guarantee of exactly-once side effects.
    """

    def __init__(self, store: PersistentJobStore, recovery: PersistentRecoveryService) -> None:
        if store.path != recovery.store.path:
            raise ValueError("store and recovery service must share the same SQLite database")
        self.store = store
        self.recovery = recovery
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS recovery_cycle_runs (
                proposal_id TEXT PRIMARY KEY,
                original_job_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                resulting_job_id TEXT,
                execution_succeeded INTEGER NOT NULL,
                verification_passed INTEGER NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.store.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def get_result(self, proposal_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recovery_cycle_runs WHERE proposal_id=?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        data = dict(row)
        data["execution_succeeded"] = bool(data["execution_succeeded"])
        data["verification_passed"] = bool(data["verification_passed"])
        return data

    def execute_reviewed(self, proposal_id: str, *, workflow_service: Any, approved_by: str,
                         planner: RuleBasedPlanner | None = None,
                         tools: Sequence[ToolDefinition] = (), workflow_adapter: Any = None,
                         idempotency_key: str | None = None) -> RecoveryCycleResult:
        """Dispatch a reviewed proposal, then persist the observed terminal outcome."""
        # A repeat call returns the recorded outcome rather than intentionally rerunning.
        try:
            prior = self.get_result(proposal_id)
        except KeyError:
            prior = None
        if prior is not None:
            return RecoveryCycleResult(
                proposal_id=prior["proposal_id"], original_job_id=prior["original_job_id"],
                action=prior["action"], status=prior["status"],
                resulting_job_id=prior["resulting_job_id"],
                execution_succeeded=prior["execution_succeeded"],
                verification_passed=prior["verification_passed"], detail=prior["detail"],
            )

        record = self.recovery.get_proposal(proposal_id)
        if record["status"] != "approved_for_manual_resume":
            raise ValueError("Recovery proposal must be explicitly approved before dispatch")
        proposal = record["proposal"]
        action = proposal.get("decision", {}).get("action", "unknown")
        original_job_id = record["job_id"]
        if action == "continue":
            job = self.recovery.execute_approved_retry(
                proposal_id, workflow_service, approved_by=approved_by,
            )
        elif action == "replan":
            if planner is None or workflow_adapter is None or not idempotency_key:
                raise ValueError("replan requires planner, workflow_adapter and a fresh idempotency_key")
            job = self.recovery.execute_approved_replan(
                proposal_id, planner=planner, tools=tools, workflow_adapter=workflow_adapter,
                workflow_service=workflow_service, idempotency_key=idempotency_key,
                approved_by=approved_by,
            )
        else:
            raise ValueError(f"Recovery action {action!r} is not executable")

        resulting_job_id = job.get("job_id") if isinstance(job, dict) else None
        state = str(job.get("state", "unknown")) if isinstance(job, dict) else "unknown"
        verification = job.get("verification", {}) if isinstance(job, dict) else {}
        verified = verification.get("passed") is True
        succeeded = state == "succeeded"
        if succeeded and verified:
            status, detail = "succeeded_verified", "Resulting durable job succeeded and its configured verifier passed."
        elif succeeded:
            status, detail = "succeeded_unverified", "Job reached succeeded state but no passing verifier result was present."
        elif state in {"queued", "waiting_approval", "running"}:
            status, detail = "pending", f"Recovery workflow remains in non-terminal state {state!r}."
        else:
            status, detail = "failed_or_blocked", f"Recovery workflow ended in state {state!r}; inspect job error and verification evidence."

        # A unique proposal_id prevents duplicate audit rows. SQLite insert is atomic;
        # this is not atomic with the external workflow execution itself.
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""INSERT INTO recovery_cycle_runs
                (proposal_id,original_job_id,action,status,resulting_job_id,execution_succeeded,
                 verification_passed,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (proposal_id, original_job_id, action, status, resulting_job_id,
                 int(succeeded), int(verified), detail, _utc_now()))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return RecoveryCycleResult(proposal_id, original_job_id, action, status,
                                   resulting_job_id, succeeded, verified, detail)
