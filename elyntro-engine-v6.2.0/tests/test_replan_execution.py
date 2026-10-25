import os
import tempfile
import unittest

from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.planner import ExecutionPlan, PlannedStep, ToolDefinition
from elyntro.replan_execution import ReplannedWorkflowAdapter


class ReplannedWorkflowAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(os.path.join(self.tmp.name, "jobs.sqlite"))
        self.calls = []
        self.tools = [
            ToolDefinition("lookup", "Lookup", ("research",)),
            ToolDefinition("analyze", "Analyze", ("analyze",)),
        ]
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "test-v1", lambda result, ctx: {"passed": True, "verifier_id": "test-v1"})
        self.workflow = DurableWorkflowService(self.store, verifiers, {
            "lookup": lambda x: self.calls.append(("lookup", x)) or {"fact": "verified"},
            "analyze": lambda x: self.calls.append(("analyze", x)) or {"summary": x["fact"]},
        })
        self.adapter = ReplannedWorkflowAdapter(self.store, self.workflow)

    def tearDown(self):
        self.tmp.cleanup()

    def test_replan_carries_completed_output_and_does_not_replay_step(self):
        job_id = self.store.create_job(idempotency_key="old", task_type="durable_workflow",
            payload={"workflow_id": "old", "inputs": {}, "metadata": {}},
            steps=[JobStep("s1", "lookup", {}, idempotent=True), JobStep("s2", "analyze", {"fact": {"$ref": "s1.output.fact"}})])
        self.store.claim_next(worker_id="test", job_id=job_id)
        self.store.start_step(job_id, "s1", worker_id="test")
        self.store.finish_step(job_id, "s1", output={"fact": "saved"})
        self.store.start_step(job_id, "s2", worker_id="test")
        self.store.fail_step(job_id, "s2", error="planned failure")
        # A failed original job is eligible for a revised workflow.
        revised = ExecutionPlan("analyze saved evidence", (
            PlannedStep("s1", "Lookup", "lookup", {}, ()),
            PlannedStep("s2", "Analyze", "analyze", {"fact": {"$ref": "s1.output.fact"}}, ("s1",)),
        ))
        new_id = self.adapter.submit(job_id, revised, tools=self.tools, idempotency_key="replan-1")
        new_job = self.store.get_job(new_id)
        self.assertEqual([s["step_id"] for s in new_job["steps"]], ["s2"])
        self.assertEqual(new_job["payload"]["inputs"]["_elyntro_completed_outputs"]["s1"]["fact"], "saved")
        self.assertEqual(self.workflow.process_one(job_id=new_id)["state"], "succeeded")
        self.assertEqual([call[0] for call in self.calls], ["analyze"])
        self.assertEqual(self.calls[0][1]["fact"], "saved")

    def test_replan_rejects_changed_completed_step(self):
        job_id = self.store.create_job(idempotency_key="old2", task_type="durable_workflow",
            payload={"workflow_id": "old", "inputs": {}, "metadata": {}},
            steps=[JobStep("s1", "lookup", {"q": "original"}, idempotent=True)])
        self.store.claim_next(worker_id="test", job_id=job_id)
        self.store.start_step(job_id, "s1", worker_id="test")
        self.store.finish_step(job_id, "s1", output={"fact": "saved"})
        # Need terminal state to be eligible; a second failed step represents failure.
        self.store.fail_job(job_id, error="workflow failed after s1")
        plan = ExecutionPlan("goal", (PlannedStep("s1", "Lookup", "lookup", {"q": "changed"}),
                                       PlannedStep("s2", "Analyze", "analyze", {"fact": "x"})))
        with self.assertRaisesRegex(ValueError, "preserve completed step"):
            self.adapter.submit(job_id, plan, tools=self.tools, idempotency_key="replan-2")

    def test_rejects_missing_handler(self):
        job_id = self.store.create_job(idempotency_key="old3", task_type="durable_workflow",
            payload={"workflow_id": "old", "inputs": {}, "metadata": {}},
            steps=[JobStep("s1", "lookup", {})])
        self.store.claim_next(worker_id="test", job_id=job_id)
        self.store.start_step(job_id, "s1", worker_id="test")
        self.store.fail_step(job_id, "s1", error="failed")
        plan = ExecutionPlan("goal", (PlannedStep("s2", "Analyze", "missing", {}),))
        with self.assertRaisesRegex(ValueError, "unregistered tool/handler"):
            self.adapter.submit(job_id, plan, tools=self.tools, idempotency_key="replan-3")


if __name__ == "__main__":
    unittest.main()
