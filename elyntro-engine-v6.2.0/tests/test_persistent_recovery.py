import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.recovery import AdaptiveRecoveryCoordinator


class PersistentRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(self.path)
        tools = [ToolDefinition(key="lookup", description="Look up data", capabilities=("research",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, policy=AdaptationPolicy(max_revisions=2))
        self.service = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.plan = ExecutionPlan(goal="research something", steps=(
            PlannedStep("s1", "Look up", "lookup", {"q": "x"}),
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def _job(self, idempotent=False):
        return self.store.create_job(idempotency_key="key-" + str(idempotent), task_type="test",
            payload={}, steps=[JobStep("s1", "lookup", {"q": "x"}, idempotent=idempotent)])

    def test_proposal_is_persisted_and_unknown_error_stops(self):
        job_id = self._job()
        proposal_id, proposal = self.service.propose_for_job(job_id, goal="research something",
            current_plan=self.plan, error=RuntimeError("unclear failure"), failed_step_id="s1")
        self.assertEqual(proposal.decision.action, "stop")
        record = self.service.get_proposal(proposal_id)
        self.assertEqual(record["status"], "proposed")
        self.assertEqual(record["proposal"]["decision"]["action"], "stop")
        self.assertEqual(record["events"][0]["event_type"], "proposal_created")

    def test_idempotency_is_read_from_durable_step_definition(self):
        job_id = self._job(idempotent=True)
        _, proposal = self.service.propose_for_job(job_id, goal="research something",
            current_plan=self.plan, error=TimeoutError("temporary"), failed_step_id="s1")
        self.assertTrue(proposal.operation_idempotent)
        self.assertEqual(proposal.decision.action, "continue")

    def test_review_is_audited_and_not_execution_authorization(self):
        job_id = self._job(idempotent=True)
        proposal_id, _ = self.service.propose_for_job(job_id, goal="research something",
            current_plan=self.plan, error=TimeoutError("temporary"), failed_step_id="s1")
        reviewed = self.service.review(proposal_id, approved=True, reviewed_by="operator", note="Checked")
        self.assertEqual(reviewed["status"], "approved_for_manual_resume")
        self.assertEqual(reviewed["events"][-1]["event_type"], "proposal_reviewed")
        self.assertFalse(reviewed["proposal"]["execution_authorized"])
        with self.assertRaises(ValueError):
            self.service.review(proposal_id, approved=False, reviewed_by="operator")

    def test_unknown_job_and_step_are_rejected(self):
        with self.assertRaises(KeyError):
            self.service.propose_for_job("missing", goal="x", current_plan=self.plan)
        job_id = self._job()
        with self.assertRaises(ValueError):
            self.service.propose_for_job(job_id, goal="x", current_plan=self.plan, failed_step_id="not-a-step")


if __name__ == "__main__":
    unittest.main()
