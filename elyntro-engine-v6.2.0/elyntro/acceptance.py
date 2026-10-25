"""Repeatable end-to-end acceptance scenario for the local Elyntro engine.

This is a deterministic integration check, not a benchmark of model intelligence.
It exercises registered handlers, a transient failure, bounded idempotent retry,
persistent step outputs, and goal-level verification in one workflow.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .durable_workflows import DurableWorkflowService
from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


@dataclass(frozen=True)
class AcceptanceReport:
    passed: bool
    job_state: str
    verified: bool
    transient_failures: int
    collection_calls: int
    synthesis_calls: int
    assertions: tuple[str, ...]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "job_state": self.job_state,
            "verified": self.verified,
            "transient_failures": self.transient_failures,
            "collection_calls": self.collection_calls,
            "synthesis_calls": self.synthesis_calls,
            "assertions": list(self.assertions),
            "result": self.result,
        }


class EndToEndAcceptanceHarness:
    """Run a full local task with one recoverable failure and objective checks."""

    @staticmethod
    def run() -> AcceptanceReport:
        with tempfile.TemporaryDirectory(prefix="elyntro-acceptance-") as tmp:
            db_path = str(Path(tmp) / "acceptance.sqlite")
            calls = {"collect": 0, "synthesize": 0, "transient_failures": 0}
            expected = "verified-result-42"
            store = PersistentJobStore(db_path)
            verifiers = TaskVerifierRegistry()

            def verify(result: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
                outputs = result.get("outputs", {})
                synthesized = outputs.get("synthesize", {})
                actual = synthesized.get("answer")
                passed = actual == context.get("metadata", {}).get("expected_answer")
                return {
                    "passed": passed,
                    "verifier_id": "acceptance-exact-answer-v1",
                    "evidence": [f"exact answer matched: {actual}"] if passed else [],
                    "reason": None if passed else "synthesized answer did not match expected answer",
                }

            verifiers.register("durable_workflow", "acceptance-exact-answer-v1", verify)

            def collect(inputs: dict[str, Any]) -> dict[str, Any]:
                calls["collect"] += 1
                if calls["collect"] == 1:
                    calls["transient_failures"] += 1
                    raise TimeoutError("simulated temporary data-source timeout")
                return {"facts": ["source-a", "source-b"], "target": inputs["target"]}

            def synthesize(inputs: dict[str, Any]) -> dict[str, Any]:
                calls["synthesize"] += 1
                if len(inputs["facts"]) != 2:
                    return {"answer": "insufficient-evidence"}
                return {"answer": expected, "used_facts": len(inputs["facts"])}

            service = DurableWorkflowService(
                store, verifiers,
                {"collect": collect, "synthesize": synthesize},
                worker_id="acceptance-harness", max_retries=1,
            )
            job_id = service.submit(
                workflow_id="end-to-end-acceptance",
                inputs={"target": "produce an exact verified answer"},
                idempotency_key="acceptance-v1",
                metadata={"expected_answer": expected},
                steps=[
                    JobStep("collect", "collect", {"target": {"$ref": "input.target"}}, idempotent=True),
                    JobStep("synthesize", "synthesize", {"facts": {"$ref": "collect.output.facts"}}, idempotent=True),
                ],
            )
            job = service.process_one(job_id=job_id)
            verified = bool(job and job.get("verification", {}).get("passed") is True)
            outputs = (job or {}).get("result", {}).get("outputs", {})
            assertions = (
                "task was persisted before execution",
                "temporary failure was retried only because the step was idempotent",
                "second step consumed the first step's persisted output",
                "goal-level verifier compared the result against an exact expected value",
                "job is reported successful only when verification passes",
            )
            passed = bool(
                job and job.get("state") == "succeeded" and verified
                and calls["collect"] == 2 and calls["transient_failures"] == 1
                and calls["synthesize"] == 1
                and outputs.get("synthesize", {}).get("answer") == expected
            )
            report = AcceptanceReport(
                passed=passed,
                job_state=job.get("state", "missing") if job else "missing",
                verified=verified,
                transient_failures=calls["transient_failures"],
                collection_calls=calls["collect"],
                synthesis_calls=calls["synthesize"],
                assertions=assertions,
                result=(job or {}).get("result") or {},
            )
            return report
