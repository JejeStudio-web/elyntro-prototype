"""Persistent, auditable recovery proposals linked to durable workflow jobs.

This layer records diagnosis and human review in the same SQLite database as the job
ledger. Review approval is not execution authorization: a caller must still perform
its normal policy checks and explicitly resume/revise a workflow.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from .planner import ExecutionPlan, RuleBasedPlanner, ToolDefinition
from .persistent_jobs import PersistentJobStore
from .recovery import AdaptiveRecoveryCoordinator, RecoveryProposal


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersistentRecoveryService:
    """Store recovery proposals and review decisions durably alongside jobs."""

    def __init__(self, store: PersistentJobStore, coordinator: AdaptiveRecoveryCoordinator) -> None:
        self.store = store
        self.coordinator = coordinator
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS recovery_proposals (
                proposal_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('proposed','approved_for_manual_resume','rejected')),
                proposal_json TEXT NOT NULL,
                review_note TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_recovery_job_created ON recovery_proposals(job_id, created_at)")
            db.execute("""CREATE TABLE IF NOT EXISTS recovery_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id) REFERENCES recovery_proposals(proposal_id) ON DELETE CASCADE
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.store.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def propose_for_job(self, job_id: str, *, goal: str, current_plan: ExecutionPlan,
                        error: BaseException | str | None = None,
                        failed_step_id: str | None = None,
                        evidence_gaps: Sequence[str] = (),
                        approval_required: bool = False,
                        dependency_failed: bool = False,
                        tool_registered: bool | None = None,
                        permission_denied: bool = False,
                        constraints: Sequence[str] = (),
                        success_criteria: Sequence[str] = (),
                        revision_number: int = 0) -> tuple[str, RecoveryProposal]:
        job = self.store.get_job(job_id)
        successful = tuple(s["step_id"] for s in job["steps"] if s["state"] == "succeeded")
        step_by_id = {s["step_id"]: s for s in job["steps"]}
        if failed_step_id is not None and failed_step_id not in step_by_id:
            raise ValueError(f"failed_step_id is not part of job {job_id!r}")
        # Idempotency is read from the durable step declaration, never caller supplied.
        idempotent = bool(step_by_id[failed_step_id]["idempotent"]) if failed_step_id else False
        proposal = self.coordinator.propose(
            goal=goal, current_plan=current_plan, error=error,
            completed_step_ids=successful, failed_step_id=failed_step_id,
            evidence_gaps=evidence_gaps, approval_required=approval_required,
            dependency_failed=dependency_failed, tool_registered=tool_registered,
            permission_denied=permission_denied, operation_idempotent=idempotent,
            constraints=constraints, success_criteria=success_criteria,
            revision_number=revision_number,
        )
        proposal_id = str(uuid.uuid4())
        now = _now()
        encoded = json.dumps(proposal.to_dict(), ensure_ascii=False, sort_keys=True, allow_nan=False)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO recovery_proposals(proposal_id,job_id,created_at,status,proposal_json) VALUES(?,?,?,?,?)",
                       (proposal_id, job_id, now, "proposed", encoded))
            db.execute("INSERT INTO recovery_events(proposal_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                       (proposal_id, "proposal_created", json.dumps({"decision": proposal.decision.action,
                        "diagnosis": proposal.diagnosis.category.value}, sort_keys=True), now))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return proposal_id, proposal

    def review(self, proposal_id: str, *, approved: bool, reviewed_by: str, note: str = "") -> dict[str, Any]:
        if not isinstance(approved, bool):
            raise ValueError("approved must be a boolean")
        if not isinstance(reviewed_by, str) or not reviewed_by.strip() or len(reviewed_by) > 200:
            raise ValueError("reviewed_by must contain 1–200 characters")
        if not isinstance(note, str) or len(note) > 2000:
            raise ValueError("note must be a string of at most 2000 characters")
        new_status = "approved_for_manual_resume" if approved else "rejected"
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,job_id FROM recovery_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            if row["status"] != "proposed":
                raise ValueError("Only proposals awaiting review can be reviewed")
            now = _now()
            db.execute("UPDATE recovery_proposals SET status=?,review_note=?,reviewed_by=?,reviewed_at=? WHERE proposal_id=?",
                       (new_status, note, reviewed_by.strip(), now, proposal_id))
            detail = json.dumps({"approved": approved, "reviewed_by": reviewed_by.strip(), "note": note},
                                ensure_ascii=False, sort_keys=True)
            db.execute("INSERT INTO recovery_events(proposal_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                       (proposal_id, "proposal_reviewed", detail, now))
            db.commit()
            return self.get_proposal(proposal_id)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def execute_approved_retry(self, proposal_id: str, workflow_service: Any, *, approved_by: str) -> dict[str, Any]:
        """Execute a narrowly-scoped retry from an approved proposal.

        Only a diagnosed, retryable `continue` proposal for an idempotent failed step
        is eligible. Proposal approval and job-level approval remain separate gates.
        This method never applies a revised plan or retries non-idempotent side effects.
        """
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
            raise ValueError("approved_by must contain 1–200 characters")
        if not callable(getattr(workflow_service, "process_one", None)):
            raise TypeError("workflow_service must expose process_one(job_id=...)")
        record = self.get_proposal(proposal_id)
        if record["status"] != "approved_for_manual_resume":
            raise ValueError("Recovery proposal must be explicitly approved before execution")
        proposal = record["proposal"]
        diagnosis = proposal.get("diagnosis", {})
        decision = proposal.get("decision", {})
        feedback = proposal.get("feedback", {})
        step_id = feedback.get("failed_step_id")
        if decision.get("action") != "continue" or diagnosis.get("retryable") is not True:
            raise ValueError("Only retryable continue decisions may execute an automatic retry")
        if not proposal.get("operation_idempotent") or not step_id:
            raise ValueError("Recovery proposal does not authorize a safe idempotent step retry")
        # Check the live durable job before changing state; do not trust only proposal JSON.
        job = self.store.get_job(record["job_id"])
        step = next((item for item in job["steps"] if item["step_id"] == step_id), None)
        if step is None or not step["idempotent"] or step["state"] != "failed":
            raise ValueError("The durable failed step no longer matches the recovery proposal")
        prepared = self.store.prepare_failed_step_retry(
            record["job_id"], step_id, proposal_id=proposal_id, approved_by=approved_by,
        )
        # Preserve a separate workflow-level approval gate where required.
        if prepared["state"] == "waiting_approval":
            return prepared
        result = workflow_service.process_one(job_id=record["job_id"])
        if result is None:
            return self.store.get_job(record["job_id"])
        return result

    def materialize_approved_replan(self, proposal_id: str, *, planner: RuleBasedPlanner,
                                    tools: Sequence[ToolDefinition]) -> ExecutionPlan:
        """Reconstruct and revalidate an approved revised plan for a caller to execute.

        This does not mutate or execute the durable job. It verifies review status,
        current job progress, registered handlers, DAG structure and preservation of
        completed steps. The application must explicitly map the returned plan into a
        new/revised workflow and run its normal policy checks.
        """
        record = self.get_proposal(proposal_id)
        if record["status"] != "approved_for_manual_resume":
            raise ValueError("Replan proposal must be explicitly approved before materialization")
        proposal = record["proposal"]
        decision = proposal.get("decision", {})
        plan_data = decision.get("plan")
        if decision.get("action") != "replan" or not isinstance(plan_data, dict):
            raise ValueError("Approved proposal does not contain a revised plan")
        job = self.store.get_job(record["job_id"])
        live_completed = {step["step_id"] for step in job["steps"] if step["state"] == "succeeded"}
        feedback = proposal.get("feedback", {})
        recorded_completed = set(feedback.get("completed_step_ids", []))
        if live_completed != recorded_completed:
            raise ValueError("Job progress changed since diagnosis; create a fresh recovery proposal")
        raw_steps = plan_data.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("Revised plan must contain a non-empty steps list")
        catalog = {tool.key: tool for tool in tools}
        if len(catalog) != len(tuple(tools)):
            raise ValueError("tool keys must be unique")
        revised = planner._validate_proposal(
            str(plan_data.get("goal", "")), raw_steps, catalog,
            str(plan_data.get("planner_name", "approved-replan")),
        )
        if bool(plan_data.get("requires_approval", False)) != revised.requires_approval:
            # Approval requirements are derived from registered tool definitions,
            # never trusted from the serialized/model-proposed plan.
            raise ValueError("Serialized approval requirements do not match registered tools")
        old_steps = {step["step_id"]: step for step in job["steps"]}
        new_steps = {step.step_id: step for step in revised.steps}
        original_plan = proposal.get("current_plan")
        # Completed steps must be preserved by identity and operation inputs. Their
        # original handler/input definitions are retained in the proposal feedback
        # context when available; the durable job remains the authoritative source.
        for step_id in live_completed:
            before, after = old_steps[step_id], new_steps.get(step_id)
            if after is None:
                raise ValueError(f"Replan removed completed step {step_id!r}")
            if before["handler_key"] != after.handler_key or before["inputs"] != after.inputs:
                raise ValueError(f"Replan changed completed step {step_id!r}; refusing unsafe replay")
        return revised

    def execute_approved_replan(self, proposal_id: str, *, planner: RuleBasedPlanner,
                                tools: Sequence[ToolDefinition], workflow_adapter: Any,
                                workflow_service: Any, idempotency_key: str,
                                approved_by: str) -> dict[str, Any]:
        """Materialize, persist, and run an explicitly approved revised workflow.

        This is the integrated replan path, not a distributed transaction. It records
        the child job ID before execution, so repeated calls for the same proposal
        return the same child job rather than creating another workflow. Normal tool,
        approval, idempotency and final-verification checks remain in force.
        """
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 200:
            raise ValueError("approved_by must contain 1–200 characters")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValueError("a non-empty idempotency_key is required")
        if not callable(getattr(workflow_service, "process_one", None)):
            raise TypeError("workflow_service must expose process_one(job_id=...)")
        if not callable(getattr(workflow_adapter, "submit", None)):
            raise TypeError("workflow_adapter must expose submit(...)")
        record = self.get_proposal(proposal_id)
        if record["status"] != "approved_for_manual_resume":
            raise ValueError("Replan proposal must be explicitly approved before execution")
        decision = record["proposal"].get("decision", {})
        if decision.get("action") != "replan":
            raise ValueError("Only an approved replan proposal can create a revised workflow")

        # Idempotent replay of the coordinator call: return the already-created child.
        with self._connect() as db:
            rows = db.execute(
                "SELECT detail FROM recovery_events WHERE proposal_id=? AND event_type='replan_workflow_created' ORDER BY event_id",
                (proposal_id,),
            ).fetchall()
        if rows:
            child_id = json.loads(rows[-1]["detail"]).get("child_job_id")
            if not child_id:
                raise RuntimeError("replan audit event is missing its child job ID")
            child = self.store.get_job(child_id)
            if child["state"] in {"queued", "waiting_approval"}:
                if child["state"] == "waiting_approval":
                    return child
                processed = workflow_service.process_one(job_id=child_id)
                return processed if processed is not None else self.store.get_job(child_id)
            return child

        plan = self.materialize_approved_replan(proposal_id, planner=planner, tools=tools)
        from .replan_execution import ReplannedWorkflowAdapter
        if not isinstance(workflow_adapter, ReplannedWorkflowAdapter):
            raise TypeError("workflow_adapter must be a ReplannedWorkflowAdapter")
        if workflow_adapter.store.path != self.store.path:
            raise ValueError("workflow adapter must share the recovery service SQLite database")
        child_id = workflow_adapter.submit(
            record["job_id"], plan, tools=tools, idempotency_key=idempotency_key.strip(),
        )
        now = _now()
        detail = json.dumps({"child_job_id": child_id, "approved_by": approved_by.strip(),
                             "parent_job_id": record["job_id"], "idempotency_key": idempotency_key.strip()},
                            ensure_ascii=False, sort_keys=True)
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            # Protect against two callers racing to consume the same proposal.
            existing = db.execute(
                "SELECT event_id FROM recovery_events WHERE proposal_id=? AND event_type='replan_workflow_created' LIMIT 1",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                db.rollback()
                raise ValueError("a revised workflow has already been created for this proposal")
            db.execute("INSERT INTO recovery_events(proposal_id,event_type,detail,created_at) VALUES(?,?,?,?)",
                       (proposal_id, "replan_workflow_created", detail, now))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

        child = self.store.get_job(child_id)
        if child["state"] == "waiting_approval":
            return child
        processed = workflow_service.process_one(job_id=child_id)
        return processed if processed is not None else self.store.get_job(child_id)

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM recovery_proposals WHERE proposal_id=?", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            events = db.execute("SELECT event_type,detail,created_at FROM recovery_events WHERE proposal_id=? ORDER BY event_id",
                                (proposal_id,)).fetchall()
        return {"proposal_id": row["proposal_id"], "job_id": row["job_id"],
                "created_at": row["created_at"], "status": row["status"],
                "proposal": json.loads(row["proposal_json"]), "review_note": row["review_note"],
                "reviewed_by": row["reviewed_by"], "reviewed_at": row["reviewed_at"],
                "events": [{"event_type": e["event_type"], "detail": json.loads(e["detail"]),
                            "created_at": e["created_at"]} for e in events]}

    def list_for_job(self, job_id: str) -> list[dict[str, Any]]:
        self.store.get_job(job_id)  # Validate job existence before returning an empty list.
        with self._connect() as db:
            rows = db.execute("SELECT proposal_id FROM recovery_proposals WHERE job_id=? ORDER BY created_at",
                              (job_id,)).fetchall()
        return [self.get_proposal(row["proposal_id"]) for row in rows]
