import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import app as gateway


CLIENT_KEY = "client-test-key-0123456789abcdefghij"
ENGINE_KEY = "engine-test-key-0123456789abcdefghij"
CLIENT_AUTH = {"Authorization": f"Bearer {CLIENT_KEY}"}


@contextmanager
def setup_client(monkeypatch, handler, raise_server_exceptions=True):
    gateway._request_times.clear()
    monkeypatch.setattr(gateway, "GATEWAY_API_KEY", CLIENT_KEY)
    monkeypatch.setattr(gateway, "NINFER_API_KEY", ENGINE_KEY)
    with TestClient(
        gateway.app, raise_server_exceptions=raise_server_exceptions
    ) as client:
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
        assert request.headers["authorization"] == f"Bearer {ENGINE_KEY}"
        return httpx.Response(200, json={"data": []})

    with setup_client(monkeypatch, handler) as client:
        response = client.get(
            "/v1/models",
            headers=CLIENT_AUTH,
        )
        assert response.status_code == 200
        assert response.json() == {"data": []}


def test_chat_completions_caps_default_and_forwards(monkeypatch):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4096
        assert request.headers["authorization"] == f"Bearer {ENGINE_KEY}"
        return httpx.Response(200, json={"choices": []})

    with setup_client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=CLIENT_AUTH,
            json={"model": "test-model", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert response.status_code == 200


def test_chat_rejects_excessive_output_tokens(monkeypatch):
    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=CLIENT_AUTH,
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
        assert client.get("/v1/models", headers=CLIENT_AUTH).status_code == 200
        response = client.get("/v1/models", headers=CLIENT_AUTH)
        assert response.status_code == 429
        assert response.headers["retry-after"]


def test_chat_rejects_oversized_body_before_upstream(monkeypatch):
    monkeypatch.setattr(gateway, "MAX_REQUEST_BYTES", 32)

    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=CLIENT_AUTH,
            content=b'{"messages":[],"padding":"' + (b"x" * 64) + b'"}',
        )
        assert response.status_code == 413
        assert response.json()["detail"] == "request_too_large"


def test_key_with_non_ascii_bytes_is_refused_not_a_server_error(monkeypatch):
    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler, raise_server_exceptions=False) as client:
        response = client.get(
            "/v1/models", headers={"Authorization": b"Bearer \xff\xfe\xfd"}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid_api_key"


@pytest.mark.parametrize(
    ("client_key", "engine_key"),
    [
        ("", ENGINE_KEY),
        ("replace-with-a-long-random-client-key", ENGINE_KEY),
        (CLIENT_KEY, "replace-with-a-different-long-random-engine-key"),
        ("only-31-characters-long-abcdefg", ENGINE_KEY),
        (CLIENT_KEY, CLIENT_KEY),
    ],
)
def test_unusable_keys_close_the_api(monkeypatch, client_key, engine_key):
    def handler(request):
        raise AssertionError("upstream must not be called")

    with setup_client(monkeypatch, handler) as client:
        monkeypatch.setattr(gateway, "GATEWAY_API_KEY", client_key)
        monkeypatch.setattr(gateway, "NINFER_API_KEY", engine_key)
        response = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {client_key}"}
        )
        assert response.status_code == 503
        assert response.json()["detail"] == "gateway_not_configured"


def test_example_keys_are_the_ones_the_gateway_refuses():
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    keys = dict(
        line.split("=", 1)
        for line in example.splitlines()
        if line.startswith(("GATEWAY_API_KEY=", "NINFER_API_KEY="))
    )
    assert set(keys) == {"GATEWAY_API_KEY", "NINFER_API_KEY"}
    for value in keys.values():
        assert value.startswith(gateway.EXAMPLE_KEY_PREFIX)


def test_chat_rejects_json_nested_too_deep_to_parse(monkeypatch):
    def handler(request):
        raise AssertionError("upstream must not be called")

    depth = 200_000
    body = b'{"messages":' + b"[" * depth + b"]" * depth + b"}"
    assert len(body) < gateway.MAX_REQUEST_BYTES

    with setup_client(monkeypatch, handler, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/chat/completions", headers=CLIENT_AUTH, content=body
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "invalid_json"


class EngineStream(httpx.AsyncByteStream):
    def __init__(self, error):
        self.error = error
        self.closed = False

    async def __aiter__(self):
        yield b'{"data":'
        raise self.error

    async def aclose(self):
        self.closed = True


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (httpx.ReadError("engine connection reset"), 502, "inference_unavailable"),
        (httpx.ReadTimeout("engine stalled"), 504, "inference_timeout"),
    ],
)
def test_engine_failure_inside_a_response_is_reported_and_closed(
    monkeypatch, error, status, detail
):
    stream = EngineStream(error)

    def handler(request):
        return httpx.Response(
            200, headers={"content-type": "application/json"}, stream=stream
        )

    with setup_client(monkeypatch, handler, raise_server_exceptions=False) as client:
        response = client.get("/v1/models", headers=CLIENT_AUTH)
        assert response.status_code == status
        assert response.json()["detail"] == detail
        assert stream.closed


def test_ready_asks_the_engine_once_per_cache_period(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "ok"})

    with setup_client(monkeypatch, handler) as client:
        for _ in range(50):
            assert client.get("/ready").json() == {"status": "ready"}
        assert calls == ["/health"]

        monkeypatch.setattr(gateway, "READY_CACHE_SECONDS", 0.0)
        assert client.get("/ready").status_code == 200
        assert calls == ["/health", "/health"]


def test_ready_reports_an_engine_that_is_down(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("engine is not listening")

    with setup_client(monkeypatch, handler) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}


def test_upload_abandoned_by_the_caller_ends_the_request_cleanly():
    class AbandonedUpload:
        headers = {}

        async def stream(self):
            yield b"{"
            raise ClientDisconnect()

    with pytest.raises(HTTPException) as refusal:
        asyncio.run(gateway.read_limited_body(AbandonedUpload()))
    assert refusal.value.status_code == 400
    assert refusal.value.detail == "client_disconnected"
