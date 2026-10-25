import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.recovery import AdaptiveRecoveryCoordinator


class ApprovedRecoveryExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(self.path)
        tools = [ToolDefinition(key="first", description="First operation", capabilities=("process",)),
                 ToolDefinition(key="second", description="Second operation", capabilities=("process",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), tools, policy=AdaptationPolicy(max_revisions=2))
        self.recovery = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "all-outputs", lambda result, ctx: {
            "passed": all(s in result.get("outputs", {}) for s in ("first", "second")),
            "verifier_id": "all-outputs",
        })
        self.calls = {"first": 0, "second": 0}
        def first(inputs):
            self.calls["first"] += 1
            return {"value": "kept"}
        def second(inputs):
            self.calls["second"] += 1
            if self.calls["second"] == 1:
                raise TimeoutError("temporary outage")
            return {"value": inputs["value"] + "-recovered"}
        self.workflow = DurableWorkflowService(self.store, verifiers,
            {"first": first, "second": second}, max_retries=0)
        self.plan = ExecutionPlan(goal="run two operations", steps=(
            PlannedStep("first", "First operation", "first", {}),
            PlannedStep("second", "Second operation", "second", {"value": "kept"}),
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def _failed_job(self, second_idempotent=True):
        return self.workflow.submit(workflow_id="recovery-test", inputs={}, idempotency_key="recovery-" + str(second_idempotent), steps=[
            JobStep("first", "first", {}, idempotent=True),
            JobStep("second", "second", {"value": {"$ref": "first.output.value"}}, idempotent=second_idempotent),
        ])

    def test_approved_retry_resumes_only_failed_step(self):
        job_id = self._failed_job()
        failed = self.workflow.process_one(job_id=job_id)
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(self.calls["first"], 1)
        proposal_id, proposal = self.recovery.propose_for_job(job_id, goal="run two operations",
            current_plan=self.plan, error=TimeoutError("temporary outage"), failed_step_id="second")
        self.assertEqual(proposal.decision.action, "continue")
        with self.assertRaises(ValueError):
            self.recovery.execute_approved_retry(proposal_id, self.workflow, approved_by="operator")
        self.recovery.review(proposal_id, approved=True, reviewed_by="operator", note="Safe idempotent retry")
        result = self.recovery.execute_approved_retry(proposal_id, self.workflow, approved_by="operator")
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.calls["first"], 1, "completed step must not be replayed")
        self.assertEqual(self.calls["second"], 2)
        self.assertEqual(result["steps"][0]["state"], "succeeded")
        self.assertTrue(any(e["event_type"] == "approved_recovery_retry_queued" for e in result["events"]))

    def test_non_idempotent_step_cannot_be_retried(self):
        job_id = self._failed_job(second_idempotent=False)
        self.workflow.process_one(job_id=job_id)
        proposal_id, proposal = self.recovery.propose_for_job(job_id, goal="run two operations",
            current_plan=self.plan, error=TimeoutError("temporary outage"), failed_step_id="second")
        self.assertNotEqual(proposal.decision.action, "continue")
        self.recovery.review(proposal_id, approved=True, reviewed_by="operator")
        with self.assertRaises(ValueError):
            self.recovery.execute_approved_retry(proposal_id, self.workflow, approved_by="operator")
        self.assertEqual(self.store.get_job(job_id)["state"], "failed")

    def test_rejected_proposal_cannot_execute(self):
        job_id = self._failed_job()
        self.workflow.process_one(job_id=job_id)
        proposal_id, _ = self.recovery.propose_for_job(job_id, goal="run two operations",
            current_plan=self.plan, error=TimeoutError("temporary outage"), failed_step_id="second")
        self.recovery.review(proposal_id, approved=False, reviewed_by="operator")
        with self.assertRaises(ValueError):
            self.recovery.execute_approved_retry(proposal_id, self.workflow, approved_by="operator")


if __name__ == "__main__":
    unittest.main()
