import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.persistent_jobs import JobStep, PersistentJobStore
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.planner import RuleBasedPlanner, ToolDefinition
from elyntro.recovery import AdaptiveRecoveryCoordinator
from elyntro.reconciliation import RecoveryReconciler


class RecoveryReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(self.path)
        tools = [ToolDefinition(key="task", description="task", capabilities=("process",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, policy=AdaptationPolicy(max_revisions=1))
        self.recovery = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.reconciler = RecoveryReconciler(self.store, self.recovery)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_database_report_is_clean_and_persisted(self):
        report = self.reconciler.reconcile()
        self.assertTrue(report.clean)
        runs = self.reconciler.list_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["finding_count"], 0)

    def test_approved_proposal_without_cycle_is_reported_not_executed(self):
        jid = self.store.create_job(idempotency_key="j1", task_type="demo", payload={},
                                    steps=[JobStep("a", "task", {}, idempotent=True)])
        # Build the minimal valid proposal using the service API; it must remain proposed
        # until review and reconciliation itself must never dispatch it.
        from elyntro.planner import ExecutionPlan, PlannedStep
        pid, _ = self.recovery.propose_for_job(
            jid, goal="demo", current_plan=ExecutionPlan(goal="demo", steps=(
                PlannedStep("a", "task", "task", {}),)), error=TimeoutError("temporary"),
                failed_step_id="a")
        self.recovery.review(pid, approved=True, reviewed_by="test")
        report = self.reconciler.reconcile()
        self.assertIn("approved_proposal_without_cycle_record", [f.kind for f in report.findings])
        self.assertEqual(self.store.get_job(jid)["state"], "queued")

    def test_invalid_flag_rejected(self):
        with self.assertRaises(ValueError):
            self.reconciler.reconcile(recover_expired_leases="yes")

    def test_run_limit_validated(self):
        with self.assertRaises(ValueError):
            self.reconciler.list_runs(limit=0)


if __name__ == "__main__":
    unittest.main()
