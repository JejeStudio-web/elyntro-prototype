import tempfile
import unittest
from pathlib import Path

from elyntro.benchmark import BenchmarkCase
from elyntro.benchmark_compare import BenchmarkComparisonRunner
from elyntro.model_evaluation import EvaluationStore
from elyntro.model_registry import ModelProfile


class FixedAdapter:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def complete(self, model_id, messages, max_output_tokens, timeout_seconds):
        self.calls += 1
        return self.answer, 12, 6


class BenchmarkComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = EvaluationStore(str(Path(self.tmp.name) / "eval.db"))
        self.profiles = [
            ModelProfile("a", "pa", frozenset({"text"}), 1, 2, 10000, 1000),
            ModelProfile("b", "pb", frozenset({"text"}), 2, 4, 10000, 1000),
        ]
        self.adapters = {"pa": FixedAdapter("answer contains expected"), "pb": FixedAdapter("wrong")}
        self.cases = [
            BenchmarkCase("case-1", "text", ({"role": "user", "content": "say expected"},), required_contains=("expected",), split="holdout"),
            BenchmarkCase("case-2", "text", ({"role": "user", "content": "say expected"},), required_contains=("expected",), split="holdout"),
        ]

    def test_paired_repeated_comparison_and_persistence(self):
        report = BenchmarkComparisonRunner(self.store).compare(
            profiles=self.profiles, adapters=self.adapters, cases=self.cases,
            comparison_id="cmp-1", split="holdout", repeats=2,
        )
        self.assertEqual(len(report.raw_results), 8)
        self.assertEqual([s.success_rate for s in report.summaries], [1.0, 0.0])
        self.assertEqual(report.paired_differences[0].paired_samples, 4)
        self.assertEqual(report.paired_differences[0].mean_success_difference_a_minus_b, 1.0)
        self.assertEqual(len(self.store.list(model_id="a")), 4)

    def test_manifest_fingerprint_is_stable_for_same_declared_inputs(self):
        runner = BenchmarkComparisonRunner(self.store)
        first = runner.compare(profiles=self.profiles, adapters=self.adapters, cases=self.cases,
                               comparison_id="manifest-stable", split="holdout")
        second = runner.compare(profiles=self.profiles, adapters=self.adapters, cases=self.cases,
                                comparison_id="manifest-stable", split="holdout")
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(len(first.manifest_sha256), 64)
        self.assertTrue(all(row["manifest_sha256"] == first.manifest_sha256 for row in first.raw_results))

    def test_manifest_fingerprint_changes_when_rubric_changes(self):
        runner = BenchmarkComparisonRunner(self.store)
        first = runner.compare(profiles=self.profiles, adapters=self.adapters, cases=self.cases,
                               comparison_id="manifest-rubric", split="holdout")
        changed_cases = [self.cases[0], BenchmarkCase(
            "case-2", "text", ({"role": "user", "content": "say expected"},),
            required_contains=("expected", "answer"), split="holdout")]
        second = runner.compare(profiles=self.profiles, adapters=self.adapters, cases=changed_cases,
                                comparison_id="manifest-rubric", split="holdout")
        self.assertNotEqual(first.manifest_sha256, second.manifest_sha256)

    def test_rejects_split_mismatch(self):
        with self.assertRaises(ValueError):
            BenchmarkComparisonRunner(self.store).compare(
                profiles=self.profiles, adapters=self.adapters, cases=self.cases,
                comparison_id="cmp-2", split="validation",
            )

    def test_rejects_duplicate_model_ids(self):
        with self.assertRaises(ValueError):
            BenchmarkComparisonRunner(self.store).compare(
                profiles=[self.profiles[0], self.profiles[0]], adapters=self.adapters,
                cases=self.cases, comparison_id="cmp-3", split="holdout",
            )

    def test_json_and_csv_exports(self):
        report = BenchmarkComparisonRunner(self.store).compare(
            profiles=self.profiles, adapters=self.adapters, cases=self.cases,
            comparison_id="cmp-4", split="holdout",
        )
        json_path = Path(self.tmp.name) / "report.json"
        csv_path = Path(self.tmp.name) / "report.csv"
        report.export_json(json_path)
        report.export_csv(csv_path)
        self.assertIn('"paired_differences"', json_path.read_text())
        self.assertIn('"manifest_sha256"', json_path.read_text())
        self.assertIn("manifest_sha256", csv_path.read_text())
        self.assertIn("case_id", csv_path.read_text())


if __name__ == "__main__":
    unittest.main()
