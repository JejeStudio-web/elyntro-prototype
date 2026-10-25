import tempfile
import unittest
from pathlib import Path

from elyntro.contracts import TaskRequest, TaskStatus
from elyntro.engine import Engine
from elyntro.governance import GovernancePolicy, PolicyEngine, RiskLevel
from elyntro.store import SQLiteStore
from elyntro.verification_cycle import VerificationCycle
from elyntro.verifier import EvidenceVerifier, VerificationStatus


class GovernedVerificationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "engine.db")
        self.engine = Engine(self.store)
        self.verifier = EvidenceVerifier()
        self.verifier.register("output valid", lambda criterion, evidence, context: any(e.get("value") == "valid" for e in evidence))
        self.cycle = VerificationCycle(self.verifier, max_repairs=2)
        self.task = self.engine.submit(TaskRequest(goal="produce valid output", success_criteria=["output valid"]))
        for status in (TaskStatus.VALIDATING, TaskStatus.PLANNING):
            self.engine.transition(self.task.task_id, status)
        self.engine.set_plan(self.task.task_id, ["produce output"])
        self.engine.transition(self.task.task_id, TaskStatus.READY)
        self.engine.transition(self.task.task_id, TaskStatus.RUNNING)
        self.engine.transition(self.task.task_id, TaskStatus.VERIFYING)

    def tearDown(self):
        self.tmp.cleanup()

    def policy(self, *, allowed=True, threshold=RiskLevel.HIGH):
        return PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"task.repair"}) if allowed else frozenset(), approval_threshold=threshold))

    def test_verified_cycle_marks_task_succeeded(self):
        task, result = self.engine.verify_with_cycle(self.task.task_id, self.cycle,
            evidence=[{"criterion": "output valid", "detail": "checker output", "value": "valid"}])
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(result.report.status, VerificationStatus.VERIFIED)

    def test_repair_is_governed_before_callback(self):
        calls = []
        def repair(report, evidence, context):
            calls.append(True)
            return ([{"criterion": "output valid", "detail": "repaired and checked", "value": "valid"}], [])
        task, result = self.engine.verify_with_cycle(self.task.task_id, self.cycle,
            evidence=[{"criterion": "output valid", "detail": "bad initial output", "value": "invalid"}],
            policy_engine=self.policy(), repair=repair)
        self.assertEqual(calls, [True])
        self.assertEqual(task.status, TaskStatus.SUCCEEDED)
        events = self.store.list_events(self.task.task_id)
        self.assertTrue(any(event.get("event_type") == "repair_governance_decision" for event in events))

    def test_denied_repair_is_not_invoked_and_requires_approval(self):
        calls = []
        def repair(*args):
            calls.append(True)
            return ([], [])
        task, result = self.engine.verify_with_cycle(self.task.task_id, self.cycle,
            evidence=[{"criterion": "output valid", "detail": "bad initial output", "value": "invalid"}],
            policy_engine=self.policy(allowed=False), repair=repair)
        self.assertEqual(calls, [])
        self.assertEqual(task.status, TaskStatus.NEEDS_APPROVAL)
        self.assertFalse(result.accepted)

    def test_repair_callback_requires_policy_engine(self):
        with self.assertRaisesRegex(ValueError, "policy_engine is required"):
            self.engine.verify_with_cycle(self.task.task_id, self.cycle, evidence=[], repair=lambda *args: ([], []))

    def test_inconclusive_verification_requires_approval(self):
        verifier = EvidenceVerifier()
        cycle = VerificationCycle(verifier)
        task, result = self.engine.verify_with_cycle(self.task.task_id, cycle,
            evidence=[{"criterion": "output valid", "detail": "claim only"}])
        self.assertEqual(result.report.status, VerificationStatus.INCONCLUSIVE)
        self.assertEqual(task.status, TaskStatus.NEEDS_APPROVAL)


if __name__ == "__main__":
    unittest.main()
