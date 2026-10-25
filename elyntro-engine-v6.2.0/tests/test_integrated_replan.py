import json
import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.recovery import AdaptiveRecoveryCoordinator
from elyntro.replan_execution import ReplannedWorkflowAdapter


class IntegratedReplanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "jobs.sqlite")
        self.store = PersistentJobStore(self.path)
        self.calls = []
        self.tools = [
            ToolDefinition("lookup", "Lookup", ("research",)),
            ToolDefinition("analyze", "Analyze", ("analyze",)),
        ]
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "test-v1", lambda result, ctx: {
            "passed": True, "verifier_id": "test-v1"})
        self.workflow = DurableWorkflowService(self.store, verifiers, {
            "lookup": lambda data: self.calls.append(("lookup", data)) or {"fact": "persisted fact"},
            "analyze": lambda data: self.calls.append(("analyze", data)) or {"summary": data["fact"]},
        })
        self.adapter = ReplannedWorkflowAdapter(self.store, self.workflow)
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), self.tools,
                                    policy=AdaptationPolicy(max_revisions=2))
        self.recovery = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.plan = ExecutionPlan("research and analyze", (
            PlannedStep("s1", "Lookup", "lookup", {"q": "original"}),
            PlannedStep("s2", "Analyze", "analyze", {"fact": {"$ref": "s1.output.fact"}}, ("s1",)),
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def _failed_parent_and_approved_proposal(self):
        job_id = self.store.create_job(idempotency_key="parent", task_type="durable_workflow",
            payload={"workflow_id": "parent", "inputs": {}, "metadata": {}}, steps=[
                JobStep("s1", "lookup", {"q": "original"}, idempotent=True),
                JobStep("s2", "analyze", {"fact": {"$ref": "s1.output.fact"}}),
            ])
        self.store.claim_next(worker_id="test", job_id=job_id)
        self.store.start_step(job_id, "s1", worker_id="test")
        self.store.finish_step(job_id, "s1", output={"fact": "persisted fact"})
        self.store.start_step(job_id, "s2", worker_id="test")
        self.store.fail_step(job_id, "s2", error="missing evidence")
        proposal_id, _ = self.recovery.propose_for_job(job_id, goal=self.plan.goal,
            current_plan=self.plan, error=RuntimeError("unknown"), failed_step_id="s2")
        # This test isolates the orchestration boundary; the plan is supplied by a
        # separately validated proposal as it would be in a configured adaptation backend.
        with self.recovery._connect() as db:
            row = db.execute("SELECT proposal_json FROM recovery_proposals WHERE proposal_id=?",
                             (proposal_id,)).fetchone()
            data = json.loads(row["proposal_json"])
            data["decision"] = {
                "action": "replan", "rationale": "Use a reviewed alternative",
                "revision_number": 1, "unresolved_items": [],
                "plan": {"goal": self.plan.goal, "planner_name": "test-backend",
                         "requires_approval": False, "steps": [
                    {"step_id": "s1", "name": "Lookup", "handler_key": "lookup",
                     "inputs": {"q": "original"}, "depends_on": [], "requires_approval": False,
                     "rationale": "preserve"},
                    {"step_id": "s2", "name": "Analyze", "handler_key": "analyze",
                     "inputs": {"fact": {"$ref": "s1.output.fact"}}, "depends_on": ["s1"],
                     "requires_approval": False, "rationale": "retry analysis"},
                ]}}
            db.execute("UPDATE recovery_proposals SET proposal_json=? WHERE proposal_id=?",
                       (json.dumps(data), proposal_id))
        self.recovery.review(proposal_id, approved=True, reviewed_by="reviewer")
        return job_id, proposal_id

    def test_approved_replan_creates_and_executes_child_without_replaying_completed_step(self):
        parent_id, proposal_id = self._failed_parent_and_approved_proposal()
        self.recovery.materialize_approved_replan = lambda *args, **kwargs: self.plan
        child = self.recovery.execute_approved_replan(proposal_id, planner=RuleBasedPlanner(),
            tools=self.tools, workflow_adapter=self.adapter, workflow_service=self.workflow,
            idempotency_key="child-replan", approved_by="reviewer")
        self.assertEqual(child["state"], "succeeded")
        self.assertEqual([name for name, _ in self.calls], ["analyze"])
        self.assertEqual(self.calls[0][1]["fact"], "persisted fact")
        events = self.recovery.get_proposal(proposal_id)["events"]
        self.assertEqual(sum(e["event_type"] == "replan_workflow_created" for e in events), 1)
        self.assertEqual(self.store.get_job(parent_id)["steps"][0]["state"], "succeeded")

    def test_replan_requires_explicit_review(self):
        _, proposal_id = self._failed_parent_and_approved_proposal()
        with self.recovery._connect() as db:
            db.execute("UPDATE recovery_proposals SET status='proposed' WHERE proposal_id=?", (proposal_id,))
        with self.assertRaisesRegex(ValueError, "explicitly approved"):
            self.recovery.execute_approved_replan(proposal_id, planner=RuleBasedPlanner(),
                tools=self.tools, workflow_adapter=self.adapter, workflow_service=self.workflow,
                idempotency_key="child-replan-2", approved_by="reviewer")


if __name__ == "__main__":
    unittest.main()
