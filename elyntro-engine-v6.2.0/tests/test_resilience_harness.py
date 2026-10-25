import unittest

from elyntro.resilience import ResilienceHarness


class ResilienceHarnessTests(unittest.TestCase):
    def test_all_fault_injection_scenarios_pass(self):
        report = ResilienceHarness.run()
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(report.to_dict()["scenario_count"], 2)
        self.assertEqual(report.to_dict()["passed_count"], 2)

    def test_idempotent_step_resume_does_not_repeat_committed_step(self):
        result = ResilienceHarness._safe_idempotent_resume()
        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.first_handler_calls, 0)
        self.assertEqual(result.second_handler_calls, 1)
        self.assertEqual(result.job_state, "succeeded")

    def test_non_idempotent_step_is_not_replayed(self):
        result = ResilienceHarness._non_idempotent_outcome_stays_uncertain()
        self.assertTrue(result.passed, result.to_dict())
        self.assertEqual(result.job_state, "interrupted")
        self.assertEqual(result.second_handler_calls, 0)


if __name__ == "__main__":
    unittest.main()
