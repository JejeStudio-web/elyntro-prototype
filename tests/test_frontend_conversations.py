from pathlib import Path


def test_frontend_exposes_multiple_local_conversations_and_mobile_switcher():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="conversation-list"' in html
    assert 'id="mobile-conversation-select"' in html
    assert 'id="mobile-new-conversation"' in html
    assert 'elyntro.conversations.v2' in html
    assert 'elyntro.conversation.v1' in html  # legacy history migration
    assert "function switchConversation(id)" in html
    assert "function deleteConversation(id)" in html
    assert "while (history.length > 40) history.shift();" in html


def test_conversation_storage_does_not_include_attachment_payloads():
    html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    assert 'current.messages = history.slice(-40);' in html
    assert 'attachedText = null;' in html
    assert 'function persistConversations()' in html
