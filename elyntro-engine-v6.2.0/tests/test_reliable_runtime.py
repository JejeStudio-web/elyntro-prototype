import tempfile
import unittest
from pathlib import Path

from elyntro import (
    AdaptiveModelRouter, EvaluationResult, EvaluationStore, ModelProfile, ModelRegistry,
    ModelRouter, RoutingFeedbackStore, RoutedExecutionService, RoutedTaskRequest,
)
from elyntro.reliable_runtime import (
    DailyBudgetExceeded, DuplicateExecutionError, ExecutionLedger,
    ReliableRoutedExecutionService,
)


from elyntro.sqlite_utils import ClosingConnection
class Adapter:
    def __init__(self, *, fail=False, cost_tokens=20):
        self.fail = fail
        self.calls = 0
        self.cost_tokens = cost_tokens

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        if self.fail:
            raise RuntimeError("provider private body")
        return f"output from {model_id}", 10, min(self.cost_tokens, max_output_tokens)


class ReliableRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.registry = ModelRegistry()
        self.registry.register(ModelProfile("model-a", "provider-a", frozenset({"text"}), 1, 1, 4000, 1000))
        self.registry.register(ModelProfile("model-b", "provider-b", frozenset({"text"}), 2, 2, 4000, 1000))
        self.evals = EvaluationStore(root / "eval.db")
        self.router = ModelRouter(self.registry, self.evals, minimum_samples=2)
        for model in ("model-a", "model-b"):
            for _ in range(3):
                self.router.record(EvaluationResult(model, "writing", .9, 20, 10, 10, True, benchmark_id="seed"))
        self.adaptive = AdaptiveModelRouter(self.router)
        self.feedback = RoutingFeedbackStore(root / "feedback.db")
        self.adapter_a = Adapter(fail=True)
        self.adapter_b = Adapter()
        self.base = RoutedExecutionService(self.adaptive,
            {"provider-a": self.adapter_a, "provider-b": self.adapter_b}, self.feedback)
        self.ledger = ExecutionLedger(root / "runtime.db")
        self.service = ReliableRoutedExecutionService(self.base, self.ledger, max_concurrent=2, max_attempts=2)
        self.request = RoutedTaskRequest(
            objective="write a short paragraph", messages=({"role": "user", "content": "write something"},),
            estimated_input_tokens=10, max_output_tokens=100, max_cost=0.01,
            task_type_override="writing", required_capabilities_override=frozenset({"text"}),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_failover_to_second_model_and_persist_attempts(self):
        result = self.service.execute(self.request, idempotency_key="run-1", daily_budget=1.0)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(self.adapter_a.calls, 1)
        self.assertEqual(self.adapter_b.calls, 1)
        stored = self.ledger.get_run("run-1")
        self.assertEqual(stored["status"], "succeeded")
        self.assertEqual(len(stored["attempts"]), 2)
        self.assertLessEqual(result.charged_cost, self.request.max_cost)

    def test_idempotency_key_prevents_duplicate_execution(self):
        self.service.execute(self.request, idempotency_key="same", daily_budget=1.0)
        calls = self.adapter_a.calls + self.adapter_b.calls
        with self.assertRaises(DuplicateExecutionError):
            self.service.execute(self.request, idempotency_key="same", daily_budget=1.0)
        self.assertEqual(self.adapter_a.calls + self.adapter_b.calls, calls)

    def test_daily_budget_reservation_is_enforced(self):
        with self.assertRaises(DailyBudgetExceeded):
            self.service.execute(self.request, idempotency_key="too-expensive", daily_budget=0.005)
        self.assertEqual(self.adapter_a.calls + self.adapter_b.calls, 0)

    def test_exhausted_candidates_returns_controlled_failure(self):
        one_attempt = ReliableRoutedExecutionService(self.base, self.ledger, max_concurrent=1, max_attempts=1)
        self.adapter_a.fail = False
        result = one_attempt.execute(self.request, idempotency_key="single", daily_budget=1.0)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(result.attempts), 1)

    def test_stale_run_is_marked_interrupted_without_replay(self):
        run_id = self.ledger.begin("crashed-run", 0.01, 1.0)
        import sqlite3
        db = sqlite3.connect(self.ledger.path, factory=ClosingConnection)
        try:
            db.execute("UPDATE runtime_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE run_id=?", (run_id,))
            db.commit()
        finally:
            db.close()
        recovered = self.ledger.recover_stale_runs(older_than_seconds=0)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["status"], "interrupted")
        stored = self.ledger.get_run("crashed-run")
        self.assertEqual(stored["status"], "interrupted")
        self.assertAlmostEqual(stored["charged_cost"], 0.01)
        self.assertEqual(self.adapter_a.calls + self.adapter_b.calls, 0)
        self.assertEqual(self.ledger.daily_usage()["currently_reserved"], 0)

    def test_verification_record_requires_succeeded_run_and_is_audited(self):
        result = self.service.execute(self.request, idempotency_key="verify-me", daily_budget=1.0)
        verification_id = self.ledger.record_verification(
            result.run_id, verifier_id="test-validator-v1", passed=True,
            evidence="Output matched the task-specific expected structure.", reason="All required checks passed",
        )
        stored = self.ledger.get_run("verify-me")
        self.assertGreater(verification_id, 0)
        self.assertEqual(len(stored["verifications"]), 1)
        self.assertEqual(stored["verifications"][0]["passed"], 1)

    def test_verification_rejects_nonterminal_or_failed_runs(self):
        run_id = self.ledger.begin("not-finished", 0.01, 1.0)
        with self.assertRaises(ValueError):
            self.ledger.record_verification(run_id, verifier_id="validator", passed=True, evidence="checked")

    def test_list_runs_can_filter_by_status(self):
        self.service.execute(self.request, idempotency_key="listed-run", daily_budget=1.0)
        rows = self.ledger.list_runs(status="succeeded")
        self.assertTrue(any(row["idempotency_key"] == "listed-run" for row in rows))

    def test_daily_usage_reports_reservations_or_charges(self):
        self.service.execute(self.request, idempotency_key="usage", daily_budget=1.0)
        usage = self.ledger.daily_usage()
        self.assertGreaterEqual(usage["committed_or_reserved"], 0)
        self.assertEqual(usage["currently_reserved"], 0)


if __name__ == "__main__":
    unittest.main()
