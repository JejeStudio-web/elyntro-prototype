import tempfile
import unittest
from pathlib import Path
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


class AdaptiveRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(Path(self.tmp.name) / 'jobs.db')
        self.verifiers = TaskVerifierRegistry()
        self.verifiers.register('durable_workflow', 'retry-test-v1', lambda result, context: {
            'passed': result.get('outputs', {}).get('work', {}).get('value') == 'ok',
            'verifier_id': 'retry-test-v1',
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_transient_failure_retries_idempotent_step_and_records_attempts(self):
        calls = {'n': 0}
        def flaky(_):
            calls['n'] += 1
            if calls['n'] < 3:
                raise TimeoutError('temporary provider timeout')
            return {'value': 'ok'}
        service = DurableWorkflowService(self.store, self.verifiers, {'flaky': flaky}, max_retries=2)
        jid = service.submit(workflow_id='retry-demo', inputs={}, idempotency_key='retry-1',
                             steps=[JobStep('work', 'flaky', {}, idempotent=True)])
        job = service.process_one(job_id=jid)
        self.assertEqual(job['state'], 'succeeded')
        self.assertEqual(calls['n'], 3)
        self.assertEqual(job['steps'][0]['attempts'], 3)
        self.assertEqual(sum(e['event_type'] == 'step_retry_scheduled' for e in job['events']), 2)

    def test_non_idempotent_step_is_not_retried(self):
        calls = {'n': 0}
        def flaky(_):
            calls['n'] += 1
            raise ConnectionError('temporary')
        service = DurableWorkflowService(self.store, self.verifiers, {'flaky': flaky}, max_retries=5)
        jid = service.submit(workflow_id='retry-demo', inputs={}, idempotency_key='retry-2',
                             steps=[JobStep('work', 'flaky', {}, idempotent=False)])
        job = service.process_one(job_id=jid)
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(calls['n'], 1)
        self.assertFalse(any(e['event_type'] == 'step_retry_scheduled' for e in job['events']))

    def test_non_transient_error_is_not_retried_even_when_idempotent(self):
        calls = {'n': 0}
        def invalid(_):
            calls['n'] += 1
            raise ValueError('bad input')
        service = DurableWorkflowService(self.store, self.verifiers, {'invalid': invalid}, max_retries=5)
        jid = service.submit(workflow_id='retry-demo', inputs={}, idempotency_key='retry-3',
                             steps=[JobStep('work', 'invalid', {}, idempotent=True)])
        job = service.process_one(job_id=jid)
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(calls['n'], 1)


if __name__ == '__main__':
    unittest.main()
