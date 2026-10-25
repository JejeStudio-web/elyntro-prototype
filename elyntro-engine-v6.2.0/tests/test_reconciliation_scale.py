"""Regression tests for complete reconciliation beyond the UI listing cap."""
import json
import os
import sqlite3
import tempfile
import unittest

from elyntro.persistent_jobs import JobStep, PersistentJobStore
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.recovery import AdaptiveRecoveryCoordinator
from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.planner import RuleBasedPlanner, ToolDefinition
from elyntro.reconciliation import RecoveryReconciler


from elyntro.sqlite_utils import ClosingConnection
class ReconciliationScaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(self.path)
        tools = [ToolDefinition(key="task", description="test handler", capabilities=("process",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, policy=AdaptationPolicy(max_revisions=1))
        self.recovery = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.reconciler = RecoveryReconciler(self.store, self.recovery)

    def tearDown(self):
        self.tmp.cleanup()

    def test_older_existing_parent_is_not_misreported_as_missing(self):
        target = self.store.create_job(
            idempotency_key="target", task_type="test", payload={},
            steps=[JobStep("a", "task", {}, idempotent=True)],
        )
        # Push the target outside PersistentJobStore.list_jobs(limit=500)'s recent window.
        for i in range(501):
            self.store.create_job(
                idempotency_key=f"filler-{i}", task_type="test", payload={"i": i},
                steps=[JobStep("a", "task", {}, idempotent=True)],
            )
        self.assertNotIn(target, {item["job_id"] for item in self.store.list_jobs(limit=500)})
        with sqlite3.connect(self.path, factory=ClosingConnection) as db:
            db.execute(
                "INSERT INTO recovery_proposals(proposal_id,job_id,created_at,status,proposal_json) VALUES(?,?,?,?,?)",
                ("proposal-target", target, "2026-01-01T00:00:00+00:00", "proposed", json.dumps({})),
            )
        report = self.reconciler.reconcile()
        missing_parent_ids = [f.reference_id for f in report.findings if f.kind == "proposal_parent_job_missing"]
        self.assertNotIn("proposal-target", missing_parent_ids)

    def test_truly_missing_parent_is_reported(self):
        with sqlite3.connect(self.path, factory=ClosingConnection) as db:
            db.execute(
                "INSERT INTO recovery_proposals(proposal_id,job_id,created_at,status,proposal_json) VALUES(?,?,?,?,?)",
                ("proposal-orphan", "no-such-job", "2026-01-01T00:00:00+00:00", "proposed", json.dumps({})),
            )
        report = self.reconciler.reconcile()
        self.assertIn("proposal-orphan", [f.reference_id for f in report.findings if f.kind == "proposal_parent_job_missing"])


if __name__ == "__main__":
    unittest.main()
