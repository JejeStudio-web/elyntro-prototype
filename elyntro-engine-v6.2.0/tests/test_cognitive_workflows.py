import tempfile
import unittest
from pathlib import Path
from elyntro import (CognitiveLoop, CognitiveWorkflowService, Engine, RuleBasedPlanner,
                     SQLiteStore, TaskExecutor, TaskRequest, ToolDefinition)
from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import PersistentJobStore, TaskVerifierRegistry

class CognitiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.verifiers = TaskVerifierRegistry()
        self.verifiers.register("durable_workflow", "goal-criteria-v1", self.verify)
        self.job_store = PersistentJobStore(root / "jobs.db")
        self.durable = DurableWorkflowService(self.job_store, self.verifiers)
        tools = [ToolDefinition("research", "Gather evidence", ("research",)),
                 ToolDefinition("write", "Create result", ("create",)),
                 ToolDefinition("check", "Check result", ("verify",))]
        handlers = {
            "research": lambda d: {"evidence": "source", "goal": d["goal"]},
            "write": lambda d: {"draft": "created", "dependency_count": len(d.get("_elyntro_dependency_outputs", {}))},
            "check": lambda d: {"checked": True},
        }
        engine = Engine(SQLiteStore(root / "tasks.db"))
        self.loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, TaskExecutor(engine, handlers))
        for key, handler in handlers.items():
            self.durable.register_handler(key, handler)
        self.service = CognitiveWorkflowService(self.loop, self.durable)

    def tearDown(self): self.tmp.cleanup()

    @staticmethod
    def verify(result, context):
        outputs = result.get("outputs", {})
        passed = (len(outputs) == 3 and context.get("metadata", {}).get("success_criteria") == ["result exists"])
        return {"passed": passed, "verifier_id": "goal-criteria-v1", "reason": None if passed else "criteria missing"}

    def test_cognitive_plan_runs_as_durable_steps_and_verifies(self):
        jid = self.service.submit(TaskRequest(goal="Create a report", success_criteria=["result exists"], idempotency_key="cwf-1"))
        job = self.durable.process_one(job_id=jid)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(len(job["steps"]), 3)
        self.assertTrue(all(s["state"] == "succeeded" for s in job["steps"]))
        self.assertEqual(next(value for key, value in job["result"]["outputs"].items() if key.endswith("create"))["dependency_count"], 1)

    def test_requires_idempotency_key(self):
        with self.assertRaisesRegex(ValueError, "idempotency_key"):
            self.service.submit(TaskRequest(goal="Create a report"))

if __name__ == "__main__": unittest.main()
