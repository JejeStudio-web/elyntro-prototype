import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'elyntro-engine-v6.2.0')))

# These tests require Flask because app.py is the web entry point. They are run by Render after dependencies install.
def test_router_uses_model_and_validates_json(monkeypatch):
    import app as mod
    monkeypatch.setattr(mod, '_groq_messages', lambda *a, **k: {'text':'{"route":"research","reason":"Current facts","search_query":"latest science"}'})
    route, reason, query, source = mod._smart_route('What happened today?', [])
    assert (route, query, source) == ('research', 'latest science', 'model')

def test_router_falls_back_when_model_json_invalid(monkeypatch):
    import app as mod
    monkeypatch.setattr(mod, '_groq_messages', lambda *a, **k: {'text':'not json'})
    route, reason, query, source = mod._smart_route('Explain gravity', [])
    assert route == 'answer'
    assert source == 'fallback'

def test_artifact_generation_rejects_empty_content(monkeypatch):
    import app as mod
    monkeypatch.setattr(mod, '_groq_messages', lambda *a, **k: {'text':'{"name":"empty.md","content":"  "}'})
    artifact, result = mod._create_with_model('create a file')
    assert artifact is None
    assert 'error' in result
