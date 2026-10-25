import tempfile
import unittest
from pathlib import Path

from elyntro.benchmark import BenchmarkCase, BenchmarkRunner, basic_text_cases
from elyntro.model_evaluation import EvaluationStore
from elyntro.model_registry import ModelProfile


class FakeAdapter:
    def __init__(self, answers=None, fail_on=None):
        self.answers = answers or ["Paris is the capital of France.", "42", "I do not have enough information to know your favorite color."]
        self.fail_on = fail_on
        self.calls = 0
    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        index = self.calls
        self.calls += 1
        if index == self.fail_on:
            raise RuntimeError("do not persist this sensitive-looking detail")
        return self.answers[index], 20, 8


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = EvaluationStore(str(Path(self.tmp.name) / "eval.db"))
        self.profile = ModelProfile("fake", "fake-provider", frozenset({"text", "reasoning"}), 1, 2, 10000, 1000)

    def test_basic_suite_grades_and_persists_results(self):
        report = BenchmarkRunner(self.store).run(profile=self.profile, adapter=FakeAdapter(), cases=basic_text_cases(), benchmark_id="smoke-v1")
        self.assertEqual(len(report.case_results), 3)
        self.assertTrue(all(item.success for item in report.case_results))
        self.assertEqual(len(self.store.list(model_id="fake")), 3)
        self.assertIn("reasoning", report.summary)

    def test_failure_is_persisted_without_exception_text(self):
        report = BenchmarkRunner(self.store).run(profile=self.profile, adapter=FakeAdapter(fail_on=1), cases=basic_text_cases(), benchmark_id="smoke-fail")
        item = report.case_results[1]
        self.assertFalse(item.success)
        self.assertEqual(item.error_type, "RuntimeError")
        self.assertEqual(len(self.store.list(model_id="fake")), 3)

    def test_case_grader_reports_failed_checks(self):
        case = BenchmarkCase("x", "text", ({"role": "user", "content": "hello"},), required_contains=("world",), forbidden_contains=("bad",))
        quality, success, failures = case.grade("hello bad")
        self.assertFalse(success)
        self.assertAlmostEqual(quality, 1 / 3)
        self.assertEqual(len(failures), 2)

    def test_empty_cases_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkRunner(self.store).run(profile=self.profile, adapter=FakeAdapter(), cases=(), benchmark_id="x")

    def test_output_limit_rejected(self):
        with self.assertRaises(ValueError):
            BenchmarkRunner(self.store).run(profile=self.profile, adapter=FakeAdapter(), cases=basic_text_cases(), benchmark_id="x", max_output_tokens=1001)

if __name__ == "__main__":
    unittest.main()
