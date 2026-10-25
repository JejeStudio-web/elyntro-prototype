import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from examples.real_task_benchmark import build_cases, run_case


class RealTaskBenchmarkTests(unittest.TestCase):
    def test_end_to_end_benchmark_cases_meet_expected_outcomes(self):
        results = [run_case(case) for case in build_cases()]
        self.assertEqual(len(results), 4)
        self.assertTrue(all(row["pass"] for row in results), results)
        self.assertEqual(sum(row["actual_status"] == "succeeded" for row in results), 3)
        rejected = next(row for row in results if row["name"] == "reject_unverified_success_claim")
        self.assertEqual(rejected["actual_status"], "needs_approval")
        self.assertEqual(rejected["verification_status"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
