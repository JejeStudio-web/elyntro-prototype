import os
import tempfile
import unittest

from elyntro.adaptive import AdaptivePlanningLoop, AdaptationPolicy
from elyntro.persistent_jobs import JobStep, PersistentJobStore
from elyntro.planner import ExecutionPlan, PlannedStep, RuleBasedPlanner, ToolDefinition
from elyntro.persistent_recovery import PersistentRecoveryService
from elyntro.recovery import AdaptiveRecoveryCoordinator


class ReplanBackend:
    name = "test-replan-backend"

    def revise(self, goal, current_plan, feedback, constraints, success_criteria, tools, revision_number):
        return [{"step_id": "s1", "name": "Look up revised", "handler_key": "lookup",
                 "inputs": {"q": "revised"}, "depends_on": [], "rationale": "Correct invalid query"}]


class ApprovedReplanMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(os.path.join(self.tmp.name, "jobs.sqlite"))
        self.tools = [ToolDefinition(key="lookup", description="Look up data", capabilities=("research",))]
        loop = AdaptivePlanningLoop(RuleBasedPlanner(), self.tools,
                                    policy=AdaptationPolicy(max_revisions=2), backend=ReplanBackend())
        self.service = PersistentRecoveryService(self.store, AdaptiveRecoveryCoordinator(loop))
        self.plan = ExecutionPlan(goal="research something", steps=(
            PlannedStep("s1", "Look up", "lookup", {"q": "x"}),
        ))
        self.job_id = self.store.create_job(idempotency_key="replan-job", task_type="test", payload={},
            steps=[JobStep("s1", "lookup", {"q": "x"}, idempotent=True)])

    def tearDown(self):
        self.tmp.cleanup()

    def _proposal(self):
        proposal_id, proposal = self.service.propose_for_job(
            self.job_id, goal="research something", current_plan=self.plan,
            error=ValueError("invalid query"), failed_step_id="s1")
        self.assertEqual(proposal.decision.action, "replan")
        return proposal_id

    def test_replan_requires_review_and_revalidates_tools(self):
        proposal_id = self._proposal()
        with self.assertRaises(ValueError):
            self.service.materialize_approved_replan(proposal_id, planner=RuleBasedPlanner(), tools=self.tools)
        self.service.review(proposal_id, approved=True, reviewed_by="operator")
        plan = self.service.materialize_approved_replan(proposal_id, planner=RuleBasedPlanner(), tools=self.tools)
        self.assertEqual(plan.steps[0].inputs["q"], "revised")
        # Materialization is a proposal only; it does not silently mutate the durable job.
        self.assertEqual(self.store.get_job(self.job_id)["steps"][0]["inputs"], {"q": "x"})

    def test_unregistered_handler_is_rejected(self):
        proposal_id = self._proposal()
        self.service.review(proposal_id, approved=True, reviewed_by="operator")
        with self.assertRaises(ValueError):
            self.service.materialize_approved_replan(proposal_id, planner=RuleBasedPlanner(), tools=[])


if __name__ == "__main__":
    unittest.main()
