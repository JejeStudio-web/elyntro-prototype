from __future__ import annotations
import json
import sys
import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# These tests require the same Flask dependency installed by Render.
try:
    import app as elyntro_app
except ModuleNotFoundError as exc:
    if exc.name == "flask":
        elyntro_app = None
    else:
        raise


def test_artifact_validator_json():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    result = elyntro_app._validate_artifact({"name": "data.json", "media_type": "application/json", "content": json.dumps({"ok": True})})
    assert result["status"] == "static_checks_passed"
    assert all(check["passed"] for check in result["checks"])


def test_artifact_validator_rejects_invalid_json():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    result = elyntro_app._validate_artifact({"name": "data.json", "media_type": "application/json", "content": "{broken"})
    assert result["status"] == "static_checks_failed"


def test_artifact_validator_python_syntax_without_execution():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    good = elyntro_app._validate_artifact({"name": "main.py", "media_type": "text/x-python", "content": "def main():\n    return 3\n"})
    bad = elyntro_app._validate_artifact({"name": "main.py", "media_type": "text/x-python", "content": "def :\n"})
    assert good["status"] == "static_checks_passed"
    assert bad["status"] == "static_checks_failed"


def test_local_urls_are_not_public_fetch_targets():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    assert not elyntro_app._public_http_url("http://127.0.0.1/admin")
    assert not elyntro_app._public_http_url("file:///etc/passwd")
    assert not elyntro_app._public_http_url("http://localhost/")


def test_html_extractor_skips_script_and_navigation():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    parser = elyntro_app._ReadableHTML()
    parser.feed("<html><body><nav>MENU</nav><main>Useful page content</main><script>bad()</script></body></html>")
    extracted = " ".join(parser.parts)
    assert "Useful page content" in extracted
    assert "MENU" not in extracted
    assert "bad()" not in extracted


def test_public_research_task_retrieves_sources_and_uses_them_for_synthesis(monkeypatch, tmp_path):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    from elyntro import CognitiveLoop, EvidenceVerifier
    from elyntro.engine import Engine
    from elyntro.planner import RuleBasedPlanner
    from elyntro.store import SQLiteStore
    from elyntro.task_executor import TaskExecutor

    store = SQLiteStore(tmp_path / "research.db")
    engine = Engine(store)
    verifier = EvidenceVerifier()
    verifier.register(elyntro_app.CRITERION, elyntro_app.validate_artifact)
    executor = TaskExecutor(engine, handlers=elyntro_app.HANDLERS, verifier=verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), elyntro_app.TOOLS, executor, verifier=verifier)
    monkeypatch.setattr(elyntro_app, "store", store)
    monkeypatch.setattr(elyntro_app, "engine", engine)
    monkeypatch.setattr(elyntro_app, "executor", executor)
    monkeypatch.setattr(elyntro_app, "loop", loop)

    searched = []

    def search(query, limit=6):
        searched.append(query)
        return ([{"title": "Official technical reference", "url": "https://example.org/reference", "snippet": "A documented reference excerpt."}], None)

    def fetch(url, max_chars=8500):
        return {"title": "Official technical reference", "url": url, "content": "A documented source page with enough readable text to support a source-grounded summary." * 3}

    def provider(messages, *, max_tokens=1800, temperature=0.2, json_mode=False):
        if max_tokens == 220:
            return {"text": json.dumps({"queries": []})}
        request = json.loads(messages[-1]["content"])
        assert request["sources"][0]["url"] == "https://example.org/reference"
        return {"text": "La fuente oficial describe el tema solicitado: https://example.org/reference", "model": "test-model"}

    monkeypatch.setattr(elyntro_app, "_web_search", search)
    monkeypatch.setattr(elyntro_app, "_fetch_page", fetch)
    monkeypatch.setattr(elyntro_app, "_groq_messages", provider)

    response = elyntro_app.app.test_client().post("/api/execute", json={"goal": "Investiga fuentes sobre el uso de context windows"})
    body = response.get_json()

    assert response.status_code == 201
    assert body["task"]["status"] == "succeeded"
    assert searched
    report = next(item for item in body["task"]["artifacts"] if item["name"] == "investigacion_fuentes.json")
    assert report["content"]["retrieval_level"] == "full_pages"
    assert report["content"]["sources"][0]["url"] == "https://example.org/reference"
    analysis = next(item for item in body["task"]["artifacts"] if item["name"] == "analisis_local.json")
    assert analysis["content"]["analysis"] == "La fuente oficial describe el tema solicitado: https://example.org/reference"
    assert analysis["content"]["source_urls"] == ["https://example.org/reference"]


