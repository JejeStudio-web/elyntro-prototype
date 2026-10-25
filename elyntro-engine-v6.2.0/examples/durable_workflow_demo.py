"""Run a tiny multi-step durable workflow with persisted intermediate output."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro.durable_workflows import DurableWorkflowService
from elyntro.persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


def main():
    with TemporaryDirectory() as tmp:
        store = PersistentJobStore(Path(tmp) / "elyntro.db")
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "demo-output-v1",
                           lambda result, ctx: {"passed": "summary" in result["outputs"].get("summarize", {}), "verifier_id": "demo-output-v1"})
        service = DurableWorkflowService(store, verifiers, {
            "collect": lambda inputs: {"value": inputs["value"], "source": "demo"},
            "summarize": lambda inputs: {"summary": f"Collected {inputs['value']} from {inputs['source']}"},
        })
        job_id = service.submit(workflow_id="demo", inputs={"value": "evidence"}, idempotency_key="demo-001", steps=[
            JobStep("collect", "collect", {"value": {"$ref": "input.value"}}, idempotent=True),
            JobStep("summarize", "summarize", {"value": {"$ref": "collect.output.value"}, "source": {"$ref": "collect.output.source"}}, idempotent=True),
        ])
        job = service.process_one(job_id=job_id)
        print({"job_id": job_id, "state": job["state"], "result": job["result"], "verification": job["verification"]})


if __name__ == "__main__":
    main()
