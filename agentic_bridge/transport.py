"""SSE/HTTP transport with API key authentication for remote access.

Enables the session-bridge MCP server to be accessed remotely via
SSE (Server-Sent Events) over HTTP, with API key validation.

Usage:
    SESSION_BRIDGE_TRANSPORT=sse SESSION_BRIDGE_PORT=8100 python -m agentic_bridge

Remote clients connect via:
    {"url": "http://host:8100/sse", "headers": {"X-API-Key": "your-key"}}

Environment:
    SESSION_BRIDGE_TRANSPORT  — "stdio" (default) or "sse"
    SESSION_BRIDGE_HOST       — Bind address (default: 127.0.0.1)
    SESSION_BRIDGE_PORT       — HTTP port (default: 8100)
    SESSION_BRIDGE_API_KEYS   — Comma-separated valid API keys (empty = no auth)
"""

import json
import os
import sys
from typing import List, Optional

from agentic_bridge.logging import log


# =============================================================================
# API KEY AUTH
# =============================================================================


def _get_api_keys() -> List[str]:
    """Load valid API keys from environment."""
    raw = os.getenv("SESSION_BRIDGE_API_KEYS", "")
    if not raw.strip():
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def validate_api_key(key: Optional[str]) -> bool:
    """Check if the provided key is valid.

    Returns True if:
    - No API keys configured (auth disabled)
    - Key matches one of the configured keys
    """
    valid_keys = _get_api_keys()
    if not valid_keys:
        return True  # No auth configured
    return key in valid_keys


# =============================================================================
# ASGI AUTH MIDDLEWARE
# =============================================================================

# Paths that bypass authentication.
_PUBLIC_PATHS = frozenset({"/health"})


class APIKeyAuthMiddleware:
    """ASGI middleware that validates X-API-Key header or api_key query param.

    Uses raw ASGI (not BaseHTTPMiddleware) so it correctly intercepts
    both regular HTTP requests and long-lived SSE connections.
    """

    def __init__(self, app):
        self.app = app
        self.api_keys = _get_api_keys()
        self.auth_enabled = len(self.api_keys) > 0

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip auth for public paths
        if path in _PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        if self.auth_enabled:
            # Extract API key from headers
            key = None
            for header_name, header_value in scope.get("headers", []):
                if header_name.lower() == b"x-api-key":
                    key = header_value.decode("utf-8")
                    break

            # Fallback: check query string
            if key is None:
                qs = scope.get("query_string", b"").decode("utf-8")
                for param in qs.split("&"):
                    if param.startswith("api_key="):
                        key = param[8:]
                        break

            if not validate_api_key(key):
                log("SSE auth rejected", {"path": path})
                body = json.dumps({"error": "Invalid or missing API key"}).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            [b"content-type", b"application/json"],
                            [b"content-length", str(len(body)).encode()],
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

        await self.app(scope, receive, send)


# =============================================================================
# HEALTH ENDPOINT
# =============================================================================


async def _health_endpoint(scope, receive, send):
    """Lightweight /health ASGI endpoint."""
    body = json.dumps({"status": "ok", "service": "session-bridge"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"application/json"],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HealthRouter:
    """ASGI app that routes /health to a handler, everything else to inner app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/health":
            await _health_endpoint(scope, receive, send)
            return
        await self.app(scope, receive, send)


# =============================================================================
# CORS MIDDLEWARE (simple ASGI wrapper)
# =============================================================================


class CORSMiddleware:
    """Minimal CORS middleware that adds permissive headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Handle preflight
        method = None
        for header_name, header_value in scope.get("headers", []):
            if header_name.lower() == b"access-control-request-method":
                method = header_value
                break

        if scope.get("method") == "OPTIONS" and method is not None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [
                        [b"access-control-allow-origin", b"*"],
                        [b"access-control-allow-methods", b"GET, POST, OPTIONS"],
                        [b"access-control-allow-headers", b"content-type, x-api-key"],
                        [b"access-control-max-age", b"86400"],
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b""})
            return

        # Wrap send to inject CORS headers on responses
        async def cors_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append([b"access-control-allow-origin", b"*"])
                headers.append([b"access-control-expose-headers", b"*"])
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, cors_send)


# =============================================================================
# ENTRYPOINT
# =============================================================================


def run_sse_server(mcp) -> None:
    """Build the ASGI app stack and run with uvicorn.

    Wraps FastMCP's SSE app with:
    1. CORS middleware (outermost)
    2. API key auth middleware
    3. Health endpoint router
    4. FastMCP SSE app (innermost)

    Args:
        mcp: The FastMCP server instance (with host/port already configured)
    """
    try:
        import uvicorn
    except ImportError as e:
        print(f"SSE transport requires uvicorn: {e}", file=sys.stderr)
        print("Install with: pip install uvicorn", file=sys.stderr)
        sys.exit(1)

    api_keys = _get_api_keys()
    if api_keys:
        print(f"API key auth enabled ({len(api_keys)} key(s))", file=sys.stderr)
    else:
        print("WARNING: No API keys configured — SSE endpoint is unauthenticated", file=sys.stderr)

    # Get the Starlette SSE app from FastMCP
    sse_app = mcp.sse_app()

    # Build middleware stack (applied inside-out):
    #   request -> CORS -> Auth -> Health -> SSE app
    app = HealthRouter(sse_app)
    app = APIKeyAuthMiddleware(app)
    app = CORSMiddleware(app)

    host = mcp.settings.host
    port = mcp.settings.port

    print(f"SSE transport ready on {host}:{port}", file=sys.stderr)
    print(f"  SSE endpoint: http://{host}:{port}/sse", file=sys.stderr)
    print(f"  Health check: http://{host}:{port}/health", file=sys.stderr)

    uvicorn.run(app, host=host, port=port, log_level="info")
