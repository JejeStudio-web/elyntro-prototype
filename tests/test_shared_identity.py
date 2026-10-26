import importlib


def _app(monkeypatch):
    monkeypatch.delenv("ELYNTRO_DATABASE_URL", raising=False)
    monkeypatch.delenv("ELYNTRO_REGISTRATION_ENABLED", raising=False)
    import app
    importlib.reload(app)
    return app.app.test_client()


def test_identity_status_reports_unconfigured_without_database(monkeypatch):
    client = _app(monkeypatch)
    response = client.get("/api/identity/me")
    assert response.status_code == 503
    assert "error" in response.get_json()


def test_public_registration_is_opt_in(monkeypatch):
    client = _app(monkeypatch)
    response = client.post("/api/identity/register", json={
        "email": "user@example.com", "password": "a-long-test-password"
    })
    assert response.status_code == 403


def test_login_fails_closed_without_database(monkeypatch):
    client = _app(monkeypatch)
    response = client.post("/api/identity/login", json={
        "email": "user@example.com", "password": "a-long-test-password"
    })
    assert response.status_code == 503


def test_sync_never_accepts_anonymous_user_data(monkeypatch):
    client = _app(monkeypatch)
    response = client.put("/api/identity/sync/conversations/conversation-1",
                          json={"data": {"messages": []}})
    assert response.status_code == 503
