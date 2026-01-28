#!/usr/bin/env python3
"""
Claude Code Proxy Viewer - A real-time visualization proxy for Claude Code API interactions.

This is a standalone proxy server that intercepts and visualizes API calls between
Claude Code and the Anthropic API. It provides a WebSocket-based real-time viewer
for debugging and understanding Claude Code's behavior.
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import AsyncGenerator
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from imbue_core.async_monkey_patches import log_exception

# Configuration
CACHE_DIR = Path(tempfile.gettempdir()) / "claude_proxy_viewer"
CACHE_DIR.mkdir(exist_ok=True)

# Initialize logger
logger.remove()  # Remove default handler
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}</level> | <cyan>{message}</cyan>",
)

# Global state
app = FastAPI(title="Claude Code Proxy Viewer")
proxy_events: deque[dict[str, Any]] = deque(maxlen=100)  # Circular buffer for last 100 events
websocket_clients: set[WebSocket] = set()
recent_requests: deque[tuple[float, str, str]] = deque(maxlen=10)  # Track recent requests for duplicate detection
pending_responses: dict[str, dict[str, Any]] = {}  # Hold first responses that might get duplicated

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,  # pyre-ignore[6]
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Check if React build exists
SCRIPT_DIR = Path(__file__).parent
REACT_BUILD_PATH = SCRIPT_DIR / "frontend" / "dist"
REACT_APP_AVAILABLE = REACT_BUILD_PATH.exists() and (REACT_BUILD_PATH / "index.html").exists()

if REACT_APP_AVAILABLE:
    logger.info("React app found at {}", REACT_BUILD_PATH)
    app.mount("/assets", StaticFiles(directory=str(REACT_BUILD_PATH / "assets")), name="assets")
else:
    logger.info("React app not found, only API endpoints will be available")


class ProxyRequest(BaseModel):
    """Incoming request from Claude Code"""

    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None = None
    temperature: float | None = None
    stream: bool | None = False
    system: Any | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    stop_sequences: list[str] | None = None
    top_p: float | None = None
    top_k: int | None = None


def extract_user_message(request: ProxyRequest) -> str | None:
    """Extract the most recent user message from the request."""
    for message in reversed(request.messages):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # Look for text content blocks
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text")
    return None


def extract_token_counts(response: dict[str, Any]) -> dict[str, int]:
    """Extract token counts from the API response."""
    usage = response.get("usage", {})
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
    }


def parse_sse_chunk(chunk: str) -> dict[str, Any] | None:
    """Parse a Server-Sent Event chunk."""
    if not chunk.strip():
        return None

    # SSE format: "data: {json}\n\n"
    if chunk.startswith("data: "):
        data_str = chunk[6:].strip()
        if data_str == "[DONE]":
            return {"type": "done"}
        try:
            return json.loads(data_str)
        except json.JSONDecodeError:
            logger.info("Failed to parse SSE chunk: {}", data_str)
            return None
    return None


async def broadcast_event(event: dict[str, Any]) -> None:
    """Broadcast event to all connected WebSocket clients."""
    if not websocket_clients:
        logger.debug("No WebSocket clients connected")
        return

    logger.info("Broadcasting event to {} clients", len(websocket_clients))

    disconnected_clients = set()

    for client in websocket_clients:
        try:
            await client.send_json(event)
        except Exception as e:
            logger.info("Failed to send to client: {}", e)
            disconnected_clients.add(client)

    # Remove disconnected clients
    for client in disconnected_clients:
        websocket_clients.discard(client)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time event streaming."""
    logger.info("New WebSocket connection attempt")
    try:
        await websocket.accept()
        websocket_clients.add(websocket)
        logger.info("WebSocket client connected, total clients: {}", len(websocket_clients))

        # Send connection confirmation
        await websocket.send_json(
            {
                "type": "connected",
                "message": "WebSocket connected successfully",
                "timestamp": datetime.now().isoformat(),
            }
        )

        # Send recent events
        for event in proxy_events:
            try:
                await websocket.send_json(event)
            except Exception as e:
                log_exception(e, "Error sending historical event")
                break

        # Keep connection alive
        while True:
            try:
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now().isoformat()})
            except WebSocketDisconnect:
                break
            except Exception as e:
                log_exception(e, "WebSocket error")
                break

    except Exception as e:
        log_exception(e, "WebSocket connection error")
    finally:
        websocket_clients.discard(websocket)
        logger.info("WebSocket client disconnected, remaining clients: {}", len(websocket_clients))


