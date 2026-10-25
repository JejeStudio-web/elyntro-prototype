"""Read-only operational audit for Elyntro's persistent runtime ledgers.

The auditor reports conditions requiring attention but never replays, mutates, or
silently repairs work. It is intended for local single-host SQLite deployments.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .persistent_jobs import PersistentJobStore
from .reliable_runtime import ExecutionLedger


@dataclass(frozen=True)
class HealthFinding:
    severity: str  # info | warning | critical
    code: str
    subject_id: str
    message: str
    recommended_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "subject_id": self.subject_id,
            "message": self.message,
            "recommended_action": self.recommended_action,
        }


@dataclass(frozen=True)
class RuntimeHealthReport:
    checked_at: str
    runtime_runs_checked: int
    jobs_checked: int
    findings: tuple[HealthFinding, ...]

    @property
    def healthy(self) -> bool:
        return not any(f.severity in {"warning", "critical"} for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "checked_at": self.checked_at,
            "runtime_runs_checked": self.runtime_runs_checked,
            "jobs_checked": self.jobs_checked,
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


class RuntimeHealthAuditor:
    """Inspect recent model runs and durable jobs without changing their state."""

    def __init__(self, ledger: ExecutionLedger, jobs: PersistentJobStore | None = None) -> None:
        self.ledger = ledger
        self.jobs = jobs

    @staticmethod
    def _age_seconds(timestamp: str, now: datetime) -> float:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())

    def audit(self, *, stale_after_seconds: float = 300.0, limit: int = 200) -> RuntimeHealthReport:
        if stale_after_seconds < 0:
            raise ValueError("stale_after_seconds must be non-negative")
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        now = datetime.now(timezone.utc)
        findings: list[HealthFinding] = []
        runs = self.ledger.list_runs(limit=min(limit, 1000))
        for run in runs:
            status = run.get("status")
            run_id = str(run.get("run_id", "unknown"))
            if status == "running":
                try:
                    age = self._age_seconds(str(run["created_at"]), now)
                except (ValueError, KeyError, TypeError):
                    findings.append(HealthFinding("critical", "RUN_TIMESTAMP_INVALID", run_id,
                        "Running execution has an invalid creation timestamp.",
                        "Inspect the ledger record and reconcile it manually."))
                    continue
                if age >= stale_after_seconds:
                    findings.append(HealthFinding("critical", "RUN_STALE", run_id,
                        f"Execution has remained running for {int(age)} seconds.",
                        "Inspect provider logs and reconcile before retrying; do not replay blindly."))
                else:
                    findings.append(HealthFinding("info", "RUN_IN_PROGRESS", run_id,
                        "Execution is currently marked running.",
                        "No action is required unless progress stops."))
            elif status == "interrupted":
                findings.append(HealthFinding("warning", "RUN_INTERRUPTED", run_id,
                    "Execution was interrupted and was not automatically replayed.",
                    "Inspect attempt history and provider usage before deciding whether to resubmit."))
            elif status in {"needs_classification", "needs_evaluation"}:
                findings.append(HealthFinding("warning", "RUN_REVIEW_REQUIRED", run_id,
                    f"Execution requires follow-up ({status}).",
                    "Complete the required classification or evaluation before treating the run as accepted."))

        jobs_checked = 0
        if self.jobs is not None:
            jobs = self.jobs.list_jobs(limit=limit)
            jobs_checked = len(jobs)
            for item in jobs:
                job_id = str(item.get("job_id", "unknown"))
                try:
                    job = self.jobs.get_job(job_id)
                except (KeyError, ValueError):
                    findings.append(HealthFinding("critical", "JOB_UNREADABLE", job_id,
                        "Job appeared in the index but could not be read.",
                        "Inspect the SQLite database before any recovery operation."))
                    continue
                if job.get("state") == "interrupted":
                    findings.append(HealthFinding("warning", "JOB_INTERRUPTED", job_id,
                        "Durable job is interrupted and may contain uncertain side effects.",
                        "Review step states and external-system records before resuming."))
                uncertain = [s.get("step_id", "unknown") for s in job.get("steps", []) if s.get("state") == "uncertain"]
                if uncertain:
                    findings.append(HealthFinding("critical", "STEP_OUTCOME_UNCERTAIN", job_id,
                        "One or more non-idempotent steps have an uncertain outcome: " + ", ".join(map(str, uncertain)) + ".",
                        "Reconcile each step against its external side effects; never auto-replay uncertain steps."))
                if job.get("state") == "waiting_approval":
                    findings.append(HealthFinding("info", "JOB_AWAITING_APPROVAL", job_id,
                        "Job is waiting for human approval.",
                        "Review the requested action and approve or reject it explicitly."))
        findings.sort(key=lambda f: ({"critical": 0, "warning": 1, "info": 2}[f.severity], f.code, f.subject_id))
        return RuntimeHealthReport(now.isoformat(), len(runs), jobs_checked, tuple(findings))
