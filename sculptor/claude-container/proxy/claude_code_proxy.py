"""Anthropic API Proxy for Claude Code with caching/snapshot support.

Usage:
    # Normal mode - calls Anthropic and caches responses
    ANTHROPIC_API_KEY=sk-... python claude_code_proxy.py

    # Snapshot mode - only returns cached responses, fails on cache miss
    SNAPSHOT_PATH=/path/to/cache.db ANTHROPIC_API_KEY=sk-... python claude_code_proxy.py
"""

import json
import os
import time
from typing import Any
from typing import Literal
from typing import cast

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from loguru import logger

# pyre-ignore[21]: proxy_utils is in the same directory at runtime (container copies files flat to /imbue/)
from proxy_utils import CacheMissError
from proxy_utils import JsonCache
from proxy_utils import LOG_FILE_PATH
from proxy_utils import PROXY_CACHE_PATH
from proxy_utils import existing_snapshots_provided
from proxy_utils import safe_to_dict
from pydantic import BaseModel

os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
logger.add(
    LOG_FILE_PATH,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    level="INFO",
)


ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"


app = FastAPI()


class ContentBlockText(BaseModel):
    type: Literal["text"]
    text: str


class ContentBlockImage(BaseModel):
    type: Literal["image"]
    source: dict[str, Any]


class ContentBlockToolUse(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]


class ContentBlockToolResult(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: str | list[dict[str, Any]] | dict[str, Any] | list[Any] | Any


class SystemContent(BaseModel):
    type: Literal["text"]
    text: str


ContentBlock = ContentBlockText | ContentBlockImage | ContentBlockToolUse | ContentBlockToolResult


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[ContentBlock]


class Tool(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any]


class ThinkingConfig(BaseModel):
    enabled: bool


class MessagesRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[Message]
    system: str | list[SystemContent] | None = None
    stop_sequences: list[str] | None = None
    stream: bool | None = False
    temperature: float | None = 1.0
    top_p: float | None = None
    top_k: int | None = None
    metadata: dict[str, Any] | None = None
    tools: list[Tool] | None = None
    tool_choice: dict[str, Any] | None = None
    thinking: ThinkingConfig | None = None
    original_model: str | None = None


cache = JsonCache(PROXY_CACHE_PATH)


def get_cache_key_from_anthropic(anthropic_request: MessagesRequest) -> str:
    """Generate a deterministic cache key from Anthropic request."""

    model = anthropic_request.model
    temperature = anthropic_request.temperature
    top_p = anthropic_request.top_p
    top_k = anthropic_request.top_k
    stop = anthropic_request.stop_sequences
    messages = anthropic_request.messages

    key_dict = {
        "model": model,
        "temperature": temperature,
        "top_p": top_p,
        "top_k": top_k,
        "stop": stop,
        "messages": [safe_to_dict(msg) for msg in messages],
    }

    logger.info("Key dict for cache: {}", key_dict)

    serialized = json.dumps(key_dict, sort_keys=True, default=str)

    return serialized


async def call_anthropic_api(raw_request: Request) -> dict[str, Any]:
    """Make direct call to Anthropic API."""
    assert not existing_snapshots_provided(), "Should not call remote servers in snapshot mode"

    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    # Pass through anthropic-beta header if present (required for Claude Code)
    anthropic_beta = raw_request.headers.get("anthropic-beta")
    if anthropic_beta:
        headers["anthropic-beta"] = anthropic_beta

    request_data = await raw_request.json()

    is_streaming = request_data.get("stream", False)

    if is_streaming:
        logger.info("Turning off streaming for request")
        request_data["stream"] = False

    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(ANTHROPIC_API_URL, json=request_data, headers=headers)

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Anthropic API error: {response.status_code} - {response.text}",
            )

        try:
            return response.json()
        except json.JSONDecodeError:
            logger.error("Failed to decode JSON from Anthropic: {}", response.text)
            raise HTTPException(status_code=502, detail="Invalid JSON response from Anthropic")


def is_system_reminder(block: ContentBlockText) -> bool:
    return isinstance(block.text, str) and block.text.strip().startswith("<system-reminder>")


def filter_claude_code_request(request: MessagesRequest) -> MessagesRequest:
    filtered_request = request.copy(deep=True)

    filtered_messages = []
    for message in filtered_request.messages:
        content = message.content
        if isinstance(content, str):
            filtered_messages.append(message)
            continue

        new_content_blocks = [
            block
            for block in content
            if not (isinstance(block, ContentBlockText) and is_system_reminder(block))
            and not isinstance(block, ContentBlockToolResult)
        ]

        if new_content_blocks:
            filtered_messages.append(
                Message(
                    role=message.role,
                    content=cast(
                        list[ContentBlock],
                        new_content_blocks,
                    ),
                )
            )

    filtered_request.messages = filtered_messages
    return filtered_request


@app.post("/v1/messages")
async def create_message(request: MessagesRequest, raw_request: Request) -> dict[str, Any]:
    logger.info("Receiving request: {}", request)
    """Handle Anthropic API requests with caching."""
    try:
        logger.info("Processing request for model: {}", request.model)

        # Generate cache key
        filtered_request = filter_claude_code_request(request)
        cache_key = get_cache_key_from_anthropic(filtered_request)

        # Check cache first
        cached_response = cache.get(cache_key)
        if cached_response:
            logger.info("Returning cached response for key: {}", cache_key)
            return cached_response

        # Cache miss - check if we should fail
        if existing_snapshots_provided():
            logger.error("Cache miss and no snapshot path set")
            raise CacheMissError(f"Cache miss for key: {cache_key}")

        logger.info("Cache miss - making API call for key: {}", cache_key)

        # Make API call
        start_time = time.time()
        api_response = await call_anthropic_api(raw_request)
        logger.info("API call completed in {}", time.time() - start_time)

        # Cache the response
        cache.set(cache_key, api_response)

        return api_response

    except CacheMissError as e:  # pyre-ignore[66]
        raise HTTPException(status_code=400, detail=f"Cache miss in snapshot mode: {str(e)}")
    except Exception as e:
        logger.opt(exception=e).info("Error processing request")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


if __name__ == "__main__":
    logger.info("Starting Anthropic caching proxy server...")
    uvicorn.run(app, host="0.0.0.0", port=8082, log_level="info")