async def handle_streaming_response(
    request_id: str,
    request_data: dict[str, Any],
    user_message: str | None,
    start_time: float,
    stream: httpx.Response,
) -> AsyncGenerator[str, None]:
    """Handle streaming response from Anthropic API."""
    chunks = []
    full_response = None
    event_id = str(uuid4())
    content_blocks = []
    current_block_index = -1

    logger.info("[{}] Starting streaming response handler", request_id)

    async for line in stream.aiter_lines():
        if not line:
            continue

        # Parse the SSE chunk
        parsed = parse_sse_chunk(line)
        if not parsed:
            continue

        # Handle end of stream
        if parsed.get("type") == "done":
            break

        # Store chunks for complete response reconstruction
        chunks.append(parsed)

        # Track content blocks for proper response reconstruction
        if parsed.get("type") == "content_block_start":
            current_block_index += 1
            content_blocks.append(parsed.get("content_block", {}))
        elif parsed.get("type") == "content_block_delta":
            if current_block_index >= 0 and current_block_index < len(content_blocks):
                delta = parsed.get("delta", {})
                if delta.get("type") == "text_delta":
                    if "text" not in content_blocks[current_block_index]:
                        content_blocks[current_block_index]["text"] = ""
                    content_blocks[current_block_index]["text"] += delta.get("text", "")

        # Capture complete response from message events
        if parsed.get("type") == "message_start":
            message = parsed.get("message", {})
            full_response = {
                "id": message.get("id"),
                "type": "message",
                "role": message.get("role", "assistant"),
                "model": message.get("model"),
                "content": [],
                "usage": {},
            }
        elif parsed.get("type") == "message_delta":
            if full_response and "usage" in parsed:
                full_response["usage"] = parsed["usage"]

        # Forward the SSE chunk to Claude Code unchanged
        yield f"data: {json.dumps(parsed)}\n\n"

    # Send the final [DONE] message
    yield "data: [DONE]\n\n"

    # Broadcast the complete event to WebSocket clients
    duration_ms = (time.time() - start_time) * 1000

    # Use the content blocks we tracked
    if full_response and content_blocks:
        full_response["content"] = content_blocks

    # Ensure we have a complete response to broadcast
    if not full_response:
        logger.info("[{}] Failed to reconstruct full response from streaming, using fallback", request_id)
        full_response = {
            "id": event_id,
            "type": "message",
            "role": "assistant",
            "content": content_blocks
            if content_blocks
            else [{"type": "text", "text": "[Failed to capture response]"}],
            "model": request_data.get("model"),
            "usage": {},
        }
        # Try to get usage from any chunk
        for chunk in reversed(chunks):
            if chunk.get("type") == "message_delta" and "usage" in chunk:
                full_response["usage"] = chunk["usage"]
                break

    # Create a complete event for broadcasting (same format as non-streaming)
    event = {
        "id": event_id,
        "request_id": request_id,
        "timestamp": datetime.now().isoformat(),
        "user_message": user_message,
        "request": request_data,
        "response": full_response,  # This should be a complete response object
        "duration_ms": duration_ms,
        "token_counts": extract_token_counts(full_response),
        "from_cache": False,
        "was_streaming": True,  # Just a flag to indicate this came from streaming
    }

    # Store and broadcast the complete event
    proxy_events.append(event)
    await broadcast_event(event)

    logger.info(
        "[{}] Streaming completed, broadcast complete event with {} content blocks", request_id, len(content_blocks)
    )


