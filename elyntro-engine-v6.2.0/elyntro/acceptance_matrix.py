"""Negative and recovery acceptance matrix for the local Elyntro engine.

This is deterministic fault injection, not a benchmark of model intelligence or
proof against real distributed failures.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .acceptance import EndToEndAcceptanceHarness
from .durable_workflows import DurableWorkflowService
from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry
from .resilience import ResilienceHarness


@dataclass(frozen=True)
class AcceptanceCase:
    name: str
    passed: bool
    expected: str
    observed: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed,
                "expected": self.expected, "observed": self.observed}


@dataclass(frozen=True)
class AcceptanceMatrixReport:
    cases: tuple[AcceptanceCase, ...]

    @property
    def passed(self) -> bool:
        return bool(self.cases) and all(case.passed for case in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "case_count": len(self.cases),
                "passed_count": sum(case.passed for case in self.cases),
                "cases": [case.to_dict() for case in self.cases]}


class AcceptanceMatrix:
    """Run positive and negative scenarios against the actual local workflow engine."""

    @staticmethod
    def run() -> AcceptanceMatrixReport:
        cases: list[AcceptanceCase] = []
        positive = EndToEndAcceptanceHarness.run()
        cases.append(AcceptanceCase(
            "happy_path_recovers_and_verifies", positive.passed,
            "task succeeds only after configured goal verification",
            f"state={positive.job_state}, verified={positive.verified}, retries={positive.transient_failures}"))

        resilience = ResilienceHarness.run()
        cases.append(AcceptanceCase(
            "crash_recovery_safety", resilience.passed,
            "safe idempotent step resumes; uncertain non-idempotent step is not replayed",
            f"{resilience.to_dict()['passed_count']}/{resilience.to_dict()['scenario_count']} subscenarios passed"))

        cases.append(AcceptanceMatrix._wrong_result_is_rejected())
        cases.append(AcceptanceMatrix._non_idempotent_timeout_is_not_retried())
        cases.append(AcceptanceMatrix._missing_verifier_fails_closed())
        return AcceptanceMatrixReport(tuple(cases))

    @staticmethod
    def _wrong_result_is_rejected() -> AcceptanceCase:
        with tempfile.TemporaryDirectory(prefix="elyntro-negative-verify-") as tmp:
            store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
            verifiers = TaskVerifierRegistry()
            verifiers.register("durable_workflow", "exact-answer", lambda result, _ctx: {
                "passed": result.get("outputs", {}).get("answer", {}).get("value") == "correct",
                "verifier_id": "exact-answer",
                "evidence": ["exact answer checked"] if result.get("outputs", {}).get("answer", {}).get("value") == "correct" else [],
                "reason": "answer mismatch" if result.get("outputs", {}).get("answer", {}).get("value") != "correct" else None,
            })
            service = DurableWorkflowService(store, verifiers, {"wrong": lambda _data: {"value": "wrong"}}, max_retries=0)
            job_id = service.submit(workflow_id="negative-verification", inputs={},
                                    idempotency_key="negative-verification-v1",
                                    steps=[JobStep("answer", "wrong", {}, idempotent=True)])
            job = service.process_one(job_id=job_id)
            passed = bool(job and job.get("state") == "failed"
                          and "answer mismatch" in str(job.get("error", "")))
            return AcceptanceCase("incorrect_answer_rejected", passed,
                                  "wrong output ends failed and verification is false",
                                  f"state={job.get('state') if job else 'missing'}, error={job.get('error') if job else None}")

    @staticmethod
    def _non_idempotent_timeout_is_not_retried() -> AcceptanceCase:
        with tempfile.TemporaryDirectory(prefix="elyntro-non-idempotent-") as tmp:
            store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
            verifiers = TaskVerifierRegistry()
            verifiers.register("durable_workflow", "never-reached", lambda _r, _c: {"passed": True, "verifier_id": "never-reached"})
            calls = {"count": 0}
            def timeout(_data):
                calls["count"] += 1
                raise TimeoutError("simulated uncertain side effect")
            service = DurableWorkflowService(store, verifiers, {"side_effect": timeout}, max_retries=3)
            job_id = service.submit(workflow_id="non-idempotent-timeout", inputs={},
                                    idempotency_key="non-idempotent-timeout-v1",
                                    steps=[JobStep("external", "side_effect", {}, idempotent=False)])
            job = service.process_one(job_id=job_id)
            passed = bool(job and job.get("state") == "failed" and calls["count"] == 1)
            return AcceptanceCase("non_idempotent_timeout_not_replayed", passed,
                                  "one attempt and failed state despite retry budget",
                                  f"state={job.get('state') if job else 'missing'}, handler_calls={calls['count']}")

    @staticmethod
    def _missing_verifier_fails_closed() -> AcceptanceCase:
        with tempfile.TemporaryDirectory(prefix="elyntro-no-verifier-") as tmp:
            store = PersistentJobStore(Path(tmp) / "jobs.sqlite")
            service = DurableWorkflowService(store, TaskVerifierRegistry(), {"work": lambda _data: {"done": True}}, max_retries=0)
            job_id = service.submit(workflow_id="missing-verifier", inputs={},
                                    idempotency_key="missing-verifier-v1",
                                    steps=[JobStep("work", "work", {}, idempotent=True)])
            job = service.process_one(job_id=job_id)
            passed = bool(job and job.get("state") == "failed"
                          and "No task-specific verifier" in str(job.get("error", "")))
            return AcceptanceCase("missing_verifier_fails_closed", passed,
                                  "no verifier cannot produce success",
                                  f"state={job.get('state') if job else 'missing'}, error={job.get('error') if job else None}")
