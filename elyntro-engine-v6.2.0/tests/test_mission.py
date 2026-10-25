import tempfile
import unittest
from pathlib import Path

from elyntro import Engine, SQLiteStore, TaskRequest, ToolDefinition, MissionService, StepResult


class MissionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(SQLiteStore(str(Path(self.temp.name) / 'mission.db')))

    def tearDown(self):
        self.temp.cleanup()

    def test_full_mission_runs_through_plan_execute_and_verification(self):
        tools = [
            ToolDefinition('researcher', 'Collect relevant evidence', ('research',)),
            ToolDefinition('analyst', 'Analyze the evidence', ('analyze',)),
            ToolDefinition('checker', 'Check success criteria', ('verify',)),
        ]
        calls = []
        def handler(name):
            def run(inputs, context):
                calls.append(name)
                return StepResult(output={'stage': name}, evidence=[
                    {'criterion': 'report-ready', 'detail': f'{name} completed the criterion'}
                ])
            return run
        service = MissionService(self.engine, tools, {t.key: handler(t.key) for t in tools})
        prepared = service.prepare(TaskRequest(
            goal='Research and analyze this subject, then verify the report',
            success_criteria=['report-ready'],
        ))
        self.assertEqual(prepared.task.status.value, 'ready')
        self.assertEqual(calls, [])
        result = service.execute(prepared)
        self.assertEqual(result.status.value, 'succeeded')
        self.assertEqual(calls, ['researcher', 'analyst', 'checker'])
        events = self.engine.store.list_events(result.task_id)
        kinds = [event['event_type'] for event in events]
        self.assertIn('mission_prepared', kinds)
        self.assertIn('mission_execution_finished', kinds)
        self.assertIn('evidence_recorded', kinds)

    def test_approval_required_tool_never_runs_before_approval(self):
        tools = [
            ToolDefinition('analyst', 'Analyze report', ('analyze',)),
            ToolDefinition('publisher', 'Publish the report', ('execute',), requires_approval=True),
            ToolDefinition('checker', 'Check publication', ('verify',)),
        ]
        calls = []
        def handler(key):
            def run(inputs, context):
                calls.append(key)
                return StepResult(output={'done': key}, evidence=[{'criterion': 'published', 'detail': key + ' completed'}])
            return run
        service = MissionService(self.engine, tools, {t.key: handler(t.key) for t in tools})
        prepared = service.prepare(TaskRequest(goal='Publish the report', success_criteria=['published']))
        self.assertTrue(prepared.requires_approval)
        self.assertEqual(prepared.task.status.value, 'needs_approval')
        with self.assertRaises(PermissionError):
            service.execute(prepared)
        self.assertEqual(calls, [])
        service.approve(prepared.task.task_id, approver='test-operator', reason='approved for test')
        result = service.execute(prepared)
        self.assertEqual(result.status.value, 'succeeded')
        self.assertIn('publisher', calls)

    def test_unknown_or_missing_handlers_fail_closed(self):
        tools = [ToolDefinition('researcher', 'Research', ('research',))]
        with self.assertRaisesRegex(ValueError, 'missing handlers'):
            MissionService(self.engine, tools, {})
        with self.assertRaisesRegex(ValueError, 'no matching tool'):
            MissionService(self.engine, tools, {
                'researcher': lambda i, c: None,
                'shell': lambda i, c: None,
            })

    def test_approval_requires_identity_and_pending_state(self):
        tools = [
            ToolDefinition('analyst', 'Analyze', ('analyze',)),
            ToolDefinition('publisher', 'Publish', ('execute',), requires_approval=True),
            ToolDefinition('checker', 'Verify', ('verify',)),
        ]
        service = MissionService(self.engine, tools, {t.key: (lambda i, c: StepResult(evidence=[{'criterion':'ok','detail':'done'}])) for t in tools})
        prepared = service.prepare(TaskRequest(goal='Publish this', success_criteria=['ok']))
        with self.assertRaisesRegex(ValueError, 'approver'):
            service.approve(prepared.task.task_id, approver=' ')
        service.approve(prepared.task.task_id, approver='operator')
        with self.assertRaisesRegex(ValueError, 'awaiting approval'):
            service.approve(prepared.task.task_id, approver='operator')


if __name__ == '__main__':
    unittest.main()
