"""Host-side HTTP bridge for dispatching Claude CLI calls from Docker containers.

When AgentiBridge runs in Docker, the ``claude`` CLI binary and auth
credentials aren't available inside the container. This lightweight HTTP
server runs on the **host** and proxies dispatch requests to the local
CLI.

Uses Python's built-in ``asyncio`` HTTP server — no uvicorn or SSL
required, so it works even when the Python build lacks ``_ssl``.

Usage:
    DISPATCH_BRIDGE_SECRET=mysecret python -m agentibridge.dispatch_bridge

Env vars:
    DISPATCH_BRIDGE_SECRET  — shared secret (required, refuses to start without it)
    DISPATCH_BRIDGE_HOST    — bind address (default: 127.0.0.1)
    DISPATCH_BRIDGE_PORT    — listen port (default: 8101)
    CLAUDE_DISPATCH_TIMEOUT — max timeout cap in seconds (default: 600)
"""

import asyncio
import json
import os
import sys
from http import HTTPStatus

from agentibridge.claude_runner import ClaudeResult, run_claude
from agentibridge.logging import log


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _bridge_secret() -> str:
    return os.environ.get("DISPATCH_BRIDGE_SECRET", "")


def _bridge_host() -> str:
    return os.environ.get("DISPATCH_BRIDGE_HOST", "0.0.0.0")


def _bridge_port() -> int:
    return int(os.environ.get("DISPATCH_BRIDGE_PORT", "8101"))


def _max_timeout() -> int:
    return int(os.environ.get("CLAUDE_DISPATCH_TIMEOUT", "600"))


# ---------------------------------------------------------------------------
# Minimal asyncio HTTP server (no external deps)
# ---------------------------------------------------------------------------


async def _send_response(writer: asyncio.StreamWriter, status: int, body: dict) -> None:
    """Write an HTTP/1.1 JSON response and close."""
    payload = json.dumps(body).encode()
    reason = HTTPStatus(status).phrase
    header = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    writer.write(header.encode() + payload)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


def _parse_headers(raw_headers: str) -> dict:
    """Parse HTTP headers into a lowercase-keyed dict."""
    headers = {}
    for line in raw_headers.split("\r\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return headers


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Handle a single HTTP connection."""
    try:
        # Read request line + headers (up to 64KB)
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=10)
            if not chunk:
                writer.close()
                return
            raw += chunk
            if len(raw) > 65536:
                await _send_response(writer, 413, {"error": "Request too large"})
                return

        header_end = raw.index(b"\r\n\r\n")
        header_part = raw[:header_end].decode("utf-8", errors="replace")
        body_start = raw[header_end + 4 :]

        # Parse request line
        lines = header_part.split("\r\n")
        request_line = lines[0]
        parts = request_line.split(" ", 2)
        if len(parts) < 2:
            await _send_response(writer, 400, {"error": "Bad request"})
            return

        method, path = parts[0], parts[1]
        headers = _parse_headers("\r\n".join(lines[1:]))

        # Read remaining body if Content-Length specified
        body = body_start
        content_length = int(headers.get("content-length", "0"))
        remaining = content_length - len(body)
        while remaining > 0:
            chunk = await asyncio.wait_for(reader.read(min(remaining, 65536)), timeout=30)
            if not chunk:
                break
            body += chunk
            remaining -= len(chunk)

        # Route
        if path == "/health" and method == "GET":
            await _send_response(writer, 200, {"status": "ok"})
            return

        if path == "/dispatch" and method == "POST":
            await _handle_dispatch(headers, body, writer)
            return

        await _send_response(writer, 404, {"error": "Not found"})

    except asyncio.TimeoutError:
        try:
            await _send_response(writer, 408, {"error": "Request timeout"})
        except Exception:
            pass
    except Exception as e:
        log("dispatch_bridge: connection error", {"error": str(e)})
        try:
            await _send_response(writer, 500, {"error": "Internal server error"})
        except Exception:
            pass


async def _handle_dispatch(headers: dict, body: bytes, writer: asyncio.StreamWriter) -> None:
    """Handle POST /dispatch — validate auth, run claude, return result."""
    secret = _bridge_secret()

    # Authenticate
    provided = headers.get("x-dispatch-secret", "")
    if not provided or provided != secret:
        log("dispatch_bridge: auth failed", {"provided_length": len(provided)})
        await _send_response(writer, 401, {"error": "Unauthorized"})
        return

    # Parse body
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        await _send_response(writer, 400, {"error": "Invalid JSON body"})
        return

    prompt = data.get("prompt", "")
    if not prompt:
        await _send_response(writer, 400, {"error": "Missing required field: prompt"})
        return

    model = data.get("model", "sonnet")
    output_format = data.get("output_format", "json")
    timeout = data.get("timeout", _max_timeout())
    resume_session_id = data.get("resume_session_id", "") or None

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
            "resume_session_id": resume_session_id,
        },
    )

    # Run Claude CLI
    result: ClaudeResult = await run_claude(
        prompt=prompt,
        model=model,
        timeout=timeout,
        output_format=output_format,
        resume_session_id=resume_session_id,
    )

    await _send_response(writer, 200, result.to_dict())


# ---------------------------------------------------------------------------
# ASGI app (kept for backward compatibility with tests)
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
    """ASGI application for the dispatch bridge (for testing / uvicorn)."""
    if scope["type"] != "http":
        return

    path = scope["path"]
    method = scope["method"]

    if path == "/health" and method == "GET":
        await _send_json(send, 200, {"status": "ok"})
        return

    if path == "/dispatch" and method == "POST":
        secret = _bridge_secret()
        provided = _get_header(scope, b"x-dispatch-secret")
        if not provided or provided != secret:
            log("dispatch_bridge: auth failed", {"provided_length": len(provided)})
            await _send_json(send, 401, {"error": "Unauthorized"})
            return

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
        resume_session_id = data.get("resume_session_id", "") or None
        max_t = _max_timeout()
        if timeout > max_t:
            timeout = max_t

        log(
            "dispatch_bridge: dispatching",
            {"model": model, "prompt_len": len(prompt), "timeout": timeout, "resume_session_id": resume_session_id},
        )
        result: ClaudeResult = await run_claude(
            prompt=prompt,
            model=model,
            timeout=timeout,
            output_format=output_format,
            resume_session_id=resume_session_id,
        )
        await _send_json(send, 200, result.to_dict())
        return

    await _send_json(send, 404, {"error": "Not found"})


# ---------------------------------------------------------------------------
# Entrypoint (stdlib asyncio server — no uvicorn needed)
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

    async def _serve():
        server = await asyncio.start_server(_handle_connection, host, port)
        print(f"Dispatch bridge listening on {host}:{port}")
        async with server:
            await server.serve_forever()

    asyncio.run(_serve())


if __name__ == "__main__":
    main()
