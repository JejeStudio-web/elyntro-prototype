import tempfile
from pathlib import Path

import pytest

from elyntro import CognitiveLoop, EvidenceVerifier
from elyntro.contracts import Task, TaskRequest, TaskStatus
from elyntro.engine import Engine
from elyntro.planner import RuleBasedPlanner, ToolDefinition
from elyntro.store import SQLiteStore
from elyntro.task_executor import StepResult, TaskExecutor, TaskStep


CRITERION = "Se ha creado un artefacto no vacío y comprobable"


def research(inputs, context):
    return StepResult(output={"external_research": "not_configured"}, artifacts=[{"name": "alcance_de_investigacion.md", "content": "# Alcance\nNo se consultaron fuentes externas.", "media_type": "text/markdown"}])


def analyze(inputs, context):
    goal = str(inputs.get("goal", ""))
    return StepResult(output={"analysis": "deterministic-baseline"}, artifacts=[{"name": "analisis_local.json", "content": {"goal": goal, "external_model_used": False}, "media_type": "application/json"}])


def create(inputs, context):
    goal = str(inputs.get("goal", ""))
    if any(w in goal.casefold() for w in ("web", "página", "pagina", "html", "sitio")):
        return StepResult(output={"created": "index.html"}, artifacts=[{"name": "index.html", "content": "<!doctype html><html lang=\"es\"><title>Test</title><body>Demo</body></html>", "media_type": "text/html"}])
    return StepResult(output={"created": "resultado.md"}, artifacts=[{"name": "resultado.md", "content": "# Resultado\n\n" + goal, "media_type": "text/markdown"}])


def verify(inputs, context):
    return StepResult(output={"verification_requested": True}, evidence=[{"criterion": CRITERION, "detail": "The independent validator checks the expected artifact for this goal."}])


def validator(criterion, evidence, context):
    goal = str(context.get("goal", "")).casefold()
    arts = [a for a in context.get("artifacts", []) if isinstance(a, dict)]
    if any(w in goal for w in ("web", "página", "pagina", "html", "sitio")):
        return any(a.get("name") == "index.html" and isinstance(a.get("content"), str) and "<!doctype html>" in a["content"].casefold() for a in arts)
    if any(w in goal for w in ("analiza", "analizar", "análisis", "analisis", "evalúa", "evalua", "compara")):
        return any(a.get("name") == "analisis_local.json" and isinstance(a.get("content"), dict) and a["content"].get("goal") == context.get("goal") for a in arts)
    return any(a.get("name") == "resultado.md" and isinstance(a.get("content"), str) and a["content"].strip().startswith("# ") for a in arts)


def test_db_path_is_resolved_from_environment(monkeypatch, tmp_path):
    import app as app_module

    target = tmp_path / "runtime" / "persist" / "elyntro.db"
    monkeypatch.setenv("ELYNTRO_DB_PATH", str(target))

    resolved = app_module.resolve_db_path()

    assert resolved == target
    assert target.parent.exists()


def test_sqlite_store_reopens_data_after_close(tmp_path):
    db_path = tmp_path / "persisted.db"
    store = SQLiteStore(str(db_path))
    task = Task.from_request(TaskRequest(goal="persist this task", success_criteria=["done"]))
    store.create_task(task)

    reopened_store = SQLiteStore(str(db_path))
    reloaded = reopened_store.get_task(task.task_id)

    assert reloaded.goal == "persist this task"


def test_multistep_engine_loop_succeeds_and_verifies_artifact(tmp_path):
    engine = Engine(SQLiteStore(str(tmp_path / "engine.db")))
    tools = [ToolDefinition(key=k, description=k, capabilities=(k,), max_retries=0) for k in ("research", "analyze", "create", "verify")]
    verifier = EvidenceVerifier()
    verifier.register(CRITERION, validator)
    handlers = {"research": research, "analyze": analyze, "create": create, "verify": verify}
    executor = TaskExecutor(engine, handlers=handlers, verifier=verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), tools, executor, verifier=verifier)

    result = loop.run(TaskRequest(goal="Crea una página web de pruebas", success_criteria=[CRITERION]))

    assert result.task.status.value == "succeeded"
    assert result.verification["status"] == "verified"
    assert any(a["name"] == "index.html" for a in result.task.artifacts)


