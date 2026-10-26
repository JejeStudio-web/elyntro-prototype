from __future__ import annotations

from app import app


def test_studio_status_is_truthful_and_does_not_claim_unconfigured_integrations(monkeypatch):
    monkeypatch.delenv("COMFYUI_BASE_URL", raising=False)
    monkeypatch.delenv("ELYNTRO_PLAYWRIGHT_BRIDGE_URL", raising=False)
    monkeypatch.delenv("ELYNTRO_PLAYWRIGHT_BRIDGE_TOKEN", raising=False)
    response = app.test_client().get("/api/studio/status")
    assert response.status_code == 200
    data = response.get_json()
    assert data["features"]["threejs_frontend"] is True
    assert data["features"]["comfyui"] is False
    assert data["features"]["playwright_bridge"] is False
    assert data["features"]["self_modification_execution"] is False


def test_founder_proposal_requires_goal_and_never_executes_code():
    client = app.test_client()
    assert client.post("/api/studio/founder/proposals", json={}).status_code == 400
    response = client.post("/api/studio/founder/proposals", json={"goal": "Add a tool registry"})
    assert response.status_code == 201
    proposal = response.get_json()["proposal"]
    assert proposal["status"] == "draft"
    assert proposal["requires_human_approval"] is True
    assert proposal["execution_performed"] is False


def test_founder_proposal_caps_goal_length():
    response = app.test_client().post("/api/studio/founder/proposals", json={"goal": "x" * 4001})
    assert response.status_code == 400


def test_studio_page_is_served_with_visual_and_governance_controls():
    response = app.test_client().get("/studio")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert 'id="viewport"' in page
    assert "three.module.js" in page
    assert 'id="workflow"' in page
    assert 'id="founderGoal"' in page
