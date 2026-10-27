import base64
import json

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


def test_multimodal_analyze_passes_image_parts_to_configured_model(api_client, monkeypatch):
    import app as app_module

    seen = {}
    monkeypatch.setattr(app_module, "_model_provider_config", lambda: {
        "configured": True, "provider": "test-provider", "model": "vision-model",
        "base_url": "https://provider.example/v1", "api_key": "secret",
    })

    def fake_call(config, messages, **kwargs):
        seen["messages"] = messages
        return {"text": "Veo una imagen.", "provider": "test-provider", "model": "vision-model"}

    monkeypatch.setattr(app_module, "_call_model_config", fake_call)
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    response = api_client.post("/api/multimodal/analyze", json={
        "goal": "Describe esta imagen", "attachments": [{
            "name": "prueba.png", "mime_type": "image/png",
            "data_base64": base64.b64encode(image_bytes).decode("ascii"),
        }],
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body["answer"] == "Veo una imagen."
    assert body["multimodal"]["images_received"] == 1
    user_content = seen["messages"][1]["content"]
    assert user_content[1]["type"] == "image_url"
    assert user_content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("mime,raw", [
    ("image/png", b"not-a-png-file-with-enough-bytes"),
    ("application/x-executable", b"arbitrary binary"),
])
def test_multimodal_analyze_rejects_spoofed_or_unsupported_attachments(api_client, mime, raw):
    response = api_client.post("/api/multimodal/analyze", json={
        "goal": "Analiza esto", "attachments": [{
            "name": "adjunto.bin", "mime_type": mime,
            "data_base64": base64.b64encode(raw).decode("ascii"),
        }],
    })
    assert response.status_code in {400, 415}
    assert response.get_json()["ok"] is False


def test_v1_function_call_executes_only_allowlisted_calculator(api_client, monkeypatch):
    import elyntro_api_v1 as module

    calls = []
    class FakeResponse:
        def __init__(self, data): self.data = data
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit): return json.dumps(self.data).encode()

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        if len(calls) == 1:
            return FakeResponse({"model": "test-model", "choices": [{
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function",
                    "function": {"name": "calculate", "arguments": '{"expression":"2 + 2"}'},
                }]}, "finish_reason": "tool_calls",
            }]})
        assert calls[1]["messages"][-1]["role"] == "tool"
        assert '"result": 4' in calls[1]["messages"][-1]["content"] or '"value": 4' in calls[1]["messages"][-1]["content"]
        return FakeResponse({"model": "test-model", "choices": [{
            "message": {"role": "assistant", "content": "El resultado es 4."}, "finish_reason": "stop",
        }], "usage": {"total_tokens": 8}})

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "model": "test-model", "messages": [{"role": "user", "content": "Calcula 2 + 2"}],
        "tools": [{"type": "function", "function": {"name": "calculate"}}],
    })
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["choices"][0]["message"]["content"] == "El resultado es 4."
    assert body["elyntro"]["tool_events"][0]["tool"] == "calculate"
    assert body["elyntro"]["tool_events"][0]["status"] == "completed"


def test_v1_rejects_unapproved_function_tools(api_client):
    response = api_client.post("/api/v1/chat/completions", headers=auth(), json={
        "model": "test-model", "messages": [{"role": "user", "content": "run code"}],
        "tools": [{"type": "function", "function": {"name": "execute_shell"}}],
    })
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "tool_not_allowed"


def test_media_capabilities_never_claim_unconfigured_providers(monkeypatch):
    import app as app_module
    for key in ("OPENAI_API_KEY", "ELYNTRO_IMAGE_API_KEY", "ELYNTRO_IMAGE_API_BASE_URL",
                "ELYNTRO_IMAGE_MODEL", "ELYNTRO_TTS_API_KEY", "ELYNTRO_TTS_API_BASE_URL",
                "ELYNTRO_TTS_MODEL", "ELYNTRO_VIDEO_API_KEY", "ELYNTRO_VIDEO_API_BASE_URL",
                "ELYNTRO_VIDEO_MODEL"):
        monkeypatch.delenv(key, raising=False)
    body = app_module.app.test_client().get("/api/media/capabilities").get_json()
    assert body["capabilities"]["image_generation"]["available"] is False
    assert body["capabilities"]["speech_generation"]["available"] is False
    assert body["capabilities"]["video_generation"]["available"] is False


def test_image_generation_uses_configured_provider_and_returns_image(monkeypatch):
    import base64
    import media_runtime as module
    from app import app

    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")
    monkeypatch.delenv("ELYNTRO_IMAGE_API_BASE_URL", raising=False)
    monkeypatch.delenv("ELYNTRO_IMAGE_API_KEY", raising=False)
    monkeypatch.setenv("ELYNTRO_IMAGE_MODEL", "test-image-model")

    class FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, limit):
            return json.dumps({"data": [{"b64_json": base64.b64encode(b"image bytes").decode()}]}).encode()

    seen = {}
    def fake_urlopen(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization") or request.unredirected_hdrs.get("Authorization")
        seen["body"] = json.loads(request.data)
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    response = app.test_client().post("/api/media/generate-image",
        headers={"Origin": "http://localhost"}, json={"prompt": "A mountain at dawn"})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["data_url"].startswith("data:image/png;base64,")
    assert seen["url"] == "https://api.openai.com/v1/images/generations"
    assert seen["auth"] == "Bearer provider-secret"
    assert "provider-secret" not in response.get_data(as_text=True)


def test_quick_chat_stream_returns_sse_delta_frames(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_model_provider_config", lambda: {
        "configured": True, "provider": "test-provider", "model": "test-model",
        "base_url": "https://provider.example/v1", "api_key": "provider-secret",
    })

    class FakeStream:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def close(self): return None
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Hola"}}]}\n'
            yield b'data: [DONE]\n'

    seen = {}
    def fake_urlopen(request, timeout):
        seen["body"] = json.loads(request.data)
        seen["auth"] = request.get_header("Authorization") or request.unredirected_hdrs.get("Authorization")
        return FakeStream()

    monkeypatch.setattr(app_module.urllib.request, "urlopen", fake_urlopen)
    response = app_module.app.test_client().post("/api/chat/stream", json={
        "goal": "Saluda", "history": [],
    })
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert '"type": "delta", "text": "Hola"' in body
    assert '"type": "done"' in body
    assert seen["body"]["stream"] is True
    assert seen["auth"] == "Bearer provider-secret"
