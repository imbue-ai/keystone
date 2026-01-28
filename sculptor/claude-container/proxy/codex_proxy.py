#!/usr/bin/env python3
"""
OpenAI Proxy for Codex CLI with caching/snapshot support.

Usage:
    # Normal mode - calls OpenAI and caches responses
    OPENAI_API_KEY=sk-... python codex_proxy.py

    # Snapshot mode - only returns cached responses, fails on cache miss
    SNAPSHOT_PATH=/path/to/cache.db OPENAI_API_KEY=sk-... python codex_proxy.py

    # Run codex
    OPENAI_BASE_URL=http://0.0.0.0:8082 codex "hi"
"""

import json
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from loguru import logger

# pyre-ignore[21]: proxy_utils is in the same directory at runtime (container copies files flat to /imbue/)
from proxy_utils import JsonCache
from proxy_utils import LOG_FILE_PATH
from proxy_utils import PROXY_CACHE_PATH
from proxy_utils import existing_snapshots_provided

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
logger.add(
    LOG_FILE_PATH,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="INFO",
)

cache = JsonCache(PROXY_CACHE_PATH)
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(None))
    mode = "snapshot" if existing_snapshots_provided() else "caching"
    logger.info("Proxy started on port 8082 ({} mode, cache: {})", mode, PROXY_CACHE_PATH)
    yield
    assert http_client is not None
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)


def build_headers(request: Request) -> dict:
    headers = {}
    for header in ["content-type", "accept", "conversation_id", "session_id", "x-openai-subagent"]:
        value = request.headers.get(header)
        if value:
            headers[header] = value

    headers["authorization"] = f"Bearer {OPENAI_API_KEY}"

    if "accept" not in headers:
        headers["accept"] = "text/event-stream"

    return headers


def get_cache_key(request_body: dict, endpoint: str) -> str:
    """Generate a deterministic cache key from the request."""
    # Include fields that affect the response
    key_dict = {
        "endpoint": endpoint,
        "model": request_body.get("model"),
        "input": request_body.get("input"),  # For responses API
        "messages": request_body.get("messages"),  # For chat completions
        "instructions": request_body.get("instructions"),
    }
    return json.dumps(key_dict, sort_keys=True, default=str)


@app.post("/responses")
async def proxy_responses(request: Request):
    return await _proxy_streaming(request, "/responses")


@app.post("/chat/completions")
async def proxy_chat_completions(request: Request):
    return await _proxy_streaming(request, "/chat/completions")


@app.post("/responses/compact")
async def proxy_responses_compact(request: Request):
    return await _proxy_json(request, "/responses/compact")


async def _proxy_json(request: Request, endpoint: str):
    body = await request.body()
    request_data = json.loads(body)
    cache_key = get_cache_key(request_data, endpoint)

    # Check cache
    cached = cache.get(cache_key)
    if cached:
        logger.info("Cache hit for {}", endpoint)
        return JSONResponse(status_code=200, content=cached)

    # Cache miss in snapshot mode = error
    if existing_snapshots_provided():
        raise HTTPException(status_code=400, detail=f"Cache miss in snapshot mode")

    # Call upstream
    url = f"https://api.openai.com/v1{endpoint}"
    headers = build_headers(request)
    headers["accept"] = "application/json"

    logger.info("Cache miss - calling {}", url)

    assert http_client is not None
    try:
        resp = await http_client.post(url, content=body, headers=headers)
        logger.info("Upstream response: {}", resp.status_code)

        if resp.status_code >= 400:
            return JSONResponse(status_code=resp.status_code, content=resp.text)

        response_data = resp.json()
        cache.set(cache_key, response_data)
        return JSONResponse(status_code=resp.status_code, content=response_data)

    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=str(e))


async def _proxy_streaming(request: Request, endpoint: str):
    body = await request.body()
    request_data = json.loads(body)
    cache_key = get_cache_key(request_data, endpoint)

    # Check cache
    logger.info("Got key {}", cache_key)
    cached = cache.get(cache_key)
    if cached:
        logger.info("Cache hit for {}", endpoint)

        # Return cached SSE data as streaming response
        async def cached_stream():
            encoded = cached.encode()
            logger.info("Yielding cached result: {}", encoded)
            yield encoded

        return StreamingResponse(cached_stream(), status_code=200, media_type="text/event-stream")

    # Cache miss in snapshot mode = error
    if existing_snapshots_provided():
        raise HTTPException(status_code=400, detail=f"Cache miss in snapshot mode")

    # Call upstream
    url = f"https://api.openai.com/v1{endpoint}"
    headers = build_headers(request)

    logger.info("Cache miss - calling {}", url)

    try:
        # For caching, we need to disable streaming to get the full response
        request_data["stream"] = False
        assert http_client is not None
        resp = await http_client.post(url, json=request_data, headers=headers)

        logger.info("Upstream response: {}", resp.status_code)

        if resp.status_code >= 400:
            error_body = resp.text
            return JSONResponse(
                status_code=resp.status_code,
                content=error_body,
            )

        # Get the full response and cache it
        response_data = resp.json()

        # Convert non-streaming response to SSE format for codex
        sse_response = convert_to_sse(response_data)
        cache.set(cache_key, sse_response)

        async def stream_cached():
            yield sse_response.encode()

        return StreamingResponse(stream_cached(), status_code=200, media_type="text/event-stream")

    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=str(e))


def convert_to_sse(response: dict) -> str:
    """Convert a non-streaming OpenAI response to SSE format."""
    events = []

    # response.created
    events.append(
        f"event: response.created\ndata: {json.dumps({'type': 'response.created', 'response': {'id': response.get('id', '')}})}\n\n"
    )

    # Output items
    for item in response.get("output", []):
        # response.output_item.added
        events.append(
            f"event: response.output_item.added\ndata: {json.dumps({'type': 'response.output_item.added', 'item': item})}\n\n"
        )
        # response.output_item.done
        events.append(
            f"event: response.output_item.done\ndata: {json.dumps({'type': 'response.output_item.done', 'item': item})}\n\n"
        )

    # response.completed
    completed_data = {
        "type": "response.completed",
        "response": {
            "id": response.get("id", ""),
            "output": response.get("output", []),
            "usage": response.get("usage"),
        },
    }
    events.append(f"event: response.completed\ndata: {json.dumps(completed_data)}\n\n")

    return "".join(events)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
