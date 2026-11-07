import json
from contextlib import contextmanager

import httpx
from fastapi.testclient import TestClient

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import app as gateway


@contextmanager
def setup_client(monkeypatch, handler):
    gateway._request_times.clear()
    monkeypatch.setattr(gateway, "GATEWAY_API_KEY", "client-test-key")
    monkeypatch.setattr(gateway, "NINFER_API_KEY", "engine-test-key")
    with TestClient(gateway.app) as client:
        client.app.state.http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        yield client


def test_health_is_public_and_does_not_expose_keys(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"status": "ok"})

    with setup_client(monkeypatch, handler) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert "key" not in response.text.lower()


def test_rejects_missing_or_invalid_client_key(monkeypatch):
    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get(
            "/v1/models", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401


def test_models_uses_private_upstream_key(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer engine-test-key"
        return httpx.Response(200, json={"data": []})

    with setup_client(monkeypatch, handler) as client:
        response = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer client-test-key"},
        )
        assert response.status_code == 200
        assert response.json() == {"data": []}


def test_chat_completions_caps_default_and_forwards(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4096
        assert request.headers["authorization"] == "Bearer engine-test-key"
        return httpx.Response(200, json={"choices": []})

    with setup_client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer client-test-key"},
            json={"model": "test-model", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert response.status_code == 200


def test_chat_rejects_excessive_output_tokens(monkeypatch):
    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer client-test-key"},
            json={"messages": [], "max_tokens": 4097},
        )
        assert response.status_code == 400


def test_ready_checks_engine(monkeypatch):
    def handler(request):
        assert request.url.path == "/health"
        return httpx.Response(200, json={"status": "ok"})

    with setup_client(monkeypatch, handler) as client:
        assert client.get("/ready").json() == {"status": "ready"}


def test_rate_limit_returns_retry_after(monkeypatch):
    monkeypatch.setattr(gateway, "MAX_REQUESTS_PER_MINUTE", 1)

    def handler(request):
        return httpx.Response(200, json={"data": []})

    with setup_client(monkeypatch, handler) as client:
        headers = {"Authorization": "Bearer client-test-key"}
        assert client.get("/v1/models", headers=headers).status_code == 200
        response = client.get("/v1/models", headers=headers)
        assert response.status_code == 429
        assert response.headers["retry-after"]
