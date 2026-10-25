import tempfile
import unittest
from pathlib import Path
import pytest
from elyntro import CognitiveLoop, EvidenceVerifier
from elyntro.contracts import TaskRequest
from elyntro.engine import Engine
from elyntro.planner import RuleBasedPlanner, ToolDefinition
from elyntro.store import SQLiteStore
from elyntro.task_executor import StepResult, TaskExecutor

CRITERION = "Se ha creado un artefacto no vacío y comprobable"

def research(inputs, context):
    return StepResult(output={"external_research": "not_configured"}, artifacts=[{"name":"alcance_de_investigacion.md", "content":"# Alcance\nNo se consultaron fuentes externas.", "media_type":"text/markdown"}])
def analyze(inputs, context):
    goal = str(inputs.get("goal", ""))
    return StepResult(output={"analysis":"deterministic-baseline"}, artifacts=[{"name":"analisis_local.json", "content":{"goal":goal,"external_model_used":False}, "media_type":"application/json"}])
def create(inputs, context):
    goal = str(inputs.get("goal", ""))
    if any(w in goal.casefold() for w in ("web", "página", "pagina", "html", "sitio")):
        return StepResult(output={"created":"index.html"}, artifacts=[{"name":"index.html", "content":"<!doctype html><html lang=\"es\"><title>Test</title><body>Demo</body></html>", "media_type":"text/html"}])
    return StepResult(output={"created":"resultado.md"}, artifacts=[{"name":"resultado.md", "content":"# Resultado\n\n"+goal, "media_type":"text/markdown"}])
def verify(inputs, context):
    return StepResult(output={"verification_requested":True}, evidence=[{"criterion":CRITERION,"detail":"The independent validator checks the expected artifact for this goal."}])
def validator(criterion, evidence, context):
    goal = str(context.get("goal", "")).casefold()
    arts = [a for a in context.get("artifacts", []) if isinstance(a, dict)]
    if any(w in goal for w in ("web", "página", "pagina", "html", "sitio")):
        return any(a.get("name")=="index.html" and isinstance(a.get("content"),str) and "<!doctype html>" in a["content"].casefold() for a in arts)
    if any(w in goal for w in ("analiza", "analizar", "análisis", "analisis", "evalúa", "evalua", "compara")):
        return any(a.get("name")=="analisis_local.json" and isinstance(a.get("content"),dict) and a["content"].get("goal")==context.get("goal") for a in arts)
    return any(a.get("name")=="resultado.md" and isinstance(a.get("content"),str) and a["content"].strip().startswith("# ") for a in arts)

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(SQLiteStore(str(Path(self.tmp.name)/"test.db")))
        self.tools = [ToolDefinition(key=k, description=k, capabilities=(k,), max_retries=0) for k in ("research","analyze","create","verify")]
        self.verifier = EvidenceVerifier(); self.verifier.register(CRITERION, validator)
        handlers={"research":research,"analyze":analyze,"create":create,"verify":verify}
        self.executor=TaskExecutor(self.engine, handlers=handlers, verifier=self.verifier)
        self.loop=CognitiveLoop(self.engine, RuleBasedPlanner(), self.tools, self.executor, verifier=self.verifier)
    def tearDown(self): self.tmp.cleanup()
    def test_web_artifact_verified(self):
        r=self.loop.run(TaskRequest(goal="Crea una página web de ciencias",success_criteria=[CRITERION]))
        self.assertEqual(r.task.status.value,"succeeded")
        self.assertTrue(any(a["name"]=="index.html" for a in r.task.artifacts))
        self.assertEqual(r.verification["status"],"verified")
    def test_analysis_artifact_verified(self):
        r=self.loop.run(TaskRequest(goal="Analiza mi idea de app",success_criteria=[CRITERION]))
        self.assertEqual(r.task.status.value,"succeeded")
        self.assertTrue(any(a["name"]=="analisis_local.json" for a in r.task.artifacts))
        self.assertEqual(r.verification["status"],"verified")
    def test_document_artifact_verified(self):
        r=self.loop.run(TaskRequest(goal="Escribe un documento sobre Elyntro",success_criteria=[CRITERION]))
        self.assertEqual(r.task.status.value,"succeeded")
        self.assertTrue(any(a["name"]=="resultado.md" for a in r.task.artifacts))
        self.assertEqual(r.verification["status"],"verified")
    def test_wrong_artifact_does_not_verify(self):
        self.assertFalse(validator(CRITERION, [], {"goal":"Crea una página web","artifacts":[{"name":"resultado.md","content":"# Not a web page"}]}))

if __name__ == "__main__": unittest.main(verbosity=2)


