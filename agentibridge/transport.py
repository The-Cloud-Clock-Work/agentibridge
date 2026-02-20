"""HTTP transport with API key authentication for remote access.

Enables the AgentiBridge MCP server to be accessed remotely via
streamable HTTP (/mcp endpoint), with API key validation.
Legacy SSE transport (/sse) is also supported for backward compatibility.

Usage:
    AGENTIBRIDGE_TRANSPORT=sse AGENTIBRIDGE_PORT=8100 python -m agentibridge

Remote clients connect via:
    {"url": "http://host:8100/mcp", "headers": {"X-API-Key": "your-key"}}

Environment:
    AGENTIBRIDGE_TRANSPORT  — "stdio" (default) or "sse"
    AGENTIBRIDGE_HOST       — Bind address (default: 127.0.0.1)
    AGENTIBRIDGE_PORT       — HTTP port (default: 8100)
    AGENTIBRIDGE_API_KEYS   — Comma-separated valid API keys (empty = no auth)
"""

import json
import os
import sys
from typing import List, Optional

from agentibridge.logging import log


# =============================================================================
# API KEY AUTH
# =============================================================================


def _get_api_keys() -> List[str]:
    """Load valid API keys from environment."""
    raw = os.getenv("AGENTIBRIDGE_API_KEYS", "")
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

# OAuth endpoint paths that must be publicly accessible.
_OAUTH_PUBLIC_PATHS = frozenset({"/authorize", "/token", "/register", "/revoke"})


def _is_oauth_public_path(path: str) -> bool:
    """Check if a path should bypass auth for OAuth protocol endpoints."""
    return path in _OAUTH_PUBLIC_PATHS or path.startswith("/.well-known/")


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


class OAuthCompatAuthMiddleware:
    """ASGI middleware for dual auth: OAuth Bearer tokens + API keys.

    Used when OAuth is enabled. Routes:
    - /health, OAuth endpoints, /.well-known/* → pass through (public)
    - /mcp + X-API-Key → convert to Authorization: Bearer header, pass to FastMCP
    - /mcp + Authorization: Bearer → pass through (FastMCP validates)
    - /mcp + nothing → pass through (FastMCP returns 401)
    - /sse, /messages + API key → validate with API key auth
    - /sse, /messages + no key → reject 401
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

        # Public paths: health + OAuth protocol endpoints
        if path in _PUBLIC_PATHS or _is_oauth_public_path(path):
            await self.app(scope, receive, send)
            return

        # /mcp path: handled by FastMCP's built-in Bearer auth
        if path.startswith("/mcp"):
            # Check for X-API-Key header and convert to Bearer for FastMCP
            headers = list(scope.get("headers", []))
            api_key = None
            has_auth_header = False

            for header_name, header_value in headers:
                name_lower = header_name.lower() if isinstance(header_name, bytes) else header_name
                if name_lower == b"x-api-key":
                    api_key = header_value.decode("utf-8") if isinstance(header_value, bytes) else header_value
                if name_lower == b"authorization":
                    has_auth_header = True

            if api_key and not has_auth_header:
                # Convert API key to Bearer token so FastMCP's auth can validate it
                new_headers = [h for h in headers if h[0].lower() != b"x-api-key"]
                new_headers.append([b"authorization", f"Bearer {api_key}".encode()])
                scope = {**scope, "headers": new_headers}

            await self.app(scope, receive, send)
            return

        # SSE/messages paths: use API key auth (existing behavior)
        if self.auth_enabled:
            key = None
            for header_name, header_value in scope.get("headers", []):
                if header_name.lower() == b"x-api-key":
                    key = header_value.decode("utf-8")
                    break

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
    body = json.dumps({"status": "ok", "service": "agentibridge"}).encode()
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
                        [b"access-control-allow-headers", b"content-type, x-api-key, authorization"],
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


def _build_app(mcp):
    """Build the ASGI app stack with both /mcp and /sse endpoints.

    Wraps FastMCP's apps with:
    1. CORS middleware (outermost)
    2. Auth middleware (OAuthCompat when OAuth enabled, APIKey otherwise)
    3. Health endpoint router
    4. Dual-transport router: /mcp (streamable HTTP) + /sse (legacy)

    When OAuth is enabled, OAuth protocol paths (/authorize, /token,
    /register, /revoke, /.well-known/*) are routed to the HTTP app
    alongside /mcp.
    """
    from contextlib import asynccontextmanager

    http_app = mcp.streamable_http_app()  # Starlette app with /mcp route + lifespan
    sse_app = mcp.sse_app()  # Starlette app with /sse, /messages

    oauth_enabled = mcp.settings.auth is not None

    # The streamable HTTP app needs its session manager lifespan started.
    # We call it directly, then route /mcp to the HTTP app and everything
    # else to the legacy SSE app.
    session_manager = mcp.session_manager

    @asynccontextmanager
    async def lifespan():
        async with session_manager.run():
            yield

    _lifespan_cm = None

    async def dual_transport(scope, receive, send):
        nonlocal _lifespan_cm
        if scope["type"] == "lifespan":
            # Start the streamable HTTP session manager on startup
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    _lifespan_cm = lifespan()
                    await _lifespan_cm.__aenter__()
                    await send({"type": "lifespan.startup.complete"})
                except Exception:
                    await send({"type": "lifespan.startup.failed"})
                    return
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                if _lifespan_cm:
                    await _lifespan_cm.__aexit__(None, None, None)
                await send({"type": "lifespan.shutdown.complete"})
            return

        path = scope.get("path", "")

        # Route to HTTP app: /mcp + OAuth endpoints (when enabled)
        if path.startswith("/mcp"):
            await http_app(scope, receive, send)
        elif oauth_enabled and _is_oauth_public_path(path):
            await http_app(scope, receive, send)
        else:
            await sse_app(scope, receive, send)

    app = dual_transport

    app = HealthRouter(app)
    if oauth_enabled:
        app = OAuthCompatAuthMiddleware(app)
    else:
        app = APIKeyAuthMiddleware(app)
    app = CORSMiddleware(app)
    return app


def run_sse_server(mcp) -> None:
    """Build the ASGI app stack and run with uvicorn.

    Serves both /mcp (streamable HTTP, preferred) and /sse (legacy)
    endpoints behind auth + CORS middleware.

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
    oauth_enabled = mcp.settings.auth is not None

    if oauth_enabled:
        print("OAuth 2.1 auth enabled (API keys also accepted)", file=sys.stderr)
    if api_keys:
        print(f"API key auth enabled ({len(api_keys)} key(s))", file=sys.stderr)
    elif not oauth_enabled:
        print("WARNING: No API keys configured — endpoint is unauthenticated", file=sys.stderr)

    app = _build_app(mcp)

    host = mcp.settings.host
    port = mcp.settings.port

    print(f"MCP transport ready on {host}:{port}", file=sys.stderr)
    print(f"  Streamable HTTP: http://{host}:{port}/mcp", file=sys.stderr)
    print(f"  Legacy SSE:      http://{host}:{port}/sse", file=sys.stderr)
    print(f"  Health check:    http://{host}:{port}/health", file=sys.stderr)
    if oauth_enabled:
        print(f"  OAuth metadata:  http://{host}:{port}/.well-known/oauth-authorization-server", file=sys.stderr)

    uvicorn.run(app, host=host, port=port, log_level="info")
