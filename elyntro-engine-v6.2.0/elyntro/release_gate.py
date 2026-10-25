"""Deterministic pre-release gate for Elyntro's local orchestration core.

This gate checks importability, positive/negative workflow acceptance, and the
operation journal's durable ambiguity handling. It is not a security audit,
load test, provider certification, or proof of distributed exactly-once execution.
Run with: python -m elyntro.release_gate
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Callable

from . import Engine, ModelGateway, OperationJournal, PolicyEngine, VerificationCycle
from .acceptance_matrix import AcceptanceMatrix
from .store import SQLiteStore


@dataclass(frozen=True)
class GateCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True)
class ReleaseGateReport:
    version: str
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "passed": self.passed,
            "check_count": len(self.checks),
            "passed_count": sum(check.passed for check in self.checks),
            "checks": [check.to_dict() for check in self.checks],
        }


class ReleaseGate:
    """Run local, repeatable release checks and report every check independently."""

    @staticmethod
    def run() -> ReleaseGateReport:
        checks: list[GateCheck] = []
        checks.append(ReleaseGate._public_api_imports())
        checks.append(ReleaseGate._acceptance_matrix())
        checks.append(ReleaseGate._journal_durable_ambiguity())
        checks.append(ReleaseGate._governed_executor_controls())
        checks.append(ReleaseGate._cognitive_loop_fails_closed_without_validator())
        version_path = Path(__file__).resolve().parent.parent / "VERSION"
        if version_path.exists():
            version = version_path.read_text(encoding="utf-8").strip()
        else:
            try:
                version = package_version("elyntro-engine")
            except PackageNotFoundError:
                version = "unknown"
        return ReleaseGateReport(version, tuple(checks))

    @staticmethod
    def _public_api_imports() -> GateCheck:
        required = (Engine, ModelGateway, OperationJournal, PolicyEngine, VerificationCycle)
        passed = all(item is not None for item in required)
        return GateCheck(
            "public_api_imports",
            passed,
            "required orchestration, provider gateway, journal, governance, and verification symbols import",
        )

    @staticmethod
    def _acceptance_matrix() -> GateCheck:
        report = AcceptanceMatrix.run()
        data = report.to_dict()
        return GateCheck(
            "positive_negative_workflow_matrix",
            report.passed,
            f"{data['passed_count']}/{data['case_count']} scenarios passed",
        )

    @staticmethod
    def _governed_executor_controls() -> GateCheck:
        from .contracts import TaskRequest, TaskStatus
        from .task_executor import TaskExecutor, TaskStep
        from .governance import GovernancePolicy, PolicyEngine
        with tempfile.TemporaryDirectory(prefix="elyntro-v6-governed-gate-") as tmp:
            engine = Engine(SQLiteStore(Path(tmp) / "tasks.sqlite"))
            task = engine.submit(TaskRequest(goal="release gate denied action"))
            calls: list[str] = []
            policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"allowed_tool"})))
            executor = TaskExecutor(engine, {"blocked_tool": lambda inputs, context: calls.append("invoked")}, policy_engine=policy)
            result = executor.execute(task.task_id, [TaskStep("step-1", "blocked action", "blocked_tool")])
            events = engine.store.list_events(task.task_id)
            denied = any(e["event_type"] == "action_policy_decision" and e["payload"].get("outcome") == "deny" for e in events)
            passed = result.status == TaskStatus.FAILED and not calls and denied
            return GateCheck("governed_executor_blocks_denied_actions", passed,
                "denied handler was not invoked and the policy decision was persisted")

    @staticmethod
    def _cognitive_loop_fails_closed_without_validator() -> GateCheck:
        from .cognitive_loop import CognitiveLoop
        from .contracts import TaskRequest, TaskStatus
        from .planner import RuleBasedPlanner, ToolDefinition
        from .task_executor import StepResult, TaskExecutor
        with tempfile.TemporaryDirectory(prefix="elyntro-v6-cognitive-gate-") as tmp:
            engine = Engine(SQLiteStore(Path(tmp) / "tasks.sqlite"))
            tools = [
                ToolDefinition("researcher", "Prepare input", ("research",)),
                ToolDefinition("analyzer", "Process input", ("analyze",)),
                ToolDefinition("checker", "Review output", ("verify",)),
            ]
            def handler(inputs, context):
                criterion = inputs.get("success_criteria", ["result valid"])[0]
                return StepResult(output={"claimed_success": True}, evidence=[
                    {"criterion": criterion, "detail": "handler claim without independent proof"}
                ])
            executor = TaskExecutor(engine, {tool.key: handler for tool in tools})
            loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, executor)
            result = loop.run(TaskRequest(goal="Analyze release gate task", success_criteria=["result valid"]))
            passed = (result.task.status == TaskStatus.NEEDS_APPROVAL
                      and result.verification is not None
                      and result.verification.get("status") == "inconclusive")
            return GateCheck(
                "cognitive_loop_fails_closed_without_validator", passed,
                "unverified handler claim cannot become SUCCEEDED; human review is required",
            )

    @staticmethod
    def _journal_durable_ambiguity() -> GateCheck:
        with tempfile.TemporaryDirectory(prefix="elyntro-v6-gate-") as tmp:
            path = str(Path(tmp) / "journal.sqlite")
            journal = OperationJournal(path)
            prepared = journal.prepare(
                "release-gate:ambiguous-operation",
                "simulate_external_write",
                {"resource": "example", "value": 1},
            )
            journal.transition(prepared["operation_id"], "started")
            journal.transition(
                prepared["operation_id"],
                "uncertain",
                error="simulated process interruption after dispatch",
            )
            # Re-open via a fresh journal instance to exercise persisted state.
            reopened = OperationJournal(path)
            persisted = reopened.get_by_key("release-gate:ambiguous-operation")
            uncertain = reopened.list_uncertain()
            passed = (
                persisted["state"] == "uncertain"
                and persisted["request"] == {"resource": "example", "value": 1}
                and len(uncertain) == 1
                and uncertain[0]["operation_id"] == prepared["operation_id"]
            )
            return GateCheck(
                "operation_journal_reopen_and_uncertain_state",
                passed,
                "uncertain state and canonical request survive reopening and remain discoverable",
            )


def main() -> int:
    report = ReleaseGate.run()
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