def test_research_task_without_provider_keeps_sources_but_does_not_claim_success(monkeypatch, tmp_path):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    from elyntro import CognitiveLoop, EvidenceVerifier
    from elyntro.engine import Engine
    from elyntro.planner import RuleBasedPlanner
    from elyntro.store import SQLiteStore
    from elyntro.task_executor import TaskExecutor

    store = SQLiteStore(tmp_path / "research-unavailable.db")
    engine = Engine(store)
    verifier = EvidenceVerifier()
    verifier.register(elyntro_app.CRITERION, elyntro_app.validate_artifact)
    executor = TaskExecutor(engine, handlers=elyntro_app.HANDLERS, verifier=verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), elyntro_app.TOOLS, executor, verifier=verifier)
    monkeypatch.setattr(elyntro_app, "store", store)
    monkeypatch.setattr(elyntro_app, "engine", engine)
    monkeypatch.setattr(elyntro_app, "executor", executor)
    monkeypatch.setattr(elyntro_app, "loop", loop)
    monkeypatch.setattr(elyntro_app, "_web_search", lambda query, limit=6: ([{
        "title": "Official technical reference",
        "url": "https://example.org/reference",
        "snippet": "A documented reference excerpt.",
    }], None))
    monkeypatch.setattr(elyntro_app, "_fetch_page", lambda url, max_chars=8500: {
        "title": "Official technical reference",
        "url": url,
        "content": "A documented source page with enough readable text to support a source-grounded summary." * 3,
    })
    monkeypatch.setattr(elyntro_app, "_groq_messages", lambda messages, **kwargs: {"text": None, "error": "GROQ_API_KEY no está configurada."})

    response = elyntro_app.app.test_client().post("/api/execute", json={"goal": "Investiga fuentes sobre el uso de context windows"})
    body = response.get_json()
    task = body["task"]

    assert response.status_code == 201
    assert body["ok"] is False
    assert task["status"] == "failed"
    assert any(item["name"] == "investigacion_fuentes.json" for item in task["artifacts"])
    assert any(event["event_type"] == "step_attempt_failed" for event in body["events"])
    assert body["verification"]["status"] == "failed"


def test_research_verifier_rejects_unreferenced_analysis():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    context = {
        "goal": "Investiga fuentes sobre el tema",
        "artifacts": [
            {"name": "investigacion_fuentes.json", "content": {"retrieval_level": "full_pages", "sources": [{
                "title": "Official source", "url": "https://example.org/reference", "content": "Relevant source text.",
            }]}},
            {"name": "analisis_local.json", "content": {"analysis": "Claim without citations.", "source_urls": []}},
        ],
    }

    assert elyntro_app.validate_artifact(elyntro_app.CRITERION, [], context) is False


def test_research_verifier_requires_url_to_be_cited_in_analysis_text():
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    context = {
        "goal": "Investiga fuentes sobre el tema",
        "artifacts": [
            {"name": "investigacion_fuentes.json", "content": {"sources": [{
                "title": "Official source", "url": "https://example.org/reference", "provider": "tavily", "content": "Relevant source text.",
            }]}},
            {"name": "analisis_local.json", "content": {
                "analysis": "Claim without a source link.",
                "source_urls": ["https://example.org/reference"],
            }},
        ],
    }

    assert elyntro_app.validate_artifact(elyntro_app.CRITERION, [], context) is False


def test_web_search_reports_provider_challenge_instead_of_empty_success(monkeypatch):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")

    class ChallengeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b"<html><body>Unfortunately, bots use DuckDuckGo too. Please complete the following challenge.</body></html>"

    monkeypatch.setenv("ELYNTRO_SEARCH_PROVIDER", "duckduckgo")
    monkeypatch.setattr(elyntro_app.urllib.request, "urlopen", lambda *args, **kwargs: ChallengeResponse())

    results, error = elyntro_app._web_search("official docs", limit=3)

    assert results == []
    assert error == "search_provider_challenge"


def test_tavily_search_provider_normalizes_documented_response(monkeypatch):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    from search_providers import TavilySearchProvider

    captured = {}

    class SearchResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            return json.dumps({"results": [{
                "title": "Official source",
                "url": "https://example.org/reference",
                "content": "Retrieved excerpt",
                "score": 0.91,
            }]}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return SearchResponse()

    monkeypatch.setattr("search_providers.socket.getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("8.8.8.8", 443)),
    ])
    monkeypatch.setattr("search_providers.urllib.request.urlopen", fake_urlopen)

    results, error = TavilySearchProvider("test-token").search("official reference", limit=4)

    assert error is None
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["authorization"] == "Bearer test-token"
    assert captured["payload"] == {
        "query": "official reference",
        "search_depth": "basic",
        "max_results": 4,
        "include_answer": False,
        "include_raw_content": False,
    }
    assert results == [{
        "title": "Official source",
        "url": "https://example.org/reference",
        "snippet": "Retrieved excerpt",
        "provider": "tavily",
        "score": 0.91,
    }]


def test_tavily_provider_reports_missing_credentials_without_network(monkeypatch):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    from search_providers import TavilySearchProvider

    called = []
    monkeypatch.setattr("search_providers.urllib.request.urlopen", lambda *args, **kwargs: called.append(True))

    results, error = TavilySearchProvider("").search("query")

    assert results == []
    assert error == "search_provider_not_configured"
    assert called == []


