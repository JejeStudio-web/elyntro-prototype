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



def patch_hash_for_test(patch):
    import hashlib
    return hashlib.sha256(patch.encode()).hexdigest()


def test_founder_proposals_can_be_listed_and_reviewed_without_applying(monkeypatch, tmp_path):
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_PROPOSAL_DIR", str(tmp_path))
    client = mod.app.test_client()
    headers = {"Authorization": "Bearer founder-secret"}
    created = client.post("/api/creative/founder/proposals", headers=headers, json={
        "title": "Improve scene editor", "rationale": "Add durable review lifecycle",
        "patch": "diff --git a/static/creative-studio.html b/static/creative-studio.html"
    })
    assert created.status_code == 201
    proposal_id = created.get_json()["proposal"]["id"]

    listed = client.get("/api/creative/founder/proposals", headers=headers)
    assert listed.status_code == 200
    assert listed.get_json()["proposals"][0]["id"] == proposal_id
    details = client.get(f"/api/creative/founder/proposals/{proposal_id}", headers=headers)
    assert details.status_code == 200
    assert details.get_json()["proposal"]["patch_sha256"] == patch_hash_for_test(details.get_json()["proposal"]["patch"])

    missing_review_hash = client.post(f"/api/creative/founder/proposals/{proposal_id}/review",
                                        headers=headers, json={"decision": "approve"})
    assert missing_review_hash.status_code == 409

    patch_hash = created.get_json()["proposal"]["patch_sha256"]
    approved = client.post(f"/api/creative/founder/proposals/{proposal_id}/review",
                           headers=headers, json={"decision": "approve", "reviewed_patch_sha256": patch_hash})
    assert approved.status_code == 200
    body = approved.get_json()
    assert body["proposal"]["status"] == "approved_for_manual_implementation"
    assert body["proposal"]["policy"]["auto_apply"] is False
    assert "no aplica" in body["note"].lower()

    duplicate = client.post(f"/api/creative/founder/proposals/{proposal_id}/review",
                            headers=headers, json={"decision": "reject"})
    assert duplicate.status_code == 409


def test_founder_proposal_listing_requires_founder_token(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    response = mod.app.test_client().get("/api/creative/founder/proposals")
    assert response.status_code == 401


def test_studio_contains_scene_transform_and_export_controls():
    import app as mod
    response = mod.app.test_client().get("/creative-studio")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'id="posX"' in html
    assert 'id="saveScene"' in html
    assert 'id="exportScene"' in html
    assert 'id="importScene"' in html
    assert 'id="workflowExample"' in html
    assert 'id="checkGeneration"' in html
    assert 'id="clickBrowserButton"' in html



def test_comfyui_history_returns_only_output_image_references(monkeypatch):
    import json
    import creative_runtime
    import app as mod

    monkeypatch.setenv("ELYNTRO_API_KEY", "test-api-key")
    monkeypatch.setenv("COMFYUI_BASE_URL", "http://comfyui.test:8188")
    payload = {"prompt-123": {"outputs": {
        "7": {"images": [
            {"filename": "render.png", "subfolder": "", "type": "output"},
            {"filename": "temp.png", "subfolder": "", "type": "temp"},
            {"filename": "../escape.png", "subfolder": "", "type": "output"},
        ]}
    }}}

    class FakeResponse:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit=-1):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        assert request.full_url == "http://comfyui.test:8188/history/prompt-123"
        return FakeResponse()

    monkeypatch.setattr(creative_runtime.urllib.request, "urlopen", fake_urlopen)
    response = mod.app.test_client().get(
        "/api/creative/comfyui/history/prompt-123",
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert response.status_code == 200
    images = response.get_json()["images"]
    assert len(images) == 1
    assert images[0]["filename"] == "render.png"
    assert images[0]["url"] == "/api/creative/comfyui/view/prompt-123/render.png"



def test_founder_selfdev_creates_isolated_github_pull_request(monkeypatch):
    import io
    import json
    import urllib.error
    import creative_runtime
    import app as mod

    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_SELFDEV_ENABLED", "true")
    monkeypatch.setenv("ELYNTRO_GITHUB_TOKEN", "scoped-test-token")
    monkeypatch.setenv("ELYNTRO_GITHUB_REPOSITORY", "JejeStudio-web/elyntro-prototype")
    monkeypatch.setenv("ELYNTRO_GITHUB_BASE_BRANCH", "main")
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, limit=-1):
            return json.dumps(self.payload).encode("utf-8")

    def fake_urlopen(request, timeout=0):
        calls.append((request.get_method(), request.full_url))
        method, url = request.get_method(), request.full_url
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return FakeResponse({"object": {"sha": "a" * 40}})
        if method == "POST" and url.endswith("/git/refs"):
            return FakeResponse({"ref": "refs/heads/elyntro-selfdev-test"})
        if method == "GET" and "/contents/docs/new-note.md?" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        if method == "PUT" and url.endswith("/contents/docs/new-note.md"):
            return FakeResponse({"content": {"sha": "b" * 40}})
        if method == "POST" and url.endswith("/pulls"):
            return FakeResponse({"number": 777, "html_url": "https://github.com/JejeStudio-web/elyntro-prototype/pull/777", "state": "open", "draft": True})
        raise AssertionError("Unexpected GitHub API call: " + method + " " + url)

    monkeypatch.setattr(creative_runtime.urllib.request, "urlopen", fake_urlopen)
    response = mod.app.test_client().post(
        "/api/creative/founder/selfdev/pull-request",
        headers={"Authorization": "Bearer founder-secret"},
        json={"goal": "Improve docs", "title": "Improve generated docs", "rationale": "Test isolated PR flow",
              "files": [{"path": "docs/new-note.md", "content": "# New note\n"}]},
    )
    body = response.get_json()
    assert response.status_code == 201
    assert body["pull_request"]["number"] == 777
    assert body["pull_request"]["draft"] is True
    assert body["branch"].startswith("elyntro-selfdev-")
    assert ("POST", "https://api.github.com/repos/JejeStudio-web/elyntro-prototype/pulls") in calls


