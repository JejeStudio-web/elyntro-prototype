from pathlib import Path


HTML = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")


def test_attachment_content_is_not_written_to_persistent_conversation_history():
    assert 'history.push({ role: "user", content: fullPrompt });' not in HTML
    assert "contenido no guardado en el historial" in HTML
    assert "const storedUserMessage = prompt +" in HTML


def test_user_can_clear_saved_local_history():
    assert 'id="clear-history"' in HTML
    assert '$("clear-history").addEventListener("click"' in HTML
    assert "localStorage.removeItem(HISTORY_KEY)" in HTML
    assert "window.confirm(" in HTML
