import unittest
from elyntro.verifier import EvidenceVerifier, VerificationStatus
from elyntro.verification_cycle import VerificationCycle


class VerificationCycleTests(unittest.TestCase):
    def setUp(self):
        self.verifier = EvidenceVerifier()
        self.verifier.register("output is valid", lambda criterion, evidence, context: evidence[0].get("value") == "valid")

    def test_verified_result_does_not_trigger_repair(self):
        calls = []
        result = VerificationCycle(self.verifier).run(
            ["output is valid"], [{"criterion": "output is valid", "detail": "checked", "value": "valid"}],
            repair=lambda *args: calls.append(True),
        )
        self.assertTrue(result.accepted)
        self.assertEqual(result.report.status, VerificationStatus.VERIFIED)
        self.assertEqual(calls, [])
        self.assertEqual(result.attempts, 1)

    def test_failed_result_is_repaired_and_reverified(self):
        def repair(report, evidence, context):
            return ([{"criterion": "output is valid", "detail": "recomputed and checked", "value": "valid"}], [])
        result = VerificationCycle(self.verifier, max_repairs=2).run(
            ["output is valid"], [{"criterion": "output is valid", "detail": "initial", "value": "invalid"}], repair=repair)
        self.assertTrue(result.accepted)
        self.assertTrue(result.repaired)
        self.assertEqual(result.attempts, 2)

    def test_inconclusive_is_fail_closed_without_repair(self):
        verifier = EvidenceVerifier()
        called = []
        result = VerificationCycle(verifier).run(
            ["unknown"], [{"criterion": "unknown", "detail": "claim"}],
            repair=lambda *args: called.append(True),
        )
        self.assertEqual(result.report.status, VerificationStatus.INCONCLUSIVE)
        self.assertEqual(called, [])

    def test_repair_count_is_bounded(self):
        calls = []
        def repair(report, evidence, context):
            calls.append(1)
            return ([{"criterion": "output is valid", "detail": "still invalid", "value": "invalid"}], [])
        result = VerificationCycle(self.verifier, max_repairs=2).run(
            ["output is valid"], [{"criterion": "output is valid", "detail": "initial", "value": "invalid"}], repair=repair)
        self.assertFalse(result.accepted)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.attempts, 3)

    def test_rejects_invalid_repair_limit(self):
        for value in (-1, 6, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                VerificationCycle(self.verifier, max_repairs=value)


if __name__ == "__main__":
    unittest.main()
