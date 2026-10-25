import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.recovery import AdaptiveRecoveryCoordinator
from elyntro.recovery_cycle import RecoveryCycleCoordinator


class RecoveryCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(path)
        self.tools = [ToolDefinition(key="first", description="first", capabilities=("process",)),
                      ToolDefinition(key="second", description="second", capabilities=("process",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), self.tools, policy=AdaptationPolicy(max_revisions=2))
        self.recovery = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.cycle = RecoveryCycleCoordinator(self.store, self.recovery)
        self.calls = {"first": 0, "second": 0}
        def first(inputs):
            self.calls["first"] += 1
            return {"value": "kept"}
        def second(inputs):
            self.calls["second"] += 1
            if self.calls["second"] == 1:
                raise TimeoutError("temporary outage")
            return {"value": inputs["value"] + "-ok"}
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "has-second-output", lambda result, ctx: {
            "passed": "second" in result.get("outputs", {}), "evidence": ["second output exists"]})
        self.workflow = DurableWorkflowService(self.store, verifiers,
            {"first": first, "second": second}, max_retries=0)
        self.plan = ExecutionPlan(goal="two steps", steps=(
            PlannedStep("first", "first", "first", {}),
            PlannedStep("second", "second", "second", {"value": "kept"}),
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def test_reviewed_retry_runs_and_records_verified_outcome(self):
        jid = self.workflow.submit(workflow_id="cycle", inputs={}, idempotency_key="cycle-job", steps=[
            JobStep("first", "first", {}, idempotent=True),
            JobStep("second", "second", {"value": {"$ref": "first.output.value"}}, idempotent=True),
        ])
        self.workflow.process_one(job_id=jid)
        pid, proposal = self.recovery.propose_for_job(jid, goal="two steps", current_plan=self.plan,
            error=TimeoutError("temporary outage"), failed_step_id="second")
        self.assertEqual(proposal.decision.action, "continue")
        self.recovery.review(pid, approved=True, reviewed_by="tester")
        result = self.cycle.execute_reviewed(pid, workflow_service=self.workflow, approved_by="tester")
        self.assertEqual(result.status, "succeeded_verified")
        self.assertTrue(result.execution_succeeded)
        self.assertTrue(result.verification_passed)
        self.assertEqual(self.calls["first"], 1)
        again = self.cycle.execute_reviewed(pid, workflow_service=self.workflow, approved_by="tester")
        self.assertEqual(again, result)
        self.assertEqual(self.calls["second"], 2)

    def test_unapproved_proposal_rejected(self):
        jid = self.workflow.submit(workflow_id="cycle", inputs={}, idempotency_key="cycle-unapproved", steps=[
            JobStep("first", "first", {}, idempotent=True),
            JobStep("second", "second", {"value": {"$ref": "first.output.value"}}, idempotent=True),
        ])
        self.workflow.process_one(job_id=jid)
        pid, _ = self.recovery.propose_for_job(jid, goal="two steps", current_plan=self.plan,
            error=TimeoutError("temporary outage"), failed_step_id="second")
        with self.assertRaises(ValueError):
            self.cycle.execute_reviewed(pid, workflow_service=self.workflow, approved_by="tester")


if __name__ == "__main__":
    unittest.main()