def test_tavily_selection_feeds_provenanced_sources_into_research_pipeline(monkeypatch, tmp_path):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    import search_providers
    from elyntro import CognitiveLoop, EvidenceVerifier
    from elyntro.engine import Engine
    from elyntro.planner import RuleBasedPlanner
    from elyntro.store import SQLiteStore
    from elyntro.task_executor import TaskExecutor

    store = SQLiteStore(tmp_path / "tavily-pipeline.db")
    engine = Engine(store)
    verifier = EvidenceVerifier()
    verifier.register(elyntro_app.CRITERION, elyntro_app.validate_artifact)
    executor = TaskExecutor(engine, handlers=elyntro_app.HANDLERS, verifier=verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), elyntro_app.TOOLS, executor, verifier=verifier)
    monkeypatch.setattr(elyntro_app, "store", store)
    monkeypatch.setattr(elyntro_app, "engine", engine)
    monkeypatch.setattr(elyntro_app, "executor", executor)
    monkeypatch.setattr(elyntro_app, "loop", loop)
    monkeypatch.setenv("ELYNTRO_SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-token")
    monkeypatch.setattr(search_providers.socket, "getaddrinfo", lambda *args, **kwargs: [
        (2, 1, 6, "", ("8.8.8.8", 443)),
    ])

    class SearchResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size):
            return json.dumps({"results": [{
                "title": "Official technical reference",
                "url": "https://example.org/reference",
                "content": "A documented source excerpt.",
            }]}).encode()

    monkeypatch.setattr(search_providers.urllib.request, "urlopen", lambda request, timeout: SearchResponse())
    monkeypatch.setattr(elyntro_app, "_fetch_page", lambda url, max_chars=8500: {
        "title": "Official technical reference",
        "url": url,
        "content": "A documented source page with enough readable text to support a source-grounded summary." * 3,
    })

    def provider(messages, *, max_tokens=1800, temperature=0.2, json_mode=False):
        if max_tokens == 220:
            return {"text": json.dumps({"queries": []})}
        return {"text": "La fuente recuperada respalda esta síntesis: https://example.org/reference", "model": "test-model"}

    monkeypatch.setattr(elyntro_app, "_groq_messages", provider)

    response = elyntro_app.app.test_client().post("/api/execute", json={"goal": "Investiga fuentes sobre el tema de prueba"})
    body = response.get_json()

    assert response.status_code == 201
    assert body["task"]["status"] == "succeeded"
    report = next(item for item in body["task"]["artifacts"] if item["name"] == "investigacion_fuentes.json")
    source = report["content"]["sources"][0]
    assert source["provider"] == "tavily"
    assert source["url"] == "https://example.org/reference"
    assert source["search_snippet"] == "A documented source excerpt."
    assert any("no es verificación independiente" in note for note in report["content"]["limitations"])
    analysis = next(item for item in body["task"]["artifacts"] if item["name"] == "analisis_local.json")
    assert analysis["content"]["source_urls"] == ["https://example.org/reference"]
    assert analysis["content"]["sources"][0]["provider"] == "tavily"
    assert analysis["content"]["sources"][0]["url"] == "https://example.org/reference"
    assert analysis["content"]["sources"][0]["source_id"] == 1
    assert analysis["content"]["sources"][0]["title"] == source["title"]
    assert "https://example.org/reference" in analysis["content"]["analysis"]
    assert any("no constituye verificación independiente" in note for note in analysis["content"]["limitations"])


def test_unconfigured_tavily_pipeline_reports_no_sources_and_fails(monkeypatch, tmp_path):
    if elyntro_app is None:
        pytest.skip("Flask no está instalado en el entorno de construcción")
    from elyntro import CognitiveLoop, EvidenceVerifier
    from elyntro.engine import Engine
    from elyntro.planner import RuleBasedPlanner
    from elyntro.store import SQLiteStore
    from elyntro.task_executor import TaskExecutor
    import search_providers

    store = SQLiteStore(tmp_path / "tavily-unconfigured.db")
    engine = Engine(store)
    verifier = EvidenceVerifier()
    verifier.register(elyntro_app.CRITERION, elyntro_app.validate_artifact)
    executor = TaskExecutor(engine, handlers=elyntro_app.HANDLERS, verifier=verifier)
    loop = CognitiveLoop(engine, RuleBasedPlanner(), elyntro_app.TOOLS, executor, verifier=verifier)
    monkeypatch.setattr(elyntro_app, "store", store)
    monkeypatch.setattr(elyntro_app, "engine", engine)
    monkeypatch.setattr(elyntro_app, "executor", executor)
    monkeypatch.setattr(elyntro_app, "loop", loop)
    monkeypatch.setenv("ELYNTRO_SEARCH_PROVIDER", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    network_calls = []
    monkeypatch.setattr(search_providers.urllib.request, "urlopen", lambda *args, **kwargs: network_calls.append(True))

    response = elyntro_app.app.test_client().post("/api/execute", json={"goal": "Investiga fuentes sobre el tema sin credenciales"})
    body = response.get_json()
    research = next(item for item in body["task"]["artifacts"] if item["name"] == "investigacion_fuentes.json")

    assert response.status_code == 201
    assert body["ok"] is False
    assert body["task"]["status"] == "failed"
    assert research["content"]["sources"] == []
    assert "search_provider_not_configured" in research["content"]["errors"]
    assert network_calls == []
