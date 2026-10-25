"""Demonstrate governed repair and independent verification."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro.contracts import TaskRequest, TaskStatus
from elyntro.engine import Engine
from elyntro.governance import GovernancePolicy, PolicyEngine
from elyntro.store import SQLiteStore
from elyntro.verification_cycle import VerificationCycle
from elyntro.verifier import EvidenceVerifier

with TemporaryDirectory() as tmp:
    engine = Engine(SQLiteStore(Path(tmp) / "demo.db"))
    task = engine.submit(TaskRequest(goal="make a valid output", success_criteria=["output valid"]))
    for state in (TaskStatus.VALIDATING, TaskStatus.PLANNING):
        engine.transition(task.task_id, state)
    engine.set_plan(task.task_id, ["produce", "check"])
    engine.transition(task.task_id, TaskStatus.READY)
    engine.transition(task.task_id, TaskStatus.RUNNING)
    engine.transition(task.task_id, TaskStatus.VERIFYING)

    verifier = EvidenceVerifier()
    verifier.register("output valid", lambda criterion, evidence, context: any(x.get("value") == "valid" for x in evidence))
    cycle = VerificationCycle(verifier, max_repairs=2)
    policy = PolicyEngine(GovernancePolicy(allowed_tools=frozenset({"task.repair"})))

    def repair(report, evidence, context):
        return ([{"criterion": "output valid", "detail": "repaired output checked", "value": "valid"}], [])

    completed, result = engine.verify_with_cycle(
        task.task_id, cycle,
        evidence=[{"criterion": "output valid", "detail": "initial output failed", "value": "invalid"}],
        policy_engine=policy, repair=repair,
    )
    print({"task_status": completed.status.value, "verification": result.report.status.value,
           "attempts": result.attempts, "repair_history": list(result.repair_history)})
