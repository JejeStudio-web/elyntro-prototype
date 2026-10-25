"""Read-only reconciliation for interrupted recovery cycles.

The reconciler reports inconsistencies and can optionally recover expired leases
using the job store's conservative policy. It never replays external side effects
or executes approved recovery proposals automatically.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .persistent_jobs import PersistentJobStore
from .persistent_recovery import PersistentRecoveryService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReconciliationFinding:
    kind: str
    reference_id: str
    severity: str
    action_required: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "reference_id": self.reference_id,
            "severity": self.severity,
            "action_required": self.action_required,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReconciliationReport:
    started_at: str
    finished_at: str
    findings: tuple[ReconciliationFinding, ...]
    recovered_leases: tuple[dict[str, Any], ...]

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "clean": self.clean,
            "finding_count": len(self.findings),
            "findings": [item.to_dict() for item in self.findings],
            "recovered_leases": list(self.recovered_leases),
        }


class RecoveryReconciler:
    """Audit job/recovery state after crashes and report safe next actions.

    `recover_expired_leases=True` invokes only PersistentJobStore's built-in policy:
    idempotent running steps may return to pending; non-idempotent steps become
    uncertain and require operator reconciliation. Approved proposals without a
    cycle record are never executed automatically.
    """

    def __init__(self, store: PersistentJobStore, recovery: PersistentRecoveryService) -> None:
        if store.path != recovery.store.path:
            raise ValueError("store and recovery service must share the same SQLite database")
        self.store = store
        self.recovery = recovery
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reconciliation_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                finding_count INTEGER NOT NULL,
                recovered_count INTEGER NOT NULL,
                report_json TEXT NOT NULL
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.store.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def reconcile(self, *, recover_expired_leases: bool = False) -> ReconciliationReport:
        if not isinstance(recover_expired_leases, bool):
            raise ValueError("recover_expired_leases must be a boolean")
        started = _now()
        recovered: list[dict[str, Any]] = []
        findings: list[ReconciliationFinding] = []

        if recover_expired_leases:
            recovered = self.store.recover_expired_leases()
            for item in recovered:
                if item["state"] == "interrupted":
                    findings.append(ReconciliationFinding(
                        "uncertain_step_after_lease_expiry", item["job_id"], "high",
                        "manual_reconciliation",
                        "At least one non-idempotent step has an uncertain external outcome; do not replay it without evidence.",
                    ))

        # Snapshot all job identifiers directly from SQLite. The public list_jobs API
        # is intentionally capped at 500 for UI use; using it here would create false
        # "missing parent" findings for older jobs in a larger database.
        with self._connect() as db:
            jobs = {row["job_id"]: dict(row) for row in db.execute(
                "SELECT job_id,state,task_type,created_at,updated_at FROM jobs"
            ).fetchall()}
            proposals = db.execute(
                "SELECT proposal_id,job_id,status FROM recovery_proposals ORDER BY created_at"
            ).fetchall()
            cycle_rows = db.execute(
                "SELECT proposal_id,original_job_id,resulting_job_id,status FROM recovery_cycle_runs"
            ).fetchall() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_cycle_runs'"
            ).fetchone() else []
            replan_rows = db.execute(
                "SELECT proposal_id,event_type,detail FROM recovery_events WHERE event_type='replan_workflow_created'"
            ).fetchall()

        cycles = {row["proposal_id"]: dict(row) for row in cycle_rows}
        replan_child_by_proposal: dict[str, str] = {}
        for row in replan_rows:
            try:
                child_id = json.loads(row["detail"]).get("child_job_id")
            except (TypeError, json.JSONDecodeError):
                child_id = None
            if child_id:
                replan_child_by_proposal[row["proposal_id"]] = child_id

        for row in proposals:
            pid, jid, status = row["proposal_id"], row["job_id"], row["status"]
            if jid not in jobs:
                findings.append(ReconciliationFinding(
                    "proposal_parent_job_missing", pid, "critical", "manual_investigation",
                    "A recovery proposal references a job not present in the visible job ledger.",
                ))
                continue
            cycle = cycles.get(pid)
            child_id = replan_child_by_proposal.get(pid)
            if status == "approved_for_manual_resume" and cycle is None and child_id is None:
                findings.append(ReconciliationFinding(
                    "approved_proposal_without_cycle_record", pid, "medium", "inspect_before_resume",
                    "The proposal is approved but has no recorded recovery-cycle result or replan child event. It was not executed by reconciliation.",
                ))
            if child_id is not None and child_id not in jobs:
                # list_jobs is capped; verify directly before reporting missing.
                try:
                    self.store.get_job(child_id)
                except KeyError:
                    findings.append(ReconciliationFinding(
                        "replan_child_missing", pid, "critical", "manual_investigation",
                        f"Recovery audit refers to child job {child_id!r}, but the job cannot be found.",
                    ))
            if cycle is not None and cycle.get("resulting_job_id"):
                child = cycle["resulting_job_id"]
                try:
                    child_job = self.store.get_job(child)
                except KeyError:
                    findings.append(ReconciliationFinding(
                        "cycle_result_job_missing", pid, "critical", "manual_investigation",
                        f"Recovery cycle records resulting job {child!r}, but it cannot be found.",
                    ))
                else:
                    if cycle["status"] in {"succeeded_verified", "succeeded_unverified"} and child_job["state"] not in {"succeeded", "queued", "running", "waiting_approval"}:
                        findings.append(ReconciliationFinding(
                            "cycle_status_disagrees_with_job", pid, "high", "refresh_recovery_result",
                            f"Recorded cycle status is {cycle['status']!r}, but resulting job state is {child_job['state']!r}.",
                        ))

        finished = _now()
        report = ReconciliationReport(started, finished, tuple(findings), tuple(recovered))
        with self._connect() as db:
            db.execute(
                "INSERT INTO reconciliation_runs(started_at,finished_at,finding_count,recovered_count,report_json) VALUES(?,?,?,?,?)",
                (started, finished, len(findings), len(recovered), json.dumps(report.to_dict(), sort_keys=True)),
            )
        return report

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer between 1 and 200")
        with self._connect() as db:
            rows = db.execute(
                "SELECT run_id,started_at,finished_at,finding_count,recovered_count,report_json FROM reconciliation_runs ORDER BY run_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["report"] = json.loads(item.pop("report_json"))
            result.append(item)
        return result
