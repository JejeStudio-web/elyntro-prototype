import unittest

from elyntro.acceptance import EndToEndAcceptanceHarness


class EndToEndAcceptanceTests(unittest.TestCase):
    def test_full_task_recovers_and_verifies_goal(self):
        report = EndToEndAcceptanceHarness.run()
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(report.job_state, "succeeded")
        self.assertTrue(report.verified)
        self.assertEqual(report.transient_failures, 1)
        self.assertEqual(report.collection_calls, 2)
        self.assertEqual(report.synthesis_calls, 1)


if __name__ == "__main__":
    unittest.main()
