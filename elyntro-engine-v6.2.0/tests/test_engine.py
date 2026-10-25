import tempfile
import unittest
from pathlib import Path

from elyntro.contracts import TaskRequest, TaskStatus
from elyntro.engine import Engine
from elyntro.state_machine import InvalidTransitionError
from elyntro.store import SQLiteStore


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.engine = Engine(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def make_task(self, criteria=None, key=None):
        return self.engine.submit(TaskRequest(goal="Test goal", success_criteria=criteria or [], idempotency_key=key))

    def test_rejects_blank_goal(self):
        with self.assertRaises(ValueError):
            self.engine.submit(TaskRequest(goal="  "))

    def test_idempotency_key_returns_same_task(self):
        first = self.make_task(key="same-request")
        second = self.make_task(key="same-request")
        self.assertEqual(first.task_id, second.task_id)

    def test_invalid_transition_rejected(self):
        task = self.make_task()
        with self.assertRaises(InvalidTransitionError):
            self.engine.transition(task.task_id, TaskStatus.SUCCEEDED)

    def test_success_requires_evidence_for_each_criterion(self):
        task = self.make_task(["report exists", "report checked"])
        self.engine.transition(task.task_id, TaskStatus.VALIDATING)
        self.engine.transition(task.task_id, TaskStatus.PLANNING)
        self.engine.set_plan(task.task_id, ["make report"])
        self.engine.transition(task.task_id, TaskStatus.READY)
        self.engine.transition(task.task_id, TaskStatus.RUNNING)
        self.engine.transition(task.task_id, TaskStatus.VERIFYING)
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            self.engine.verify_success(task.task_id, [{"criterion": "report exists", "detail": "Stored"}])
        self.assertEqual(self.store.get_task(task.task_id).status, TaskStatus.VERIFYING)

    def test_full_lifecycle_and_event_history(self):
        task = self.make_task(["artifact exists"])
        for state in [TaskStatus.VALIDATING, TaskStatus.PLANNING]:
            self.engine.transition(task.task_id, state)
        self.engine.set_plan(task.task_id, ["create artifact"])
        self.engine.transition(task.task_id, TaskStatus.READY)
        self.engine.transition(task.task_id, TaskStatus.RUNNING)
        self.engine.add_artifact(task.task_id, "result.txt", "verified demo")
        self.engine.transition(task.task_id, TaskStatus.VERIFYING)
        result = self.engine.verify_success(task.task_id, [{"criterion": "artifact exists", "detail": "result.txt is in the task record"}])
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertGreaterEqual(len(self.store.list_events(task.task_id)), 8)

    def test_artifact_cannot_be_added_outside_running(self):
        task = self.make_task()
        with self.assertRaisesRegex(ValueError, "running"):
            self.engine.add_artifact(task.task_id, "x.txt", "x")

    def test_task_survives_store_reopen(self):
        task = self.make_task()
        reopened = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.assertEqual(reopened.get_task(task.task_id).goal, "Test goal")


if __name__ == "__main__":
    unittest.main()


class TaskExecutorTests(unittest.TestCase):
    def setUp(self):
        from elyntro.task_executor import TaskExecutor
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "executor.db")
        self.engine = Engine(self.store)
        self.TaskExecutor = TaskExecutor

    def tearDown(self):
        self.tmp.cleanup()

    def test_executes_steps_in_order_and_verifies_criteria(self):
        from elyntro.task_executor import StepResult, TaskStep
        calls = []
        def first(inputs, context):
            calls.append((context.step_id, inputs["value"]))
            return StepResult(output={"normalized": inputs["value"].upper()}, evidence=[
                {"criterion": "input normalized", "detail": "Output was uppercased by handler"}
            ])
        def second(inputs, context):
            calls.append((context.step_id, inputs["value"]))
            return StepResult(output="saved", evidence=[
                {"criterion": "result saved", "detail": "Save handler returned success"}
            ])
        task = self.engine.submit(TaskRequest(goal="Normalize and save", success_criteria=["input normalized", "result saved"]))
        executor = self.TaskExecutor(self.engine, {"normalize": first, "save": second})
        result = executor.execute(task.task_id, [
            TaskStep("s1", "Normalize", "normalize", {"value": "hello"}),
            TaskStep("s2", "Save", "save", {"value": "HELLO"}),
        ])
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertEqual(calls, [("s1", "hello"), ("s2", "HELLO")])
        self.assertEqual(len(result.artifacts), 2)
        event_types = [event["event_type"] for event in self.store.list_events(task.task_id)]
        self.assertIn("step_succeeded", event_types)
        self.assertIn("evidence_recorded", event_types)

    def test_retries_handler_then_succeeds(self):
        from elyntro.task_executor import StepResult, TaskStep
        attempts = {"count": 0}
        def flaky(inputs, context):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("temporary failure")
            return StepResult(output="ok", evidence=[{"criterion": "done", "detail": "handler succeeded"}])
        task = self.engine.submit(TaskRequest(goal="Retry", success_criteria=["done"]))
        result = self.TaskExecutor(self.engine, {"flaky": flaky}).execute(
            task.task_id, [TaskStep("s1", "Flaky step", "flaky", max_retries=1)]
        )
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertEqual(attempts["count"], 2)
        self.assertTrue(any(e["event_type"] == "step_attempt_failed" for e in self.store.list_events(task.task_id)))

    def test_exhausted_retries_fail_task(self):
        from elyntro.task_executor import TaskStep
        def broken(inputs, context):
            raise RuntimeError("always fails")
        task = self.engine.submit(TaskRequest(goal="Fail safely"))
        result = self.TaskExecutor(self.engine, {"broken": broken}).execute(
            task.task_id, [TaskStep("s1", "Broken step", "broken", max_retries=2)]
        )
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(len([e for e in self.store.list_events(task.task_id) if e["event_type"] == "step_attempt_failed"]), 3)

    def test_missing_evidence_prevents_success(self):
        from elyntro.task_executor import StepResult, TaskStep
        task = self.engine.submit(TaskRequest(goal="Evidence required", success_criteria=["must be proven"]))
        executor = self.TaskExecutor(self.engine, {"noop": lambda inputs, context: StepResult(output="ran")})
        result = executor.execute(task.task_id, [TaskStep("s1", "No evidence", "noop")])
        self.assertEqual(result.status, TaskStatus.FAILED)

    def test_unregistered_handler_rejected_before_state_changes(self):
        from elyntro.task_executor import TaskStep
        task = self.engine.submit(TaskRequest(goal="No unknown tools"))
        executor = self.TaskExecutor(self.engine)
        with self.assertRaisesRegex(ValueError, "unregistered handlers"):
            executor.execute(task.task_id, [TaskStep("s1", "Unknown", "not_registered")])
        self.assertEqual(self.store.get_task(task.task_id).status, TaskStatus.CREATED)

    def test_duplicate_step_ids_rejected(self):
        from elyntro.task_executor import TaskStep
        task = self.engine.submit(TaskRequest(goal="Unique step ids"))
        executor = self.TaskExecutor(self.engine, {"noop": lambda inputs, context: "ok"})
        steps = [TaskStep("same", "First", "noop"), TaskStep("same", "Second", "noop")]
        with self.assertRaisesRegex(ValueError, "unique"):
            executor.execute(task.task_id, steps)
        self.assertEqual(self.store.get_task(task.task_id).status, TaskStatus.CREATED)

