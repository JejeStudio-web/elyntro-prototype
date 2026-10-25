import unittest
from elyntro.acceptance_matrix import AcceptanceMatrix


class AcceptanceMatrixTests(unittest.TestCase):
    def test_positive_and_negative_acceptance_matrix_passes(self):
        report = AcceptanceMatrix.run()
        self.assertTrue(report.passed, report.to_dict())
        self.assertEqual(report.to_dict()["case_count"], 5)
        self.assertEqual(report.to_dict()["passed_count"], 5)

    def test_wrong_answer_is_rejected(self):
        case = AcceptanceMatrix._wrong_result_is_rejected()
        self.assertTrue(case.passed, case.to_dict())

    def test_non_idempotent_operation_is_not_retried(self):
        case = AcceptanceMatrix._non_idempotent_timeout_is_not_retried()
        self.assertTrue(case.passed, case.to_dict())

    def test_missing_verifier_fails_closed(self):
        case = AcceptanceMatrix._missing_verifier_fails_closed()
        self.assertTrue(case.passed, case.to_dict())


if __name__ == "__main__":
    unittest.main()
