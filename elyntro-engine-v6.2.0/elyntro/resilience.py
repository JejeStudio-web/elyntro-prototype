"""Deterministic fault-injection checks for durable workflow recovery.

This module simulates a worker lease expiring after one step committed and while
another step is in flight. It is a local test harness, not a process-kill or
external-service chaos-testing framework.
"""
from __future__ import annotations
from .sqlite_utils import ClosingConnection

import os
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import Any

from .durable_workflows import DurableWorkflowService
from .persistent_jobs import JobStep, PersistentJobStore, TaskVerifierRegistry


@dataclass(frozen=True)
class ResilienceScenarioResult:
    name: str
    passed: bool
    job_state: str
    first_handler_calls: int
    second_handler_calls: int
    assertions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "job_state": self.job_state,
            "first_handler_calls": self.first_handler_calls,
            "second_handler_calls": self.second_handler_calls,
            "assertions": list(self.assertions),
        }


@dataclass(frozen=True)
class ResilienceReport:
    scenarios: tuple[ResilienceScenarioResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.scenarios) and all(s.passed for s in self.scenarios)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "scenario_count": len(self.scenarios),
            "passed_count": sum(1 for s in self.scenarios if s.passed),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


class ResilienceHarness:
    """Run repeatable crash-recovery scenarios against a temporary SQLite DB."""

    @staticmethod
    def run() -> ResilienceReport:
        return ResilienceReport((
            ResilienceHarness._safe_idempotent_resume(),
            ResilienceHarness._non_idempotent_outcome_stays_uncertain(),
        ))

    @staticmethod
    def _setup(path: str, calls: dict[str, int], *, second_idempotent: bool):
        store = PersistentJobStore(path)
        verifiers = TaskVerifierRegistry()
        verifiers.register("durable_workflow", "both-outputs", lambda result, _ctx: {
            "passed": "first" in result.get("outputs", {}) and "second" in result.get("outputs", {}),
            "verifier_id": "both-outputs",
            "evidence": ["both outputs are present"] if "first" in result.get("outputs", {}) and "second" in result.get("outputs", {}) else [],
        })

        def first(_inputs):
            calls["first"] += 1
            return {"value": "committed"}

        def second(_inputs):
            calls["second"] += 1
            return {"value": "continued"}

        service = DurableWorkflowService(store, verifiers, {"first": first, "second": second}, max_retries=0)
        job_id = service.submit(
            workflow_id="resilience-fault-injection",
            inputs={},
            idempotency_key="resilience-" + ("safe" if second_idempotent else "uncertain"),
            steps=[
                JobStep("first", "first", {}, idempotent=True),
                JobStep("second", "second", {}, idempotent=second_idempotent),
            ],
        )
        return store, service, job_id

    @staticmethod
    def _simulate_crash_between_steps(store: PersistentJobStore, job_id: str) -> None:
        """Persist step one, mark step two running, then expire the worker lease."""
        job = store.claim_next(worker_id="fault-injection-worker", lease_seconds=30, job_id=job_id)
        if job is None:
            raise RuntimeError("could not claim test job")
        store.start_step(job_id, "first", worker_id="fault-injection-worker")
        store.finish_step(job_id, "first", output={"value": "committed"})
        store.start_step(job_id, "second", worker_id="fault-injection-worker")
        with sqlite3.connect(store.path, factory=ClosingConnection) as db:
            db.execute("UPDATE jobs SET lease_until='2000-01-01T00:00:00+00:00' WHERE job_id=?", (job_id,))

    @staticmethod
    def _safe_idempotent_resume() -> ResilienceScenarioResult:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "safe.sqlite")
            calls = {"first": 0, "second": 0}
            store, service, job_id = ResilienceHarness._setup(path, calls, second_idempotent=True)
            ResilienceHarness._simulate_crash_between_steps(store, job_id)
            recovered = store.recover_expired_leases()
            # Recreate both objects to ensure the test uses persisted state after restart.
            store = PersistentJobStore(path)
            verifiers = TaskVerifierRegistry()
            verifiers.register("durable_workflow", "both-outputs", lambda result, _ctx: {
                "passed": "first" in result.get("outputs", {}) and "second" in result.get("outputs", {}),
                "verifier_id": "both-outputs",
                "evidence": ["both outputs are present"] if "first" in result.get("outputs", {}) and "second" in result.get("outputs", {}) else [],
            })
            def first(_inputs):
                calls["first"] += 1
                return {"value": "unexpected-repeat"}
            def second(_inputs):
                calls["second"] += 1
                return {"value": "continued"}
            restarted = DurableWorkflowService(store, verifiers, {"first": first, "second": second}, max_retries=0)
            job = restarted.process_one(job_id=job_id)
            assertions = (
                "expired lease recovered to queued",
                "committed first step was not executed again after service restart",
                "idempotent in-flight step was safely resumed",
                "configured verifier passed",
            )
            passed = bool(recovered and recovered[0]["state"] == "queued" and job
                           and job["state"] == "succeeded" and calls["first"] == 0
                           and calls["second"] == 1 and job.get("verification", {}).get("passed") is True)
            return ResilienceScenarioResult("safe_idempotent_resume", passed,
                job["state"] if job else "missing", calls["first"], calls["second"], assertions)

    @staticmethod
    def _non_idempotent_outcome_stays_uncertain() -> ResilienceScenarioResult:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "uncertain.sqlite")
            calls = {"first": 0, "second": 0}
            store, _service, job_id = ResilienceHarness._setup(path, calls, second_idempotent=False)
            ResilienceHarness._simulate_crash_between_steps(store, job_id)
            recovered = store.recover_expired_leases()
            store = PersistentJobStore(path)
            job = store.get_job(job_id)
            second_state = next(s["state"] for s in job["steps"] if s["step_id"] == "second")
            assertions = (
                "non-idempotent in-flight step marked uncertain",
                "job interrupted instead of automatically replayed",
                "no handler was invoked by reconciliation",
            )
            passed = bool(recovered and recovered[0]["state"] == "interrupted"
                           and job["state"] == "interrupted" and second_state == "uncertain"
                           and calls["first"] == 0 and calls["second"] == 0)
            return ResilienceScenarioResult("non_idempotent_stays_uncertain", passed,
                job["state"], calls["first"], calls["second"], assertions)