class ExecutionControlTests(unittest.TestCase):
    def setUp(self):
        from elyntro.task_executor import TaskExecutor
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteStore(Path(self.tmp.name) / "control.db")
        self.engine = Engine(self.store)
        self.TaskExecutor = TaskExecutor

    def tearDown(self):
        self.tmp.cleanup()

    def test_timeout_fails_task_and_records_timeout(self):
        import time
        from elyntro.task_executor import TaskStep
        def slow(inputs, context):
            # Cooperative handler observes the deadline and exits promptly.
            while True:
                time.sleep(0.01)
                context.checkpoint()
        task = self.engine.submit(TaskRequest(goal="Timeout"))
        result = self.TaskExecutor(self.engine, {"slow": slow}).execute(
            task.task_id, [TaskStep("slow-1", "Slow step", "slow", timeout_seconds=0.05)]
        )
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertTrue(any(e["payload"].get("error_type") == "StepTimeoutError" for e in self.store.list_events(task.task_id) if e["event_type"] == "step_attempt_failed"))

    def test_resume_skips_durably_committed_step(self):
        from elyntro.task_executor import StepResult, TaskStep
        calls = {"first": 0, "second": 0}
        task = self.engine.submit(TaskRequest(goal="Resume", success_criteria=["first done", "second done"]))
        self.engine.transition(task.task_id, TaskStatus.VALIDATING)
        self.engine.transition(task.task_id, TaskStatus.PLANNING)
        self.engine.set_plan(task.task_id, ["First", "Second"])
        self.engine.transition(task.task_id, TaskStatus.READY)
        self.engine.transition(task.task_id, TaskStatus.RUNNING)
        self.store.add_event(task.task_id, "step_result_committed", {
            "step_id": "first", "attempt": 1, "output": "cached",
            "evidence": [{"criterion": "first done", "detail": "persisted before simulated crash"}], "artifacts": []
        })
        def first(inputs, context):
            calls["first"] += 1
            return StepResult(output="wrong", evidence=[{"criterion": "first done", "detail": "reran"}])
        def second(inputs, context):
            calls["second"] += 1
            return StepResult(output="ok", evidence=[{"criterion": "second done", "detail": "second step completed"}])
        result = self.TaskExecutor(self.engine, {"first": first, "second": second}).execute(task.task_id, [
            TaskStep("first", "First", "first"), TaskStep("second", "Second", "second")
        ])
        self.assertEqual(result.status, TaskStatus.SUCCEEDED)
        self.assertEqual(calls, {"first": 0, "second": 1})
        self.assertTrue(any(e["event_type"] == "step_recovered" for e in self.store.list_events(task.task_id)))

    def test_cancellation_token_stops_cooperative_handler(self):
        import threading
        import time
        from elyntro.execution_control import CancellationToken
        from elyntro.task_executor import TaskStep
        token = CancellationToken()
        started = threading.Event()
        def cooperative(inputs, context):
            started.set()
            while True:
                context.checkpoint()
                time.sleep(0.01)
        task = self.engine.submit(TaskRequest(goal="Cancel"))
        executor = self.TaskExecutor(self.engine, {"cooperative": cooperative})
        result_box = []
        thread = threading.Thread(target=lambda: result_box.append(executor.execute(
            task.task_id, [TaskStep("s1", "Cooperative", "cooperative", timeout_seconds=2)], token
        )))
        thread.start()
        self.assertTrue(started.wait(1))
        token.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_box[0].status, TaskStatus.CANCELLED)