@app.post("/v1/messages")
async def proxy_messages(request: ProxyRequest, raw_request: Request) -> Any:
    """Proxy endpoint that intercepts Claude Code API calls."""
    start_time = time.time()
    request_id = str(uuid4())[:8]  # Short ID for easier tracking

    # Get client information for debugging
    raw_request.headers.get("user-agent", "unknown")
    logger.info("Query params: {}", raw_request.query_params)
    logger.info("Headers: {}", raw_request.headers)

    logger.info("[{}] ========== NEW REQUEST ==========", request_id)

    # Get API key from environment
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("[{}] ANTHROPIC_API_KEY not set!", request_id)
        raise HTTPException(status_code=401, detail="ANTHROPIC_API_KEY not set")

    # Log the request details
    user_message = extract_user_message(request)
    logger.info(
        "[{}] Model: {}, Messages: {}, Streaming: {}", request_id, request.model, len(request.messages), request.stream
    )

    # Forward to Anthropic API
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}

    # Convert request to dict and remove None values
    request_data = request.dict(exclude_none=True)

    # Track request signature for duplicate detection
    request_signature = (
        f"{request.model}:{len(request.messages)}:{request.stream}:{user_message[:50] if user_message else 'none'}"
    )

    # Check for very recent duplicate (different streaming mode is likely a retry)
    current_time = time.time()
    is_duplicate = False
    duplicate_of = None

    # Create a signature without streaming flag for duplicate detection
    base_signature = f"{request.model}:{len(request.messages)}:{user_message[:50] if user_message else 'none'}"

    for prev_time, prev_hash, prev_id in list(recent_requests)[-5:]:
        # Extract the base signature (model:message_count:user_message) without streaming flag
        # prev_hash format: "model:message_count:streaming_flag:user_message"
        parts = prev_hash.split(":", 3)  # Split into at most 4 parts
        if len(parts) >= 4:
            # Reconstruct without the streaming flag (index 2)
            prev_base = f"{parts[0]}:{parts[1]}:{parts[3]}"
        else:
            prev_base = prev_hash  # Fallback if format is unexpected

        # If signatures match, it's a duplicate (no time restriction)
        if prev_base == base_signature:
            logger.info("[{}] Duplicate detected of {}", request_id, prev_id)
            is_duplicate = True
            duplicate_of = prev_id
            break

    # Store this request for duplicate detection
    recent_requests.append((current_time, request_signature, request_id))

    # Convert ALL streaming to non-streaming (simple approach)
    if request.stream:
        logger.info("[{}] Converting streaming to non-streaming for all requests", request_id)
        request_data["stream"] = False

    # Handle streaming vs non-streaming
    if request_data.get("stream", False):
        # Actually handle streaming properly
        logger.info("[{}] Handling STREAMING request to Anthropic API...", request_id)
        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST", "https://api.anthropic.com/v1/messages", json=request_data, headers=headers
                ) as response:
                    if response.status_code != 200:
                        logger.error("[{}] Streaming error: {}", request_id, response.status_code)
                        error_text = await response.aread()
                        raise HTTPException(
                            status_code=response.status_code, detail=f"Anthropic API error: {error_text.decode()}"
                        )

                    # Return streaming response
                    logger.info("[{}] Streaming response started", request_id)
                    return StreamingResponse(
                        handle_streaming_response(request_id, request_data, user_message, start_time, response),
                        media_type="text/event-stream",
                    )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("[{}] Streaming error: {}", request_id, e)
            raise HTTPException(status_code=500, detail=str(e))

    # Non-streaming request
    try:
        logger.info("[{}] Forwarding request to Anthropic API", request_id)

        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", json=request_data, headers=headers)

            if response.status_code != 200:
                logger.error("[{}] Anthropic API error: {}", request_id, response.status_code)
                # Still broadcast the error event so we can see it in the viewer
                error_event = {
                    "id": str(uuid4()),
                    "request_id": request_id,
                    "timestamp": datetime.now().isoformat(),
                    "user_message": user_message,
                    "request": request_data,
                    "response": {"error": response.text, "status_code": response.status_code},
                    "duration_ms": (time.time() - start_time) * 1000,
                    "token_counts": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "from_cache": False,
                    "was_streaming": False,
                    "is_error": True,
                }
                proxy_events.append(error_event)
                await broadcast_event(error_event)

                # Re-raise to return error to Claude Code
                raise HTTPException(status_code=response.status_code, detail=f"Anthropic API error: {response.text}")

            response_data = response.json()

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        log_exception(e, "[{request_id}] Unexpected error calling Anthropic API", request_id=request_id)
        # Broadcast error event
        error_event = {
            "id": str(uuid4()),
            "request_id": request_id,
            "timestamp": datetime.now().isoformat(),
            "user_message": user_message,
            "request": request_data,
            "response": {"error": str(e), "error_type": type(e).__name__},
            "duration_ms": (time.time() - start_time) * 1000,
            "token_counts": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "from_cache": False,
            "was_streaming": False,
            "is_error": True,
        }
        proxy_events.append(error_event)
        await broadcast_event(error_event)
        raise HTTPException(status_code=500, detail=str(e))

    # Calculate duration
    duration_ms = (time.time() - start_time) * 1000

    # Create event for broadcasting
    event = {
        "id": str(uuid4()),
        "request_id": request_id,
        "timestamp": datetime.now().isoformat(),
        "user_message": user_message,
        "request": request_data,
        "response": response_data,
        "duration_ms": duration_ms,
        "token_counts": extract_token_counts(response_data),
        "from_cache": False,
        "was_streaming": False,
        "is_duplicate": is_duplicate,
        "duplicate_of": duplicate_of,
    }

    # Check model type
    is_haiku = "haiku" in request.model.lower()
    is_opus_or_sonnet = "opus" in request.model.lower() or "sonnet" in request.model.lower()

    # Store the event
    proxy_events.append(event)

    # Decide whether to broadcast or hold
    if is_opus_or_sonnet and not is_duplicate:
        # First Opus/Sonnet response - hold it
        logger.info("[{}] Holding first response, waiting for duplicate", request_id)
        pending_responses[base_signature] = event
    elif is_opus_or_sonnet and is_duplicate:
        # Duplicate Opus/Sonnet response - broadcast it and clear pending
        logger.info("[{}] Broadcasting duplicate response", request_id)
        await broadcast_event(event)
        # Clear the pending response
        if base_signature in pending_responses:
            del pending_responses[base_signature]
    elif is_haiku and is_duplicate:
        # Duplicate Haiku response - suppress it entirely
        logger.info("[{}] Suppressing duplicate Haiku response", request_id)
        # Don't broadcast, just skip
    else:
        # First Haiku response or unknown model - broadcast immediately
        await broadcast_event(event)

    logger.info("[{}] Request completed in {:.0f}ms", request_id, duration_ms)

    # Return the response to Claude Code
    return response_data


