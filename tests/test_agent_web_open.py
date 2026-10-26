import json


def test_web_open_rejects_non_text_url():
    import app as mod

    result = mod._agent_tool_open_page({"url": ["https://example.com"]})
    assert result["ok"] is False


def test_web_open_rejects_url_that_is_not_public(monkeypatch):
    import app as mod

    monkeypatch.setattr(mod, "_public_http_url", lambda url: False)
    result = mod._agent_tool_open_page({"url": "http://127.0.0.1/admin"})
    assert result["ok"] is False
    assert "no está permitida" in result["error"]


def test_web_open_returns_bounded_page_text_for_allowed_public_url(monkeypatch):
    import app as mod

    monkeypatch.setattr(mod, "_public_http_url", lambda url: True)
    monkeypatch.setattr(mod, "_fetch_page", lambda url, max_chars=8500: {
        "title": "Página de prueba", "url": url, "content": "Contenido público de prueba" * 100
    })
    result = mod._agent_tool_open_page({"url": "https://example.com/guide"})

    assert result["ok"] is True
    assert result["page"]["title"] == "Página de prueba"
    assert "evidencia no confiable" in result["summary"]


def test_tool_agent_advertises_page_opening_capability(monkeypatch):
    import app as mod

    responses = [
        {"text": json.dumps({
            "action": "tool", "tool": "web_open",
            "arguments": {"url": "https://example.com/guide"}
        })},
        {"text": json.dumps({"action": "final", "answer": "He consultado el texto de la página."})},
    ]
    seen = []

    def fake_model_call(messages, **options):
        seen.extend(m.get("content", "") for m in messages if m.get("role") == "system")
        return responses.pop(0)

    monkeypatch.setattr(mod, "_groq_messages", fake_model_call)
    monkeypatch.setattr(mod, "_public_http_url", lambda url: True)
    monkeypatch.setattr(mod, "_fetch_page", lambda url, max_chars=8500: {
        "title": "Guía", "url": url, "content": "Texto de ejemplo de una página pública."
    })
    result = mod._run_tool_agent("Resume https://example.com/guide", [])

    assert result["ok"] is True
    assert result["events"][0]["tool"] == "web_open"
    assert result["events"][0]["status"] == "succeeded"
    assert any("web_open" in prompt for prompt in seen)
