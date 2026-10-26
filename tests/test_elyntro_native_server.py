import pytest

torch = pytest.importorskip("torch")
from elyntro_model import server


def test_inference_health_does_not_require_authentication():
    response = server.app.test_client().get("/health")
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_inference_routes_reject_missing_or_wrong_api_key(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "test-secret")
    client = server.app.test_client()
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_inference_models_endpoint_requires_auth_then_returns_model(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "test-secret")
    response = server.app.test_client().get("/v1/models", headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert response.get_json()["data"][0]["owned_by"] == "elyntro"


def test_chat_endpoint_validates_messages_before_loading_model(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "test-secret")
    client = server.app.test_client()
    response = client.post("/v1/chat/completions", json={"model": "elyntro-native", "messages": []},
                           headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 400
    assert response.get_json()["error"]["type"] == "invalid_request_error"
