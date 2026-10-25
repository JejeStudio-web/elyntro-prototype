import unittest
from elyntro.failure_diagnosis import FailureCategory, FailureDiagnoser

class FailureDiagnoserTests(unittest.TestCase):
    def setUp(self): self.d = FailureDiagnoser()
    def test_timeout_is_retryable_but_bounded_by_caller(self):
        result = self.d.diagnose(error=TimeoutError('timed out'))
        self.assertEqual(result.category, FailureCategory.TRANSIENT)
        self.assertTrue(result.retryable)
        self.assertIn('idempotent', result.recommended_action)
    def test_unknown_error_fails_closed(self):
        result = self.d.diagnose(error=RuntimeError('something happened'))
        self.assertEqual(result.category, FailureCategory.UNKNOWN)
        self.assertFalse(result.retryable)
        self.assertTrue(result.requires_human)
    def test_evidence_gap_not_transient(self):
        result = self.d.diagnose(evidence_gaps=['missing citation'])
        self.assertEqual(result.category, FailureCategory.MISSING_EVIDENCE)
        self.assertFalse(result.retryable)
    def test_approval_takes_precedence(self):
        result = self.d.diagnose(error=TimeoutError(), approval_required=True)
        self.assertEqual(result.category, FailureCategory.APPROVAL_REQUIRED)
        self.assertFalse(result.retryable)
    def test_missing_handler_configuration(self):
        result = self.d.diagnose(tool_registered=False)
        self.assertEqual(result.category, FailureCategory.CONFIGURATION)
    def test_permission_failure_requires_human(self):
        result = self.d.diagnose(error=PermissionError('denied'))
        self.assertEqual(result.category, FailureCategory.PERMISSION)
        self.assertTrue(result.requires_human)
    def test_feedback_preserves_execution_context(self):
        diagnosis = self.d.diagnose(error=TimeoutError('late'))
        feedback = diagnosis.to_feedback(completed_step_ids=['a'], failed_step_id='b')
        self.assertEqual(feedback.completed_step_ids, ('a',))
        self.assertEqual(feedback.failed_step_id, 'b')
        self.assertTrue(feedback.retryable)
    def test_serialization_is_json_friendly(self):
        data = self.d.diagnose(error=ValueError('bad')).to_dict()
        self.assertEqual(data['category'], 'invalid_input')
        self.assertIsInstance(data['signals'], tuple)

if __name__ == '__main__': unittest.main()
