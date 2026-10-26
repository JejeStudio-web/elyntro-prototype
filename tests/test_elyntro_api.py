import json


def test_models_endpoint_reports_configured_provider_without_secrets(monkeypatch):
    import app as mod

    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ELYNTRO_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("ELYNTRO_MODEL", "elyntro-local-test")
    monkeypatch.setenv("ELYNTRO_MODEL_API_KEY", "must-not-be-returned")

    response = mod.app.test_client().get("/api/v1/models")
    body = response.get_json()

    assert response.status_code == 200
    assert body["configured"] is True
    assert body["provider"] == "ollama"
    assert body["data"][0]["id"] == "elyntro-local-test"
    assert "must-not-be-returned" not in response.get_data(as_text=True)


def test_chat_completions_returns_openai_compatible_response(monkeypatch):
    import app as mod

    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "test-model")
    monkeypatch.setattr(mod, "_groq_messages", lambda *args, **kwargs: {
        "text": "Hola desde Elyntro", "model": "test-model", "provider": "groq",
        "response_id": "provider-response", "usage": {
            "prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7
        }
    })

    response = mod.app.test_client().post("/api/v1/chat/completions", json={
        "model": "elyntro-default",
        "messages": [{"role": "user", "content": "Hola"}]
    })
    body = response.get_json()

    assert response.status_code == 200
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hola desde Elyntro"
    assert body["usage"]["total_tokens"] == 7
    assert body["elyntro"]["provider"] == "groq"


def test_chat_completions_supports_streaming_and_rejects_unknown_models(monkeypatch):
    import app as mod
    from flask import Response

    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ELYNTRO_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("ELYNTRO_MODEL", "local-test")
    observed = {}
    def fake_stream(messages, model, max_tokens, temperature, json_mode=False):
        observed.update(model=model, max_tokens=max_tokens, temperature=temperature, json_mode=json_mode,
                        messages=messages)
        return Response('data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
                        mimetype="text/event-stream")
    monkeypatch.setattr(mod, "_stream_provider_completion", fake_stream)
    client = mod.app.test_client()
    messages = [{"role": "user", "content": "test"}]

    streaming = client.post("/api/v1/chat/completions", json={"messages": messages, "stream": True})
    unknown = client.post("/api/v1/chat/completions", json={"messages": messages, "model": "other-model"})

    assert streaming.status_code == 200
    assert streaming.mimetype == "text/event-stream"
    assert "data: [DONE]" in streaming.get_data(as_text=True)
    assert observed["model"] == "local-test"
    assert observed["messages"] == messages
    assert unknown.status_code == 404
    assert unknown.get_json()["error"]["type"] == "model_not_found"


def test_ollama_adapter_calls_configured_openai_compatible_endpoint(monkeypatch):
    import app as mod

    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ELYNTRO_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("ELYNTRO_MODEL", "local-test")
    monkeypatch.delenv("ELYNTRO_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "local-response",
                "model": "local-test",
                "choices": [{"message": {"content": "Respuesta local"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["authorization"] = request.get_header("Authorization")
        observed["body"] = json.loads(request.data.decode("utf-8"))
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    result = mod._groq_messages([{"role": "user", "content": "test"}])

    assert result["text"] == "Respuesta local"
    assert result["provider"] == "ollama"
    assert observed["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert observed["authorization"] is None
    assert observed["body"]["model"] == "local-test"
    assert observed["timeout"] == 55



def test_versioned_api_requires_configured_api_key(monkeypatch):
    import app as mod

    monkeypatch.setenv("ELYNTRO_API_KEY", "test-elyntro-key")
    client = mod.app.test_client()

    missing = client.get("/api/v1/models")
    wrong = client.get("/api/v1/models", headers={"Authorization": "Bearer wrong-key"})
    valid_bearer = client.get("/api/v1/models", headers={
        "Authorization": "Bearer test-elyntro-key"
    })
    valid_header = client.get("/api/v1/models", headers={
        "X-Elyntro-API-Key": "test-elyntro-key"
    })

    assert missing.status_code == 401
    assert missing.get_json()["error"]["code"] == "invalid_api_key"
    assert wrong.status_code == 401
    assert valid_bearer.status_code == 200
    assert valid_header.status_code == 200
    assert "test-elyntro-key" not in missing.get_data(as_text=True)
    assert "test-elyntro-key" not in valid_bearer.get_data(as_text=True)


def test_versioned_api_remains_backward_compatible_when_key_is_unset(monkeypatch):
    import app as mod

    monkeypatch.delenv("ELYNTRO_API_KEY", raising=False)
    response = mod.app.test_client().get("/api/v1/models")

    assert response.status_code == 200



def test_chat_completions_accepts_bounded_base64_image_parts(monkeypatch):
    import app as mod

    monkeypatch.delenv("ELYNTRO_API_KEY", raising=False)
    monkeypatch.setenv("ELYNTRO_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("ELYNTRO_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("ELYNTRO_MODEL", "vision-local")
    observed = {}
    def fake(messages, **kwargs):
        observed["messages"] = messages
        return {"text": "Veo una imagen", "model": "vision-local", "provider": "ollama"}
    monkeypatch.setattr(mod, "_groq_messages", fake)
    payload = {
        "model": "elyntro-default",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "Describe esta imagen"},
            {"type": "image_url", "image_url": {
                "url": "data:image/png;base64,aGVsbG8=", "detail": "low"
            }}
        ]}]
    }
    response = mod.app.test_client().post("/api/v1/chat/completions", json=payload)
    assert response.status_code == 200
    assert observed["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_chat_completions_rejects_remote_image_urls(monkeypatch):
    import app as mod

    monkeypatch.delenv("ELYNTRO_API_KEY", raising=False)
    response = mod.app.test_client().post("/api/v1/chat/completions", json={
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
        ]}]
    })
    assert response.status_code == 400
    assert response.get_json()["error"]["type"] == "invalid_request_error"
