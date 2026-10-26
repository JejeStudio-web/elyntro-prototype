import json


def test_creative_status_discloses_unconfigured_integrations(monkeypatch):
    import app as mod
    monkeypatch.delenv("COMFYUI_BASE_URL", raising=False)
    monkeypatch.delenv("ELYNTRO_FOUNDER_TOKEN", raising=False)
    response = mod.app.test_client().get("/api/creative/status")
    body = response.get_json()
    assert response.status_code == 200
    assert body["features"]["threejs_scene"]["available"] is True
    assert body["features"]["comfyui"]["available"] is False
    assert body["features"]["self_modification"]["automatic_apply"] is False


def test_comfyui_endpoint_requires_api_key(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_API_KEY", "test-api-key")
    response = mod.app.test_client().post("/api/creative/comfyui/generate", json={"prompt": "test", "workflow": {}})
    assert response.status_code == 400 or response.status_code == 401


def test_comfyui_workflow_rejects_missing_prompt_nodes(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_API_KEY", "test-api-key")
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    response = mod.app.test_client().post("/api/creative/comfyui/generate",
        headers={"Authorization": "Bearer test-api-key"},
        json={"prompt": "a city", "workflow": {"1": {"class_type": "KSampler", "inputs": {}}}})
    assert response.status_code == 400


def test_founder_mode_proposes_patch_without_applying(monkeypatch, tmp_path):
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_PROPOSAL_DIR", str(tmp_path))
    response = mod.app.test_client().post("/api/creative/founder/proposals",
        headers={"Authorization": "Bearer founder-secret"},
        json={"title": "Add safe feature", "rationale": "Test proposal", "patch": "diff --git a/x b/x"})
    body = response.get_json()
    assert response.status_code == 201
    assert body["proposal"]["status"] == "pending_human_review"
    assert body["proposal"]["policy"]["auto_apply"] is False
    assert len(list(tmp_path.glob("prop_*.json"))) == 1



def test_playwright_control_requires_api_key_and_is_disabled_by_default(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_API_KEY", "test-api-key")
    monkeypatch.delenv("ELYNTRO_PLAYWRIGHT_ENABLED", raising=False)
    client = mod.app.test_client()
    missing_key = client.post("/api/creative/browser/act", json={"url": "https://example.com", "actions": []})
    disabled = client.post("/api/creative/browser/act",
        headers={"Authorization": "Bearer test-api-key"},
        json={"url": "https://example.com", "actions": []})
    assert missing_key.status_code == 401
    assert disabled.status_code == 503


def test_studio_route_serves_threejs_editor():
    import app as mod
    response = mod.app.test_client().get("/creative-studio")
    assert response.status_code == 200
    assert "three.module.js" in response.get_data(as_text=True)
    assert "Founder Mode" in response.get_data(as_text=True)
