"""Host-side HTTP bridge for dispatching Claude CLI calls from Docker containers.

When AgentiBridge runs in Docker, the ``claude`` CLI binary and auth
credentials aren't available inside the container. This lightweight HTTP
server runs on the **host** and proxies dispatch requests to the local
CLI.

Usage:
    DISPATCH_BRIDGE_SECRET=mysecret python -m agentibridge.dispatch_bridge

Env vars:
    DISPATCH_BRIDGE_SECRET  — shared secret (required, refuses to start without it)
    DISPATCH_BRIDGE_HOST    — bind address (default: 127.0.0.1)
    DISPATCH_BRIDGE_PORT    — listen port (default: 8101)
    CLAUDE_DISPATCH_TIMEOUT — max timeout cap in seconds (default: 600)
"""

import json
import os
import sys

from agentibridge.claude_runner import ClaudeResult, run_claude
from agentibridge.logging import log


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _bridge_secret() -> str:
    return os.environ.get("DISPATCH_BRIDGE_SECRET", "")


def _bridge_host() -> str:
    return os.environ.get("DISPATCH_BRIDGE_HOST", "127.0.0.1")


def _bridge_port() -> int:
    return int(os.environ.get("DISPATCH_BRIDGE_PORT", "8101"))


def _max_timeout() -> int:
    return int(os.environ.get("CLAUDE_DISPATCH_TIMEOUT", "600"))


# ---------------------------------------------------------------------------
# ASGI application (pure Starlette-style, no FastAPI needed)
# ---------------------------------------------------------------------------


async def _read_body(receive) -> bytes:
    """Read full request body from ASGI receive callable."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    return body


async def _send_json(send, status: int, data: dict) -> None:
    """Send a JSON response via ASGI send callable."""
    payload = json.dumps(data).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(payload)).encode()],
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _get_header(scope, name: bytes) -> str:
    """Extract a header value from ASGI scope."""
    for key, value in scope.get("headers", []):
        if key.lower() == name.lower():
            return value.decode()
    return ""


async def app(scope, receive, send):
    """ASGI application for the dispatch bridge."""
    if scope["type"] != "http":
        return

    path = scope["path"]
    method = scope["method"]

    # GET /health
    if path == "/health" and method == "GET":
        await _send_json(send, 200, {"status": "ok"})
        return

    # POST /dispatch
    if path == "/dispatch" and method == "POST":
        await _handle_dispatch(scope, receive, send)
        return

    await _send_json(send, 404, {"error": "Not found"})


async def _handle_dispatch(scope, receive, send):
    """Handle POST /dispatch — validate auth, run claude, return result."""
    secret = _bridge_secret()

    # Authenticate
    provided = _get_header(scope, b"x-dispatch-secret")
    if not provided or provided != secret:
        log("dispatch_bridge: auth failed", {"provided_length": len(provided)})
        await _send_json(send, 401, {"error": "Unauthorized"})
        return

    # Parse body
    body = await _read_body(receive)
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        await _send_json(send, 400, {"error": "Invalid JSON body"})
        return

    prompt = data.get("prompt", "")
    if not prompt:
        await _send_json(send, 400, {"error": "Missing required field: prompt"})
        return

    model = data.get("model", "sonnet")
    output_format = data.get("output_format", "json")
    timeout = data.get("timeout", _max_timeout())

    # Cap timeout
    max_t = _max_timeout()
    if timeout > max_t:
        timeout = max_t

    log(
        "dispatch_bridge: dispatching",
        {
            "model": model,
            "prompt_len": len(prompt),
            "timeout": timeout,
        },
    )

    # Run Claude CLI
    result: ClaudeResult = await run_claude(
        prompt=prompt,
        model=model,
        timeout=timeout,
        output_format=output_format,
    )

    await _send_json(send, 200, result.to_dict())


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    """Start the dispatch bridge server."""
    secret = _bridge_secret()
    if not secret:
        print("ERROR: DISPATCH_BRIDGE_SECRET env var is required.", file=sys.stderr)
        print(
            "Set it before starting: DISPATCH_BRIDGE_SECRET=mysecret python -m agentibridge.dispatch_bridge",
            file=sys.stderr,
        )
        sys.exit(1)

    host = _bridge_host()
    port = _bridge_port()

    print(f"Dispatch bridge starting on {host}:{port}")
    print(f"Secret configured: {'*' * len(secret)}")

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
