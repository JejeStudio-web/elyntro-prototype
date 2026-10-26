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
    assert response.get_json()["data"][0]["capabilities"]["streaming"] is False


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
        seen["auth"] = request.get_header("Authorization")
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
    ({"stream": True, "messages": [{"role": "user", "content": "hi"}]}, "streaming_not_supported"),
    ({"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}, "multimodal_not_supported"),
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
