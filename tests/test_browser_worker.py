import importlib
import socket
import sys
import types

import pytest


@pytest.fixture
def worker(monkeypatch):
    # The CI suite tests request guards without installing a browser binary.
    # Real Chromium installation is validated by the isolated Docker worker build.
    sys.modules.pop("browser_worker", None)
    module = importlib.import_module("browser_worker")
    monkeypatch.setattr(module, "_domains", lambda: [])
    return module


def test_worker_health_endpoint(worker):
    response = worker.app.test_client().get("/healthz")
    assert response.status_code == 200
    assert response.get_json()["service"] == "elyntro-browser-worker"


def test_worker_requires_bearer_token(worker, monkeypatch):
    monkeypatch.setenv("ELYNTRO_BROWSER_WORKER_TOKEN", "test-secret")
    response = worker.app.test_client().post("/v1/inspect", json={"url": "https://example.com"})
    assert response.status_code == 401


def test_worker_fails_closed_without_allowlist(worker, monkeypatch):
    monkeypatch.setenv("ELYNTRO_BROWSER_WORKER_TOKEN", "test-secret")
    monkeypatch.setattr(worker, "_authorized", lambda: True)
    response = worker.app.test_client().post("/v1/inspect", json={"url": "https://example.com"})
    assert response.status_code == 503


def test_worker_rejects_private_literal_even_if_allowlisted(worker):
    assert not worker._safe_url("http://127.0.0.1/", ["127.0.0.1"])
    assert not worker._safe_url("http://169.254.169.254/latest/meta-data/", ["169.254.169.254"])


def test_worker_rejects_non_allowlisted_host(worker, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 443))])
    assert not worker._safe_url("https://not-allowed.example/", ["allowed.example"])


def test_worker_rejects_dns_resolving_to_private_address(worker, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.2", 443))])
    assert not worker._safe_url("https://allowed.example/", ["allowed.example"])