def test_runtime_rejects_unsafe_calculation():
    from tool_runtime import safe_calculate

    result = safe_calculate("__import__('os').system('echo pwned')")

    assert result["ok"] is False
    assert "permit" in result["error"].lower() or "no válida" in result["error"].lower() or "inválida" in result["error"].lower()


def test_task_executor_records_retry_and_incomplete_state(tmp_path):
    engine = Engine(SQLiteStore(str(tmp_path / "retry.db")))
    attempts = {"count": 0}

    def flaky(inputs, context):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("temporary failure")
        return StepResult(output={"ok": True}, evidence=[{"criterion": "done", "detail": "ok after retry"}])

    def finalize(inputs, context):
        return StepResult(output={"done": True}, evidence=[{"criterion": "final", "detail": "completed"}])

    executor = TaskExecutor(engine, handlers={"flaky": flaky, "finalize": finalize})
    task = engine.submit(TaskRequest(goal="Retry task", success_criteria=["done"]))

    result = executor.execute(task.task_id, [TaskStep("s1", "Flaky step", "flaky", max_retries=1)])

    assert result.status == TaskStatus.SUCCEEDED
    assert attempts["count"] == 2
    assert any(e["event_type"] == "step_attempt_failed" for e in engine.store.list_events(task.task_id))


def test_task_executor_fails_when_evidence_is_missing(tmp_path):
    engine = Engine(SQLiteStore(str(tmp_path / "missing_evidence.db")))

    def noop(inputs, context):
        return StepResult(output={"ok": True})

    task = engine.submit(TaskRequest(goal="Evidence required", success_criteria=["must be proven"]))
    result = TaskExecutor(engine, {"noop": noop}).execute(task.task_id, [TaskStep("s1", "No evidence", "noop")])

    assert result.status == TaskStatus.FAILED


def test_executor_resumes_committed_step_after_store_reopen(tmp_path):
    db_path = tmp_path / "resume.db"
    engine_before_restart = Engine(SQLiteStore(db_path))
    task = engine_before_restart.submit(TaskRequest(
        goal="Resume after restart",
        success_criteria=["first step persisted", "second step completed"],
    ))
    steps = [TaskStep("first", "First step", "first"), TaskStep("second", "Second step", "second")]
    for state in (TaskStatus.VALIDATING, TaskStatus.PLANNING):
        engine_before_restart.transition(task.task_id, state)
    engine_before_restart.set_plan(task.task_id, [step.name for step in steps])
    engine_before_restart.transition(task.task_id, TaskStatus.READY)
    engine_before_restart.transition(task.task_id, TaskStatus.RUNNING)
    engine_before_restart.store.add_event(task.task_id, "execution_plan_manifest", {
        "fingerprint": TaskExecutor._plan_fingerprint(steps),
        "step_count": len(steps),
        "step_ids": [step.step_id for step in steps],
    })
    engine_before_restart.add_artifact(task.task_id, "first.txt", "persisted result", "text/plain")
    engine_before_restart.store.add_event(task.task_id, "step_result_committed", {
        "step_id": "first", "attempt": 1, "output": {"saved": True},
        "evidence": [{"criterion": "first step persisted", "detail": "Saved before simulated restart."}],
        "artifacts": [{"name": "first.txt", "content": "persisted result", "media_type": "text/plain"}],
    })
    engine_before_restart.store.add_event(task.task_id, "step_succeeded", {
        "step_id": "first", "attempt": 1, "evidence_count": 1, "artifact_count": 1,
    })

    calls = {"first": 0, "second": 0}

    def first(inputs, context):
        calls["first"] += 1
        raise AssertionError("committed step must not be repeated after restart")

    def second(inputs, context):
        calls["second"] += 1
        return StepResult(output={"done": True}, evidence=[{
            "criterion": "second step completed", "detail": "Ran after store recreation.",
        }])

    engine_after_restart = Engine(SQLiteStore(db_path))
    result = TaskExecutor(engine_after_restart, {"first": first, "second": second}).execute(task.task_id, steps)

    assert result.status == TaskStatus.SUCCEEDED
    assert calls == {"first": 0, "second": 1}
    assert any(event["event_type"] == "step_recovered" for event in engine_after_restart.store.list_events(task.task_id))