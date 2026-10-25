import unittest

from elyntro.release_gate import GateCheck, ReleaseGate, ReleaseGateReport


class ReleaseGateTests(unittest.TestCase):
    def test_report_fails_if_any_check_fails(self):
        report = ReleaseGateReport("6.2.0", (
            GateCheck("ok", True, "ok"),
            GateCheck("bad", False, "bad"),
        ))
        self.assertFalse(report.passed)
        self.assertEqual(report.to_dict()["passed_count"], 1)

    def test_release_gate_checks_pass(self):
        report = ReleaseGate.run()
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(report.version, "6.2.0")
        self.assertEqual(report.to_dict()["check_count"], 5)


if __name__ == "__main__":
    unittest.main()
