import json

import app as app_module


def test_task_and_artifact_reads_require_task_capability_token():
    client = app_module.app.test_client()
    created = client.post("/api/execute", json={"goal": "Crea una página web de prueba de seguridad"})
    assert created.status_code == 201, created.get_json()
    body = created.get_json()
    task_id = body["task"]["task_id"]
    token = body["task_access_token"]
    assert isinstance(token, str) and len(token) >= 40

    assert client.get(f"/api/tasks/{task_id}").status_code == 403
    assert client.get(
        f"/api/tasks/{task_id}", headers={"Authorization": "Bearer invalid-token"}
    ).status_code == 403

    authorized = client.get(
        f"/api/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert authorized.status_code == 200
    assert authorized.get_json()["task"]["task_id"] == task_id

    artifact = client.get(
        f"/api/tasks/{task_id}/artifacts/index.html",
        headers={"X-Elyntro-Task-Token": token},
    )
    assert artifact.status_code == 200
    assert "<html" in artifact.get_data(as_text=True).casefold()


def test_expensive_chat_route_is_rate_limited(monkeypatch):
    monkeypatch.setenv("ELYNTRO_RATE_LIMIT_CHAT_PER_MINUTE", "1")
    app_module._RATE_LIMITS.clear()
    monkeypatch.setattr(app_module, "_groq_chat", lambda goal, direct=False: {
        "configured": True, "text": "OK", "model": "test-model",
        "requested_model": "test-model", "response_id": "test-response", "usage": {}, "error": None,
    })
    client = app_module.app.test_client()
    first = client.post("/api/chat", json={"goal": "Di OK"})
    second = client.post("/api/chat", json={"goal": "Di OK"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json()["error_detail"]["code"] == "rate_limit_exceeded"


def test_api_rejects_oversized_request_body():
    client = app_module.app.test_client()
    response = client.post(
        "/api/chat",
        data="x" * (app_module.app.config["MAX_CONTENT_LENGTH"] + 1),
        content_type="text/plain",
    )
    assert response.status_code == 413