@app.get("/")
async def root() -> RedirectResponse:
    """Root endpoint that redirects to the viewer."""

    return RedirectResponse(url="/viewer", status_code=302)


@app.get("/viewer")
async def viewer() -> FileResponse:
    """Serve the viewer interface."""
    return FileResponse(str(REACT_BUILD_PATH / "index.html"))


# This catch-all route MUST come after all other specific routes
@app.api_route("/{path:path}", methods=["POST", "GET", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def catch_all(path: str, request: Request) -> None:
    """Catch-all route to detect unexpected API calls."""
    logger.info("UNEXPECTED ROUTE ACCESSED: /{}", path)
    logger.info("  Method: {}", request.method)
    logger.info("  Headers: {}", dict(request.headers))
    if request.method in ["POST", "PUT", "PATCH"]:
        try:
            body = await request.body()
            logger.info("  Body preview: {}...", body[:500].decode("utf-8", errors="ignore"))
        except Exception:
            pass

    # Return 404 for unexpected routes
    raise HTTPException(status_code=404, detail=f"Route /{path} not found")


def open_browser(port: int) -> None:
    # Wait a moment for the server to start
    time.sleep(1.5)
    viewer_url = f"http://localhost:{port}/viewer"
    logger.info("Opening browser to {}", viewer_url)
    webbrowser.open(viewer_url)


def main() -> None:
    """Main entry point for the proxy server."""
    parser = argparse.ArgumentParser(
        description="Claude Code Proxy Viewer - Real-time visualization of API interactions"
    )
    parser.add_argument("--port", type=int, default=8082, help="Port to run the proxy server on (default: 8082)")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the server to (default: 0.0.0.0)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    # Check for API key
    if not os.environ.get("ANTHROPIC_API_KEY"):
        # TODO: is there a reason to do logger.error and sys.exit(1) instead of raising an exception?
        logger.error(
            "ANTHROPIC_API_KEY environment variable is not set!\nPlease set it before running the proxy:\n  export ANTHROPIC_API_KEY=your-api-key"
        )
        sys.exit(1)

    # Print startup information
    logger.info("=" * 60)
    logger.info("Claude Code Proxy Viewer")
    logger.info("=" * 60)
    logger.info("Proxy URL: http://localhost:{}", args.port)
    logger.info("Viewer URL: http://localhost:{}/viewer", args.port)
    logger.info("WebSocket URL: ws://localhost:{}/ws", args.port)
    logger.info("=" * 60)
    logger.info("Configure Claude Code to use this proxy:")
    logger.info('  export ANTHROPIC_BASE_URL="http://localhost:{}"', args.port)
    logger.info("=" * 60)

    # Open browser to viewer if not disabled
    if not args.no_browser:
        browser_thread = threading.Thread(target=open_browser, args=((args.port,)), daemon=True)
        browser_thread.start()

    # Run the server
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="warning",  # Reduce uvicorn's verbosity
    )


if __name__ == "__main__":
    main()
