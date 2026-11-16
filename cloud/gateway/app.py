from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections import deque
from threading import Lock
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


NINFER_BASE_URL = os.getenv("NINFER_BASE_URL", "http://ninfer:8080").rstrip("/")
NINFER_API_KEY = os.getenv("NINFER_API_KEY", "")
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", "2097152"))
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "600"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("MAX_REQUESTS_PER_MINUTE", "30"))

# A client key this long cannot be found by guessing, so wrong keys need no lockout: a lockout
# on the one shared key would let anyone who floods the gateway shut its owner out.
MIN_GATEWAY_KEY_LENGTH = 32
EXAMPLE_KEY_PREFIX = "replace-with"
# /ready needs no key, so one engine check answers every caller for this long.
READY_CACHE_SECONDS = 5.0
READY_TIMEOUT_SECONDS = 5.0

logger = logging.getLogger("uvicorn.error")

_request_times: deque[float] = deque()
_rate_lock = Lock()

FORWARDED_RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "x-request-id",
    "x-ninfer-version",
}


@asynccontextmanager
async def lifespan(application: FastAPI):
    application.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS, connect=10.0),
        limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
    )
    application.state.ready_lock = asyncio.Lock()
    application.state.ready_checked = None
    problem = key_problem()
    if problem:
        logger.error("Gateway keys rejected, every API request gets 503: %s", problem)
    yield
    await application.state.http.aclose()


app = FastAPI(
    title="OSINT Deep Search AI Gateway",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def key_problem() -> str | None:
    """Why the configured keys must not be used, or None when they are acceptable."""
    if not GATEWAY_API_KEY:
        return "GATEWAY_API_KEY is not set"
    if GATEWAY_API_KEY.startswith(EXAMPLE_KEY_PREFIX) or NINFER_API_KEY.startswith(
        EXAMPLE_KEY_PREFIX
    ):
        return "an example key from .env.example is still in use"
    if len(GATEWAY_API_KEY) < MIN_GATEWAY_KEY_LENGTH:
        return f"GATEWAY_API_KEY is shorter than {MIN_GATEWAY_KEY_LENGTH} characters"
    if GATEWAY_API_KEY == NINFER_API_KEY:
        return "GATEWAY_API_KEY and NINFER_API_KEY are the same"
    return None


def authenticate(authorization: str | None) -> None:
    if key_problem():
        raise HTTPException(status_code=503, detail="gateway_not_configured")
    scheme, _, supplied = (authorization or "").partition(" ")
    # Bytes, not text: compare_digest raises on text with non-ASCII characters, and a header
    # may carry any byte.
    if scheme.lower() != "bearer" or not secrets.compare_digest(
        supplied.encode("utf-8"), GATEWAY_API_KEY.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="invalid_api_key")
    now = time.monotonic()
    with _rate_lock:
        while _request_times and now - _request_times[0] >= 60:
            _request_times.popleft()
        if len(_request_times) >= MAX_REQUESTS_PER_MINUTE:
            retry_after = max(1, int(60 - (now - _request_times[0])))
            raise HTTPException(
                status_code=429,
                detail="rate_limit_exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        _request_times.append(now)


async def read_limited_body(request: Request) -> bytes:
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="request_too_large")
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_content_length") from None

    chunks = bytearray()
    async for chunk in request.stream():
        if len(chunks) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="request_too_large")
        chunks.extend(chunk)
    return bytes(chunks)


def upstream_headers() -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if NINFER_API_KEY:
        headers["authorization"] = f"Bearer {NINFER_API_KEY}"
    return headers


async def send_upstream(
    request: Request, path: str, body: bytes | None = None
) -> Response:
    client: httpx.AsyncClient = request.app.state.http
    upstream_request = client.build_request(
        request.method,
        f"{NINFER_BASE_URL}{path}",
        content=body,
        headers=upstream_headers(),
    )
    try:
        upstream = await client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="inference_timeout") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="inference_unavailable") from None

    headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() in FORWARDED_RESPONSE_HEADERS
    }
    if "text/event-stream" in upstream.headers.get("content-type", "").lower():
        async def stream_body() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            stream_body(), status_code=upstream.status_code, headers=headers
        )

    try:
        content = await upstream.aread()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="inference_timeout") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="inference_unavailable") from None
    finally:
        await upstream.aclose()
    return Response(content, status_code=upstream.status_code, headers=headers)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "osint-deep-search-ai-gateway"}


@app.get("/ready")
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    async with state.ready_lock:
        checked = state.ready_checked
        if checked is None or time.monotonic() - checked[0] >= READY_CACHE_SECONDS:
            try:
                result = await state.http.get(
                    f"{NINFER_BASE_URL}/health", timeout=READY_TIMEOUT_SECONDS
                )
                engine_ready = result.status_code == 200
            except httpx.HTTPError:
                engine_ready = False
            checked = state.ready_checked = (time.monotonic(), engine_ready)
    if not checked[1]:
        return JSONResponse({"status": "not_ready"}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.get("/v1/models")
async def models(
    request: Request, authorization: str | None = Header(default=None)
) -> Response:
    authenticate(authorization)
    return await send_upstream(request, "/v1/models")


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request, authorization: str | None = Header(default=None)
) -> Response:
    authenticate(authorization)
    body = await read_limited_body(request)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        # RecursionError: nesting too deep for the parser, which a 2 MB body can reach.
        raise HTTPException(status_code=400, detail="invalid_json") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="invalid_request")
    for field in ("max_tokens", "max_completion_tokens"):
        value = payload.get(field)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise HTTPException(status_code=400, detail=f"invalid_{field}")
            if value > MAX_OUTPUT_TOKENS:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field}_exceeds_limit_{MAX_OUTPUT_TOKENS}",
                )
    if "max_tokens" not in payload and "max_completion_tokens" not in payload:
        payload["max_tokens"] = MAX_OUTPUT_TOKENS
    return await send_upstream(
        request, "/v1/chat/completions", json.dumps(payload).encode("utf-8")
    )
