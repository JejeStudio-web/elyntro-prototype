import json


def test_chat_agent_can_call_bounded_calculator(monkeypatch):
    import app as mod

    responses = [
        {"text": json.dumps({
            "action": "tool",
            "tool": "calculate",
            "arguments": {"expression": "(12 + 8) * 3"},
        })},
        {"text": json.dumps({"action": "final", "answer": "El resultado es 60."}), "model": "test-model"},
    ]
    seen_capabilities = []

    def fake_model_call(messages, **options):
        systems = [m["content"] for m in messages if m.get("role") == "system"]
        seen_capabilities.extend(systems)
        return responses.pop(0)

    monkeypatch.setattr(mod, "_groq_messages", fake_model_call)
    result = mod._run_tool_agent("Calcula (12 + 8) * 3", [])

    assert result["ok"] is True
    assert result["answer"] == "El resultado es 60."
    assert result["status"] == "completed"
    assert result["events"][0]["tool"] == "calculate"
    assert result["events"][0]["status"] == "succeeded"
    assert any("calculate" in item and "web_search" in item for item in seen_capabilities)


def test_agent_calculator_rejects_dynamic_code():
    import app as mod

    result = mod._agent_tool_calculate({"expression": "__import__('os').system('echo unsafe')"})

    assert result["ok"] is False
    assert "no se permiten" in result["error"].casefold()


def test_agent_search_rejects_non_text_queries():
    import app as mod

    result = mod._agent_tool_search({"query": ["not", "text"]})

    assert result["ok"] is False
    assert "texto no vacío" in result["error"].casefold()
