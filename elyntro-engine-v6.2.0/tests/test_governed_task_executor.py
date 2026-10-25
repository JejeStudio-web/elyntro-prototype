import tempfile
import unittest
from pathlib import Path

from elyntro.contracts import TaskRequest, TaskStatus
from elyntro.engine import Engine
from elyntro.governance import GovernancePolicy, PolicyEngine, ResourceLimits, BudgetLedger, RiskLevel
from elyntro.operation_journal import OperationJournal
from elyntro.store import SQLiteStore
from elyntro.task_executor import TaskExecutor, TaskStep, StepResult


class GovernedTaskExecutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SQLiteStore(self.root / "tasks.db")
        self.engine = Engine(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, criteria=None):
        return self.engine.submit(TaskRequest(goal="Governed execution test", success_criteria=criteria or []))

    def test_policy_denial_does_not_invoke_handler(self):
        task = self.task()
        calls = []
        policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"allowed"})))
        executor = TaskExecutor(self.engine, {"blocked": lambda inputs, ctx: calls.append("ran")}, policy_engine=policy)
        result = executor.execute(task.task_id, [TaskStep("s1", "blocked action", "blocked")])
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(calls, [])
        events = self.store.list_events(task.task_id)
        self.assertTrue(any(e["event_type"] == "action_policy_decision" and e["payload"]["outcome"] == "deny" for e in events))

    def test_approval_required_pauses_and_approved_exact_plan_resumes(self):
        task = self.task(["done"])
        calls = []
        handler = lambda inputs, ctx: (calls.append("ran") or StepResult(output="ok", evidence=[{"criterion": "done", "detail": "verified"}]))
        executor = TaskExecutor(self.engine, {"publish": handler})
        step = TaskStep("s1", "publish", "publish", requires_approval=True)
        paused = executor.execute(task.task_id, [step])
        self.assertEqual(paused.status, TaskStatus.NEEDS_APPROVAL)
        self.assertEqual(calls, [])
        self.engine.transition(task.task_id, TaskStatus.READY, "approval granted by host application")
        approved = TaskStep("s1", "publish", "publish", requires_approval=True, user_approved=True)
        finished = executor.execute(task.task_id, [approved])
        self.assertEqual(finished.status, TaskStatus.SUCCEEDED)
        self.assertEqual(calls, ["ran"])

    def test_changed_plan_is_rejected_after_manifest_is_persisted(self):
        task = self.task()
        executor = TaskExecutor(self.engine, {"work": lambda inputs, ctx: (_ for _ in ()).throw(RuntimeError("fail"))})
        original = TaskStep("s1", "work", "work", inputs={"value": 1})
        executor.execute(task.task_id, [original])
        altered = TaskStep("s1", "work", "work", inputs={"value": 2})
        with self.assertRaisesRegex(ValueError, "persisted manifest"):
            executor.execute(task.task_id, [altered])

    def test_external_write_is_journaled_and_reuses_committed_result(self):
        task = self.task(["done"])
        journal = OperationJournal(str(self.root / "operations.db"))
        calls = []
        def handler(inputs, ctx):
            calls.append(ctx.idempotency_key)
            return StepResult(output={"receipt": "R-1"}, evidence=[{"criterion": "done", "detail": "provider receipt R-1"}])
        executor = TaskExecutor(self.engine, {"publish": handler}, operation_journal=journal,
                                require_journal_for_external_writes=True)
        step = TaskStep("s1", "publish", "publish", writes_external_state=True)
        result = executor.execute(task.task_id, [step])
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertEqual(len(calls), 1)
        operation = journal.get_by_key(f"{task.task_id}:s1")
        self.assertEqual(operation["state"], "succeeded")
        self.assertEqual(operation["result"]["output"], {"receipt": "R-1"})
        self.assertEqual(operation["result"]["evidence"][0]["criterion"], "done")

    def test_external_write_without_required_journal_never_invokes_handler(self):
        task = self.task()
        calls = []
        executor = TaskExecutor(self.engine, {"publish": lambda inputs, ctx: calls.append(1)},
                                require_journal_for_external_writes=True)
        result = executor.execute(task.task_id, [TaskStep("s1", "publish", "publish", writes_external_state=True)])
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(calls, [])

    def test_uncertain_external_write_is_not_retried(self):
        task = self.task()
        journal = OperationJournal(str(self.root / "operations.db"))
        calls = []
        def handler(inputs, ctx):
            calls.append(1)
            raise TimeoutError("provider timed out")
        executor = TaskExecutor(self.engine, {"publish": handler}, operation_journal=journal,
                                require_journal_for_external_writes=True)
        step = TaskStep("s1", "publish", "publish", writes_external_state=True, max_retries=3)
        result = executor.execute(task.task_id, [step])
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(journal.get_by_key(f"{task.task_id}:s1")["state"], "uncertain")

    def test_governance_budget_exhaustion_blocks_before_handler(self):
        task = self.task()
        calls = []
        policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"work"})),
                              BudgetLedger(ResourceLimits(max_actions=1, max_total_tokens=1, max_total_cost_usd=0.01)))
        executor = TaskExecutor(self.engine, {"work": lambda inputs, ctx: (calls.append(1) or "ok")}, policy_engine=policy)
        steps = [TaskStep("s1", "first", "work"), TaskStep("s2", "second", "work")]
        result = executor.execute(task.task_id, steps)
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(len(calls), 1)
        events = self.store.list_events(task.task_id)
        self.assertTrue(any(e["event_type"] == "action_blocked" and e["payload"]["outcome"] == "budget_exceeded" for e in events))


if __name__ == "__main__":
    unittest.main()