def test_founder_selfdev_rejects_sensitive_paths_before_github_access(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_SELFDEV_ENABLED", "true")
    response = mod.app.test_client().post(
        "/api/creative/founder/selfdev/pull-request",
        headers={"Authorization": "Bearer founder-secret"},
        json={"goal": "Change deployment", "title": "Change deployment", "rationale": "Not allowed",
              "files": [{"path": "render.yaml", "content": "services: []"}]},
    )
    assert response.status_code == 400


def test_founder_selfdev_is_disabled_without_explicit_opt_in(monkeypatch):
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.delenv("ELYNTRO_SELFDEV_ENABLED", raising=False)
    response = mod.app.test_client().post(
        "/api/creative/founder/selfdev/pull-request",
        headers={"Authorization": "Bearer founder-secret"},
        json={"goal": "Change docs", "title": "Change docs", "rationale": "Test", "files": [{"path": "docs/a.md", "content": "x"}]},
    )
    assert response.status_code == 503



def test_creator_prefix_requires_founder_authentication(monkeypatch):
    import app as mod
    monkeypatch.delenv("ELYNTRO_FOUNDER_TOKEN", raising=False)
    response = mod.app.test_client().post("/api/agent/execute", json={"goal": "[245jg&] improve the studio"})
    assert response.status_code == 401
    assert "Founder Mode" in response.get_json()["error"]


def test_creator_prefix_creates_hash_addressed_proposal_without_applying(monkeypatch, tmp_path):
    import json
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_PROPOSAL_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_model_provider_config", lambda: {"configured": True, "provider": "test", "model": "mock"})
    monkeypatch.setattr(mod, "_groq_messages", lambda *args, **kwargs: {
        "text": json.dumps({
            "title": "Improve Studio", "rationale": "Add a reviewed scene control.",
            "plan": ["Inspect the existing scene editor.", "Implement a bounded change.", "Run regression tests."],
            "files_to_review": ["static/creative-studio.html", "tests/test_creative_runtime.py"],
            "patch": "diff --git a/docs/scene.md b/docs/scene.md\n--- a/docs/scene.md\n+++ b/docs/scene.md\n@@ -0,0 +1 @@\n+# Scene"
        }), "provider": "test", "model": "mock"
    })
    response = mod.app.test_client().post(
        "/api/agent/execute",
        headers={"Authorization": "Bearer founder-secret"},
        json={"goal": "[245jg&] improve the studio"},
    )
    body = response.get_json()
    assert response.status_code == 200
    assert body["route"] == "founder_mode"
    assert body["founder_mode"]["status"] == "pending_human_review"
    assert body["founder_mode"]["automatic_apply"] is False
    assert body["founder_mode"]["tests_run"] is False
    assert len(list(tmp_path.glob("prop_*.json"))) == 1


def test_creator_prefix_rejects_non_diff_model_output(monkeypatch, tmp_path):
    import json
    import app as mod
    monkeypatch.setenv("ELYNTRO_FOUNDER_TOKEN", "founder-secret")
    monkeypatch.setenv("ELYNTRO_PROPOSAL_DIR", str(tmp_path))
    monkeypatch.setattr(mod, "_model_provider_config", lambda: {"configured": True, "provider": "test", "model": "mock"})
    monkeypatch.setattr(mod, "_groq_messages", lambda *args, **kwargs: {
        "text": json.dumps({"title": "Unsafe", "rationale": "Not a diff", "plan": [], "files_to_review": [], "patch": "just some text"})
    })
    response = mod.app.test_client().post(
        "/api/agent/execute",
        headers={"Authorization": "Bearer founder-secret"},
        json={"goal": "[245jg&] modify source"},
    )
    assert response.status_code == 422
    assert list(tmp_path.glob("prop_*.json")) == []
