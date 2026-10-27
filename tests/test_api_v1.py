import json
from io import BytesIO

import pytest


@pytest.fixture
def api_client(monkeypatch):
    import app as app_module
    monkeypatch.setenv("ELYNTRO_API_KEY", "test-client-key")
    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("ELYNTRO_MODEL_API_KEY", "test-provider-key")
    monkeypatch.setenv("ELYNTRO_MODEL_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("ELYNTRO_MODEL", "test-model")
    return app_module.app.test_client()


def auth():
    return {"Authorization": "Bearer test-client-key"}


def test_v1_models_requires_client_auth(api_client):
    response = api_client.get("/api/v1/models")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_api_key"


def test_v1_models_returns_only_configured_model(api_client):
    response = api_client.get("/api/v1/models", headers=auth())
    assert response.status_code == 200
    assert response.get_json()["data"][0]["id"] == "test-model"
    assert response.get_json()["data"][0]["capabilities"]["streaming"] is True


def test_v1_chat_completion_uses_configured_compatible_provider(api_client, monkeypatch):
    import elyntro_api_v1 as module

    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({
                "id": "provider-id", "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "API_OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            }).encode()

    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization") or request.unredirected_hdrs.get("Authorization")
        seen["payload"] = json.loads(request.data)
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "model": "test-model", "messages": [{"role": "user", "content": "test"}]
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body["choices"][0]["message"]["content"] == "API_OK"
    assert body["usage"]["total_tokens"] == 6
    assert seen["url"] == "https://provider.example/v1/chat/completions"
    assert seen["auth"] == "Bearer test-provider-key"
    assert seen["payload"]["messages"] == [{"role": "user", "content": "test"}]


@pytest.mark.parametrize("payload,code", [
    ({"messages": [{"role": "user", "content": "hi"}], "max_tokens": 999999}, "invalid_max_tokens"),
    ({"messages": [{"role": "user", "content": "hi"}], "model": "other"}, "model_not_available"),
])
def test_v1_chat_rejects_unsupported_or_invalid_requests(api_client, payload, code):
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json=payload)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == code


def test_v1_chat_stays_closed_without_client_key(api_client, monkeypatch):
    monkeypatch.delenv("ELYNTRO_API_KEY")
    response = api_client.post("/api/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}]
    })
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "api_not_configured"


def test_v1_models_reports_missing_provider_configuration(api_client, monkeypatch):
    monkeypatch.delenv("ELYNTRO_MODEL_API_KEY")
    response = api_client.get("/api/v1/models", headers=auth())
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "provider_not_configured"


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
def test_v1_chat_rejects_non_finite_temperature(api_client, temperature):
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": temperature,
    })
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_temperature"


def test_v1_chat_rejects_empty_provider_answer(api_client, monkeypatch):
    import elyntro_api_v1 as module

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "   "}, "finish_reason": "stop"}],
                "usage": {},
            }).encode()

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: FakeResponse())
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "provider_empty_response"


def test_v1_chat_normalizes_untrusted_provider_usage(api_client, monkeypatch):
    import elyntro_api_v1 as module

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return json.dumps({
                "model": "test-model",
                "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": "4", "completion_tokens": -3, "total_tokens": None},
            }).encode()

    monkeypatch.setattr(module, "urlopen", lambda request, timeout: FakeResponse())
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "messages": [{"role": "user", "content": "hi"}],
    })
    assert response.status_code == 200
    assert response.get_json()["usage"] == {
        "prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 0,
    }


def test_v1_routes_are_registered_once(api_client):
    import app as app_module

    paths = [rule.rule for rule in app_module.app.url_map.iter_rules()]
    assert paths.count("/api/v1/models") == 1
    assert paths.count("/api/v1/chat/completions") == 1


def test_v1_defaults_to_the_existing_groq_runtime(api_client, monkeypatch):
    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    response = api_client.get("/api/v1/models", headers=auth())
    assert response.status_code == 200
    assert response.get_json()["data"][0]["id"] == "openai/gpt-oss-20b"
    assert response.get_json()["data"][0]["owned_by"] == "groq"


def test_v1_model_pool_lists_multiple_configured_models(api_client, monkeypatch):
    monkeypatch.setenv("ELYNTRO_MODEL_POOL", json.dumps([
        {"id": "fast", "provider": "groq", "model": "llama-3.3-70b-versatile",
         "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY", "primary": True},
        {"id": "reasoner", "provider": "openai", "model": "gpt-4.1-mini",
         "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    ]))
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    response = api_client.get("/api/v1/models", headers=auth())
    assert response.status_code == 200
    models = response.get_json()["data"]
    assert [model["id"] for model in models] == ["fast", "reasoner"]
    assert models[0]["elyntro"]["primary"] is True
    assert models[1]["owned_by"] == "openai"


def test_v1_model_pool_routes_to_selected_upstream(api_client, monkeypatch):
    import elyntro_api_v1 as module
    monkeypatch.setenv("ELYNTRO_MODEL_POOL", json.dumps([
        {"id": "fast", "provider": "groq", "model": "llama-3.3-70b-versatile",
         "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY", "primary": True},
        {"id": "reasoner", "provider": "openai", "model": "gpt-4.1-mini",
         "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    ]))
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    seen = {}
    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            return json.dumps({"model": "gpt-4.1-mini", "choices": [{"message": {"role": "assistant", "content": "ROUTED_OK"}, "finish_reason": "stop"}], "usage": {}}).encode()
    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization") or req.unredirected_hdrs.get("Authorization")
        seen["model"] = json.loads(req.data)["model"]
        return FakeResponse()
    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "model": "reasoner", "messages": [{"role": "user", "content": "test"}]
    })
    assert response.status_code == 200
    assert response.get_json()["model"] == "reasoner"
    assert response.get_json()["elyntro"]["provider"] == "openai"
    assert seen == {"url": "https://api.openai.com/v1/chat/completions", "auth": "Bearer test-openai-key", "model": "gpt-4.1-mini"}


def test_first_party_runtime_uses_same_primary_model_pool(api_client, monkeypatch):
    import app as app_module
    monkeypatch.setenv("ELYNTRO_MODEL_POOL", json.dumps([
        {"id": "fast", "provider": "groq", "model": "llama-3.3-70b-versatile",
         "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
        {"id": "reasoner", "provider": "openai", "model": "gpt-4.1-mini",
         "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY", "primary": True},
    ]))
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    config = app_module._model_provider_config()
    assert config["provider"] == "openai"
    assert config["model"] == "gpt-4.1-mini"
    assert config["model_alias"] == "reasoner"
    assert config["api_key"] == "test-openai-key"
