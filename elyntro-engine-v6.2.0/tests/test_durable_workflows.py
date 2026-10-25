import tempfile
import unittest
from pathlib import Path

from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = PersistentJobStore(Path(self.tmp.name) / "jobs.db")
        self.verifiers = TaskVerifierRegistry()
        self.verifiers.register("durable_workflow", "workflow-shape-v1",
                                lambda result, context: {"passed": bool(result.get("outputs")), "verifier_id": "workflow-shape-v1"})
        self.service = DurableWorkflowService(self.store, self.verifiers, {
            "double": lambda data: {"number": data["number"] * 2},
            "format": lambda data: {"text": f"result={data['number']}"},
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_persists_each_step_and_resolves_previous_output(self):
        jid = self.service.submit(workflow_id="demo", inputs={"n": 21}, idempotency_key="wf-1", steps=[
            JobStep("double", "double", {"number": {"$ref": "input.n"}}, idempotent=True),
            JobStep("format", "format", {"number": {"$ref": "double.output.number"}}, idempotent=True),
        ])
        job = self.service.process_one(job_id=jid)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(job["result"]["outputs"]["format"]["text"], "result=42")
        self.assertEqual([s["state"] for s in job["steps"]], ["succeeded", "succeeded"])

    def test_missing_verifier_fails_closed(self):
        registry = TaskVerifierRegistry()
        svc = DurableWorkflowService(self.store, registry, {"double": lambda d: {"number": 2}})
        jid = svc.submit(workflow_id="x", inputs={}, idempotency_key="wf-2", steps=[JobStep("a", "double", {}, idempotent=True)])
        job = svc.process_one(job_id=jid)
        self.assertEqual(job["state"], "failed")
        self.assertFalse(job["verification"].get("passed", False))

    def test_forward_reference_rejected(self):
        with self.assertRaises(ValueError):
            self.service.submit(workflow_id="x", inputs={}, idempotency_key="wf-3", steps=[
                JobStep("a", "double", {"number": {"$ref": "b.output.number"}}, idempotent=True),
                JobStep("b", "format", {"number": 1}, idempotent=True),
            ])

    def test_handler_failure_is_recorded_without_running_later_step(self):
        svc = DurableWorkflowService(self.store, self.verifiers, {
            "bad": lambda d: (_ for _ in ()).throw(RuntimeError("intentional")),
            "double": lambda d: {"number": 2},
        })
        jid = svc.submit(workflow_id="fail", inputs={}, idempotency_key="wf-4", steps=[
            JobStep("first", "bad", {}, idempotent=False), JobStep("second", "double", {}, idempotent=True),
        ])
        job = svc.process_one(job_id=jid)
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["steps"][0]["state"], "failed")
        self.assertEqual(job["steps"][1]["state"], "pending")


if __name__ == "__main__":
    unittest.main()