def test_direct_chat_bypasses_planner_and_returns_api_model(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "_groq_chat", lambda goal, direct=False: {
        "configured": True, "text": "GROQ_OK openai/gpt-oss-20b", "model": "openai/gpt-oss-20b",
        "requested_model": "openai/gpt-oss-20b", "response_id": "resp_test", "usage": {"total_tokens": 8}, "error": None
    })
    client = app_module.app.test_client()
    response = client.post('/api/chat', json={"goal": "Responde únicamente GROQ_OK y el nombre del modelo"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["path"] == "direct-chat-no-planner"
    assert body["returned_model"] == "openai/gpt-oss-20b"
    assert body["response_id"] == "resp_test"


def test_direct_chat_reports_provider_error(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "_groq_chat", lambda goal, direct=False: {
        "configured": True, "text": None, "model": "openai/gpt-oss-20b",
        "requested_model": "openai/gpt-oss-20b", "error": "HTTP 403 · denied"
    })
    client = app_module.app.test_client()
    response = client.post('/api/chat', json={"goal": "Responde únicamente GROQ_OK"})
    assert response.status_code == 502
    assert response.get_json()["error"] == "HTTP 403 · denied"


@pytest.mark.parametrize("adapter_name", ["_groq_chat", "_groq_messages"])
def test_groq_credentials_are_not_forwarded_on_redirects_or_echoed_in_errors(monkeypatch, adapter_name):
    import json
    from io import BytesIO
    from urllib.error import HTTPError
    from urllib.request import HTTPRedirectHandler

    import app as app_module

    api_key = "test-groq-secret"
    monkeypatch.setenv("GROQ_API_KEY", api_key)
    observed = {}

    def fake_urlopen(request, timeout):
        observed["timeout"] = timeout
        observed["regular_auth"] = request.headers.get("Authorization")
        observed["unredirected_auth"] = request.unredirected_hdrs.get("Authorization")
        observed["redirect_auth"] = []
        for status in (302, 303, 307, 308):
            try:
                redirected = HTTPRedirectHandler().redirect_request(
                    request,
                    None,
                    status,
                    "Redirect",
                    {},
                    "https://untrusted.example/redirect",
                )
            except HTTPError as redirect_error:
                assert redirect_error.code == status
                observed["redirect_auth"].append(None)
            else:
                observed["redirect_auth"].append(
                    redirected.get_header("Authorization") if redirected else None
                )
        raise HTTPError(
            request.full_url,
            401,
            "unauthorized",
            {},
            BytesIO(json.dumps({"error": {"message": f"invalid key {api_key}"}}).encode()),
        )

    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
    if adapter_name == "_groq_chat":
        result = app_module._groq_chat("test")
    else:
        result = app_module._groq_messages([{"role": "user", "content": "test"}])

    assert "timeout" in observed, result
    assert observed["timeout"] in {45, 55}
    assert observed["regular_auth"] is None
    assert api_key in observed["unredirected_auth"]
    assert observed["redirect_auth"] == [None, None, None, None]
    assert api_key not in result["error"]
    assert "401" in result["error"]


def test_intent_router_has_no_ui_mode_dependency():
    import app as app_module
    assert app_module.classify_request("Explícame la fotosíntesis") == "chat"
    assert app_module.classify_request("Crea una página web para mi proyecto") == "create"
    assert app_module.classify_request("Analiza esta idea de aplicación") == "analyze"
    assert app_module.classify_request("Investiga fuentes para este tema") == "research"


def test_unified_execute_routes_chat_and_reports_model(monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "_groq_chat", lambda goal, direct=False: {
        "configured": True, "text": "Respuesta de prueba", "model": "openai/gpt-oss-20b",
        "requested_model": "openai/gpt-oss-20b", "response_id": "resp_test", "error": None
    })
    response = app_module.app.test_client().post('/api/execute', json={"goal": "Explícame qué es una célula"})
    assert response.status_code == 200
    body = response.get_json()
    assert body["kind"] == "answer"
    assert body["route"] == "chat"
    assert body["returned_model"] == "openai/gpt-oss-20b"
    assert body["verification"]["status"] == "not_applicable"


def test_unified_execute_rejects_empty_input():
    import app as app_module
    response = app_module.app.test_client().post('/api/execute', json={"goal": "   "})
    assert response.status_code == 400


def test_health_reports_not_configured_when_groq_key_missing(monkeypatch):
    import app as app_module
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    response = app_module.app.test_client().get('/api/health')
    assert response.status_code == 200
    body = response.get_json()
    assert body['model_provider'] == 'not_configured'
    assert body['model'] is None


def test_direct_chat_reports_missing_groq_key_without_mocking_provider(monkeypatch):
    import app as app_module
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    response = app_module.app.test_client().post('/api/chat', json={'goal': 'Responde solo OK'})
    assert response.status_code == 502
    body = response.get_json()
    assert 'GROQ_API_KEY' in body['error']


def test_render_config_does_not_extract_zips():
    from pathlib import Path
    rendered = Path('render.yaml').read_text(encoding='utf-8')
    assert 'ZipFile' not in rendered
    assert 'pip install -r requirements.lock' in rendered
    assert 'gunicorn --bind 0.0.0.0:$PORT app:app' in rendered


@pytest.mark.parametrize("endpoint", ["/api/execute", "/api/agent/execute"])
def test_public_execution_route_runs_persisted_multistep_engine(tmp_path, monkeypatch, endpoint):
    import app as app_module

    engine = Engine(SQLiteStore(str(Path(tmp_path) / "api-engine.db")))
    calls = {"create": 0}
    original_create = app_module.HANDLERS["create"]

    def flaky_create(inputs, context):
        calls["create"] += 1
        if calls["create"] == 1:
            raise RuntimeError("transient tool failure")
        return original_create(inputs, context)

    handlers = {**app_module.HANDLERS, "create": flaky_create}
    executor = TaskExecutor(engine, handlers=handlers, verifier=app_module.verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), app_module.TOOLS, executor, verifier=app_module.verifier)
    monkeypatch.setattr(app_module, "loop", loop)

    response = app_module.app.test_client().post(
        endpoint,
        json={"goal": "Crea una página web de presentación científica"},
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["kind"] == "task"
    assert body["task"]["status"] == "succeeded"
    assert [step["handler_key"] for step in body["plan"]] == ["research", "create", "verify"]
    assert body["verification"]["status"] == "verified"
    assert calls["create"] == 2
    assert any(event["event_type"] == "step_attempt_failed" for event in body["events"])
    assert sum(event["event_type"] == "step_succeeded" for event in body["events"]) == 3
    task_id = body["task"]["task_id"]
    persisted = engine.store.get_task(task_id)
    events = engine.store.list_events(task_id)
    assert any(artifact["name"] == "index.html" for artifact in persisted.artifacts)
    assert [event["event_type"] for event in events].count("step_succeeded") == 3
    assert any(event["event_type"] == "independent_verification" for event in events)


def test_public_execution_route_does_not_complete_unavailable_research(monkeypatch, tmp_path):
    import app as app_module

    engine = Engine(SQLiteStore(str(Path(tmp_path) / "incomplete-engine.db")))
    executor = TaskExecutor(engine, handlers=app_module.HANDLERS, verifier=app_module.verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), app_module.TOOLS, executor, verifier=app_module.verifier)
    monkeypatch.setattr(app_module, "loop", loop)

    response = app_module.app.test_client().post(
        "/api/execute",
        json={"goal": "Investiga fuentes fiables sobre un tema reciente"},
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["task"]["status"] == "failed"
    assert body["execution_status"] == "failed"
    assert body["verification"]["status"] == "failed"
    assert body["ok"] is False


def test_analyze_without_provider_does_not_verify_placeholder_artifact(monkeypatch, tmp_path):
    import app as app_module

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    engine = Engine(SQLiteStore(str(Path(tmp_path) / "no-provider.db")))
    executor = TaskExecutor(engine, handlers=app_module.HANDLERS, verifier=app_module.verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), app_module.TOOLS, executor, verifier=app_module.verifier)
    monkeypatch.setattr(app_module, "loop", loop)

    response = app_module.app.test_client().post("/api/execute", json={"goal": "Analiza esta idea de aplicación"})

    body = response.get_json()
    assert response.status_code == 201
    assert body["task"]["status"] == "failed"
    assert body["ok"] is False
    assert body["verification"]["status"] == "failed"


def test_artifact_verifier_rejects_truncated_html():
    import app as app_module

    assert not app_module.validate_artifact(
        CRITERION,
        [],
        {
            "goal": "Crea una página web de ciencias",
            "artifacts": [{"name": "index.html", "content": "<!doctype html><html><body>incompleto", "media_type": "text/html"}],
        },
    )


def test_task_and_artifacts_survive_app_store_recreation(tmp_path, monkeypatch):
    import importlib
    import app as app_module

    db_path = Path(tmp_path) / "restart.db"
    monkeypatch.setenv("ELYNTRO_DB_PATH", str(db_path))
    app_module = importlib.reload(app_module)
    client = app_module.app.test_client()

    created = client.post("/api/execute", json={"goal": "Crea una página web de prueba"})
    assert created.status_code == 201
    task_id = created.get_json()["task"]["task_id"]
    expected_artifact = next(item for item in created.get_json()["task"]["artifacts"] if item["name"] == "index.html")

    reloaded_app = importlib.reload(app_module)
    loaded = reloaded_app.app.test_client().get(f"/api/tasks/{task_id}")

    assert loaded.status_code == 200
    persisted = loaded.get_json()["task"]
    assert persisted["status"] == "succeeded"
    assert expected_artifact in persisted["artifacts"]
    assert any(event["event_type"] == "step_succeeded" for event in loaded.get_json()["events"])

    downloaded = reloaded_app.app.test_client().get(f"/api/tasks/{task_id}/artifacts/index.html")
    assert downloaded.status_code == 200
    assert "</html>" in downloaded.get_data(as_text=True)
