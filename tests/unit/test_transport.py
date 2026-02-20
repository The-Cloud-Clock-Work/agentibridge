"""Unit tests for agentic_bridge.transport module.

Tests API key auth, ASGI middleware (auth, CORS, health), and helpers.
"""

import json

import pytest


# ---------------------------------------------------------------------------
# Helpers for ASGI testing
# ---------------------------------------------------------------------------


def _make_http_scope(
    path="/test",
    method="GET",
    headers=None,
    query_string=b"",
    scope_type="http",
):
    """Build a minimal ASGI HTTP scope dict."""
    if headers is None:
        headers = []
    return {
        "type": scope_type,
        "path": path,
        "method": method,
        "headers": headers,
        "query_string": query_string,
    }


class _Recorder:
    """Collects messages passed to the ASGI *send* callable."""

    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        for m in self.messages:
            if m["type"] == "http.response.start":
                return m["status"]
        return None

    @property
    def body(self):
        for m in self.messages:
            if m["type"] == "http.response.body":
                return m.get("body", b"")
        return b""

    @property
    def response_headers(self):
        """Return headers from http.response.start as a dict (lowercase keys)."""
        for m in self.messages:
            if m["type"] == "http.response.start":
                return {
                    h[0].decode() if isinstance(h[0], bytes) else h[0]: h[1].decode()
                    if isinstance(h[1], bytes)
                    else h[1]
                    for h in m.get("headers", [])
                }
        return {}


async def _noop_receive():
    """Dummy ASGI receive callable."""
    return {"type": "http.disconnect"}


class _DummyApp:
    """A trivial ASGI app that returns 200 with a fixed body."""

    def __init__(self, body=b"OK"):
        self.body = body
        self.called = False
        self.last_scope = None

    async def __call__(self, scope, receive, send):
        self.called = True
        self.last_scope = scope
        response_body = self.body
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"text/plain"],
                    [b"content-length", str(len(response_body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": response_body})


# ============================================================================
# _get_api_keys
# ============================================================================


@pytest.mark.unit
class TestGetApiKeys:
    """Tests for _get_api_keys()."""

    def test_no_env_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == []

    def test_empty_string_returns_empty(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == []

    def test_whitespace_only_returns_empty(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "   ")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == []

    def test_single_key(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret123")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == ["secret123"]

    def test_multiple_comma_separated_keys(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "key1,key2,key3")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == ["key1", "key2", "key3"]

    def test_whitespace_around_keys_is_stripped(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", " key1 , key2 , key3 ")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == ["key1", "key2", "key3"]

    def test_trailing_comma_ignored(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "key1,key2,")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == ["key1", "key2"]

    def test_empty_segments_ignored(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "key1,,key2,,,key3")
        from agentic_bridge.transport import _get_api_keys

        assert _get_api_keys() == ["key1", "key2", "key3"]


# ============================================================================
# validate_api_key
# ============================================================================


@pytest.mark.unit
class TestValidateApiKey:
    """Tests for validate_api_key()."""

    def test_no_keys_configured_any_key_valid(self, monkeypatch):
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key("anything") is True

    def test_no_keys_configured_none_valid(self, monkeypatch):
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key(None) is True

    def test_no_keys_configured_empty_string_valid(self, monkeypatch):
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key("") is True

    def test_valid_key_accepted(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret1,secret2")
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key("secret1") is True
        assert validate_api_key("secret2") is True

    def test_invalid_key_rejected(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret1,secret2")
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key("wrong-key") is False

    def test_none_key_rejected_when_auth_enabled(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret1")
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key(None) is False

    def test_empty_string_rejected_when_auth_enabled(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret1")
        from agentic_bridge.transport import validate_api_key

        assert validate_api_key("") is False


# ============================================================================
# APIKeyAuthMiddleware
# ============================================================================


@pytest.mark.unit
class TestAPIKeyAuthMiddleware:
    """Tests for APIKeyAuthMiddleware ASGI middleware."""

    @pytest.mark.asyncio
    async def test_auth_disabled_passes_through(self, monkeypatch):
        """When no API keys are configured, all requests pass through."""
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(path="/some-endpoint")
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_auth_enabled_valid_header_passes(self, monkeypatch):
        """Valid X-API-Key header passes through."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"my-secret"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_auth_enabled_invalid_key_returns_401(self, monkeypatch):
        """Invalid API key returns 401 and does not call inner app."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"wrong-key"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401
        body = json.loads(recorder.body)
        assert "error" in body
        assert "API key" in body["error"]

    @pytest.mark.asyncio
    async def test_auth_enabled_missing_key_returns_401(self, monkeypatch):
        """No API key at all returns 401."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(path="/sse", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401

    @pytest.mark.asyncio
    async def test_key_in_query_string_passes(self, monkeypatch):
        """API key provided via query string parameter passes through."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "qs-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[],
            query_string=b"api_key=qs-key",
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_query_string_key_with_other_params(self, monkeypatch):
        """API key in query string alongside other parameters."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "qs-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[],
            query_string=b"foo=bar&api_key=qs-key&baz=qux",
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_invalid_query_string_key_returns_401(self, monkeypatch):
        """Invalid API key in query string returns 401."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "qs-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[],
            query_string=b"api_key=wrong-key",
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401

    @pytest.mark.asyncio
    async def test_header_check_is_case_insensitive(self, monkeypatch):
        """Header name matching for X-API-Key is case insensitive."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        # Various capitalizations of the header name
        header_variants = [
            b"x-api-key",
            b"X-API-Key",
            b"X-API-KEY",
            b"X-Api-Key",
            b"x-Api-key",
        ]

        for header_name in header_variants:
            inner = _DummyApp()
            mw = APIKeyAuthMiddleware(inner)
            scope = _make_http_scope(
                path="/sse",
                headers=[[header_name, b"my-secret"]],
            )
            recorder = _Recorder()

            await mw(scope, _noop_receive, recorder)

            assert inner.called is True, f"Header {header_name!r} should be accepted (case insensitive)"
            assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_header_preferred_over_query_string(self, monkeypatch):
        """When header is present, it is used even if query string also has a key."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "header-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"header-key"]],
            query_string=b"api_key=wrong-key",
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        # Header key is valid, so request passes even though query key is wrong
        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_health_path_bypasses_auth(self, monkeypatch):
        """/health path should bypass authentication even when auth is enabled."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        # No API key provided, but path is /health
        scope = _make_http_scope(path="/health", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self, monkeypatch):
        """Non-http/websocket scope types pass through without auth check."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = {"type": "lifespan"}
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True

    @pytest.mark.asyncio
    async def test_websocket_scope_requires_auth(self, monkeypatch):
        """Websocket scope type also requires authentication."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "ws-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/ws",
            scope_type="websocket",
            headers=[[b"x-api-key", b"ws-key"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True

    @pytest.mark.asyncio
    async def test_websocket_scope_rejects_invalid_key(self, monkeypatch):
        """Websocket scope rejects invalid key with 401."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "ws-key")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/ws",
            scope_type="websocket",
            headers=[[b"x-api-key", b"wrong"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401

    @pytest.mark.asyncio
    async def test_401_response_content_type_is_json(self, monkeypatch):
        """401 response has application/json content type."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-secret")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        inner = _DummyApp()
        mw = APIKeyAuthMiddleware(inner)
        scope = _make_http_scope(path="/sse", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        headers = recorder.response_headers
        assert headers.get("content-type") == "application/json"

    @pytest.mark.asyncio
    async def test_multiple_keys_any_valid_passes(self, monkeypatch):
        """When multiple API keys are configured, any valid key passes."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "key1,key2,key3")
        from agentic_bridge.transport import APIKeyAuthMiddleware

        for key_value in [b"key1", b"key2", b"key3"]:
            inner = _DummyApp()
            mw = APIKeyAuthMiddleware(inner)
            scope = _make_http_scope(
                path="/sse",
                headers=[[b"x-api-key", key_value]],
            )
            recorder = _Recorder()

            await mw(scope, _noop_receive, recorder)

            assert inner.called is True, f"Key {key_value!r} should be valid"
            assert recorder.status == 200


# ============================================================================
# HealthRouter
# ============================================================================


@pytest.mark.unit
class TestHealthRouter:
    """Tests for HealthRouter ASGI app."""

    @pytest.mark.asyncio
    async def test_health_returns_ok_json(self):
        """/health returns 200 with expected JSON payload."""
        from agentic_bridge.transport import HealthRouter

        inner = _DummyApp()
        router = HealthRouter(inner)
        scope = _make_http_scope(path="/health")
        recorder = _Recorder()

        await router(scope, _noop_receive, recorder)

        assert inner.called is False  # Health handled by router, not inner
        assert recorder.status == 200
        body = json.loads(recorder.body)
        assert body == {"status": "ok", "service": "session-bridge"}

    @pytest.mark.asyncio
    async def test_health_content_type_is_json(self):
        """/health response has application/json content type."""
        from agentic_bridge.transport import HealthRouter

        inner = _DummyApp()
        router = HealthRouter(inner)
        scope = _make_http_scope(path="/health")
        recorder = _Recorder()

        await router(scope, _noop_receive, recorder)

        headers = recorder.response_headers
        assert headers.get("content-type") == "application/json"

    @pytest.mark.asyncio
    async def test_non_health_path_delegates_to_inner(self):
        """Non-/health paths are delegated to the inner app."""
        from agentic_bridge.transport import HealthRouter

        inner = _DummyApp(body=b"inner-response")
        router = HealthRouter(inner)
        scope = _make_http_scope(path="/sse")
        recorder = _Recorder()

        await router(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200
        assert recorder.body == b"inner-response"

    @pytest.mark.asyncio
    async def test_non_http_scope_delegates_to_inner(self):
        """Non-HTTP scope (e.g. lifespan) passes through to inner app."""
        from agentic_bridge.transport import HealthRouter

        inner = _DummyApp()
        router = HealthRouter(inner)
        scope = {"type": "lifespan"}
        recorder = _Recorder()

        await router(scope, _noop_receive, recorder)

        assert inner.called is True

    @pytest.mark.asyncio
    async def test_health_with_different_method(self):
        """/health responds to any HTTP method (GET, POST, etc.)."""
        from agentic_bridge.transport import HealthRouter

        inner = _DummyApp()
        router = HealthRouter(inner)
        scope = _make_http_scope(path="/health", method="POST")
        recorder = _Recorder()

        await router(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 200
        body = json.loads(recorder.body)
        assert body["status"] == "ok"


# ============================================================================
# _health_endpoint
# ============================================================================


@pytest.mark.unit
class TestHealthEndpoint:
    """Tests for the standalone _health_endpoint function."""

    @pytest.mark.asyncio
    async def test_health_endpoint_response(self):
        """_health_endpoint sends correct status and body."""
        from agentic_bridge.transport import _health_endpoint

        scope = _make_http_scope(path="/health")
        recorder = _Recorder()

        await _health_endpoint(scope, _noop_receive, recorder)

        assert recorder.status == 200
        body = json.loads(recorder.body)
        assert body == {"status": "ok", "service": "session-bridge"}

    @pytest.mark.asyncio
    async def test_health_endpoint_sends_two_messages(self):
        """_health_endpoint sends exactly 2 ASGI messages (start + body)."""
        from agentic_bridge.transport import _health_endpoint

        scope = _make_http_scope(path="/health")
        recorder = _Recorder()

        await _health_endpoint(scope, _noop_receive, recorder)

        assert len(recorder.messages) == 2
        assert recorder.messages[0]["type"] == "http.response.start"
        assert recorder.messages[1]["type"] == "http.response.body"

    @pytest.mark.asyncio
    async def test_health_endpoint_content_length_matches(self):
        """Content-Length header matches the actual body length."""
        from agentic_bridge.transport import _health_endpoint

        scope = _make_http_scope(path="/health")
        recorder = _Recorder()

        await _health_endpoint(scope, _noop_receive, recorder)

        headers = recorder.response_headers
        content_length = int(headers["content-length"])
        assert content_length == len(recorder.body)


# ============================================================================
# CORSMiddleware
# ============================================================================


@pytest.mark.unit
class TestCORSMiddleware:
    """Tests for CORSMiddleware ASGI middleware."""

    @pytest.mark.asyncio
    async def test_regular_request_gets_cors_headers(self):
        """Non-preflight request gets Access-Control-Allow-Origin added."""
        from agentic_bridge.transport import CORSMiddleware

        inner = _DummyApp(body=b"hello")
        mw = CORSMiddleware(inner)
        scope = _make_http_scope(path="/sse", method="GET")
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"
        assert headers.get("access-control-expose-headers") == "*"

    @pytest.mark.asyncio
    async def test_options_preflight_returns_204(self):
        """OPTIONS with Access-Control-Request-Method returns 204 preflight."""
        from agentic_bridge.transport import CORSMiddleware

        inner = _DummyApp()
        mw = CORSMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            method="OPTIONS",
            headers=[
                [b"access-control-request-method", b"POST"],
            ],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        # Inner app should NOT be called for preflight
        assert inner.called is False
        assert recorder.status == 204
        assert recorder.body == b""

        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"
        assert "GET" in headers.get("access-control-allow-methods", "")
        assert "POST" in headers.get("access-control-allow-methods", "")
        assert "OPTIONS" in headers.get("access-control-allow-methods", "")
        assert "x-api-key" in headers.get("access-control-allow-headers", "")
        assert "content-type" in headers.get("access-control-allow-headers", "")
        assert headers.get("access-control-max-age") == "86400"

    @pytest.mark.asyncio
    async def test_options_without_request_method_header_not_preflight(self):
        """OPTIONS without Access-Control-Request-Method is treated as a normal request."""
        from agentic_bridge.transport import CORSMiddleware

        inner = _DummyApp(body=b"options-response")
        mw = CORSMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            method="OPTIONS",
            headers=[],  # No access-control-request-method header
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        # Inner app IS called since this is not a preflight
        assert inner.called is True
        assert recorder.status == 200

        # CORS headers still added to regular response
        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_preflight_header_check_is_case_insensitive(self):
        """Preflight detection works regardless of header name casing."""
        from agentic_bridge.transport import CORSMiddleware

        header_variants = [
            b"access-control-request-method",
            b"Access-Control-Request-Method",
            b"ACCESS-CONTROL-REQUEST-METHOD",
        ]

        for header_name in header_variants:
            inner = _DummyApp()
            mw = CORSMiddleware(inner)
            scope = _make_http_scope(
                path="/sse",
                method="OPTIONS",
                headers=[[header_name, b"POST"]],
            )
            recorder = _Recorder()

            await mw(scope, _noop_receive, recorder)

            assert inner.called is False, f"Preflight should be handled for header {header_name!r}"
            assert recorder.status == 204

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        """Non-HTTP scope types are forwarded to inner app without CORS."""
        from agentic_bridge.transport import CORSMiddleware

        inner = _DummyApp()
        mw = CORSMiddleware(inner)
        scope = {"type": "lifespan"}
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True

    @pytest.mark.asyncio
    async def test_cors_headers_added_to_error_responses(self):
        """CORS headers are added even when inner app returns an error status."""

        class _ErrorApp:
            async def __call__(self, scope, receive, send):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [[b"content-type", b"text/plain"]],
                    }
                )
                await send({"type": "http.response.body", "body": b"error"})

        from agentic_bridge.transport import CORSMiddleware

        mw = CORSMiddleware(_ErrorApp())
        scope = _make_http_scope(path="/sse", method="GET")
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert recorder.status == 500
        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_cors_preserves_inner_app_headers(self):
        """CORS middleware does not remove headers set by the inner app."""

        class _CustomHeaderApp:
            async def __call__(self, scope, receive, send):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            [b"content-type", b"application/json"],
                            [b"x-custom", b"value"],
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b"{}"})

        from agentic_bridge.transport import CORSMiddleware

        mw = CORSMiddleware(_CustomHeaderApp())
        scope = _make_http_scope(path="/sse", method="GET")
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        headers = recorder.response_headers
        assert headers.get("content-type") == "application/json"
        assert headers.get("x-custom") == "value"
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_non_response_messages_pass_through_unchanged(self):
        """Non http.response.start messages are forwarded unchanged by cors_send."""

        class _BodyOnlyApp:
            """App that sends body message without response.start first."""

            async def __call__(self, scope, receive, send):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"data",
                    }
                )

        from agentic_bridge.transport import CORSMiddleware

        mw = CORSMiddleware(_BodyOnlyApp())
        scope = _make_http_scope(path="/test", method="GET")
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        # http.response.body message should be passed through as-is
        body_msg = recorder.messages[1]
        assert body_msg["type"] == "http.response.body"
        assert body_msg["body"] == b"data"


# ============================================================================
# Full middleware stack integration
# ============================================================================


@pytest.mark.unit
class TestMiddlewareStack:
    """Test the middleware stack behavior when layered together."""

    @pytest.mark.asyncio
    async def test_full_stack_health_bypasses_auth_with_cors(self, monkeypatch):
        """Health endpoint works through the full stack: CORS -> Auth -> Health."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import (
            APIKeyAuthMiddleware,
            CORSMiddleware,
            HealthRouter,
        )

        inner = _DummyApp()
        app = HealthRouter(inner)
        app = APIKeyAuthMiddleware(app)
        app = CORSMiddleware(app)

        scope = _make_http_scope(path="/health", headers=[])
        recorder = _Recorder()

        await app(scope, _noop_receive, recorder)

        assert recorder.status == 200
        body = json.loads(recorder.body)
        assert body["status"] == "ok"

        # CORS headers should be present
        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_full_stack_auth_rejection_with_cors(self, monkeypatch):
        """Auth rejection through full stack still gets CORS headers."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import (
            APIKeyAuthMiddleware,
            CORSMiddleware,
            HealthRouter,
        )

        inner = _DummyApp()
        app = HealthRouter(inner)
        app = APIKeyAuthMiddleware(app)
        app = CORSMiddleware(app)

        scope = _make_http_scope(path="/sse", headers=[])
        recorder = _Recorder()

        await app(scope, _noop_receive, recorder)

        assert recorder.status == 401
        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_full_stack_authenticated_request_passes(self, monkeypatch):
        """Authenticated request passes through entire stack."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import (
            APIKeyAuthMiddleware,
            CORSMiddleware,
            HealthRouter,
        )

        inner = _DummyApp(body=b"success")
        app = HealthRouter(inner)
        app = APIKeyAuthMiddleware(app)
        app = CORSMiddleware(app)

        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"secret"]],
        )
        recorder = _Recorder()

        await app(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200
        assert recorder.body == b"success"
        headers = recorder.response_headers
        assert headers.get("access-control-allow-origin") == "*"

    @pytest.mark.asyncio
    async def test_full_stack_preflight_bypasses_auth(self, monkeypatch):
        """CORS preflight is handled by CORS middleware before reaching auth."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import (
            APIKeyAuthMiddleware,
            CORSMiddleware,
            HealthRouter,
        )

        inner = _DummyApp()
        app = HealthRouter(inner)
        app = APIKeyAuthMiddleware(app)
        app = CORSMiddleware(app)

        scope = _make_http_scope(
            path="/sse",
            method="OPTIONS",
            headers=[[b"access-control-request-method", b"POST"]],
        )
        recorder = _Recorder()

        await app(scope, _noop_receive, recorder)

        # Preflight handled by CORS middleware, returns 204
        assert inner.called is False
        assert recorder.status == 204


# ============================================================================
# _PUBLIC_PATHS constant
# ============================================================================


@pytest.mark.unit
class TestPublicPaths:
    """Tests for the _PUBLIC_PATHS constant."""

    def test_health_in_public_paths(self):
        """Verify /health is in the public paths set."""
        from agentic_bridge.transport import _PUBLIC_PATHS

        assert "/health" in _PUBLIC_PATHS

    def test_public_paths_is_frozenset(self):
        """Public paths should be a frozenset (immutable)."""
        from agentic_bridge.transport import _PUBLIC_PATHS

        assert isinstance(_PUBLIC_PATHS, frozenset)


# ============================================================================
# _is_oauth_public_path
# ============================================================================


@pytest.mark.unit
class TestIsOAuthPublicPath:
    """Tests for _is_oauth_public_path helper."""

    def test_authorize_is_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/authorize") is True

    def test_token_is_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/token") is True

    def test_register_is_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/register") is True

    def test_revoke_is_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/revoke") is True

    def test_well_known_paths_are_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/.well-known/oauth-authorization-server") is True
        assert _is_oauth_public_path("/.well-known/oauth-protected-resource") is True

    def test_mcp_is_not_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/mcp") is False

    def test_sse_is_not_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/sse") is False

    def test_health_is_not_oauth_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/health") is False

    def test_random_path_is_not_public(self):
        from agentic_bridge.transport import _is_oauth_public_path

        assert _is_oauth_public_path("/random") is False


# ============================================================================
# OAuthCompatAuthMiddleware
# ============================================================================


@pytest.mark.unit
class TestOAuthCompatAuthMiddleware:
    """Tests for OAuthCompatAuthMiddleware ASGI middleware."""

    @pytest.mark.asyncio
    async def test_health_passes_through(self, monkeypatch):
        """Health path bypasses auth."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(path="/health", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_oauth_endpoints_pass_through(self, monkeypatch):
        """OAuth protocol endpoints bypass auth."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        for path in ["/authorize", "/token", "/register", "/revoke"]:
            inner = _DummyApp()
            mw = OAuthCompatAuthMiddleware(inner)
            scope = _make_http_scope(path=path, headers=[])
            recorder = _Recorder()

            await mw(scope, _noop_receive, recorder)

            assert inner.called is True, f"{path} should pass through"
            assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_well_known_passes_through(self, monkeypatch):
        """/.well-known/* paths bypass auth."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(path="/.well-known/oauth-authorization-server", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_mcp_with_bearer_passes_through(self, monkeypatch):
        """/mcp with Authorization: Bearer passes through to FastMCP."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/mcp",
            headers=[[b"authorization", b"Bearer some-token"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_mcp_with_api_key_converts_to_bearer(self, monkeypatch):
        """/mcp with X-API-Key gets converted to Authorization: Bearer."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-key")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/mcp",
            headers=[[b"x-api-key", b"my-key"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        # Verify the scope was modified
        forwarded_headers = dict(
            (h[0].decode() if isinstance(h[0], bytes) else h[0], h[1].decode() if isinstance(h[1], bytes) else h[1])
            for h in inner.last_scope.get("headers", [])
        )
        assert forwarded_headers.get("authorization") == "Bearer my-key"
        # X-API-Key header should be removed
        assert "x-api-key" not in forwarded_headers

    @pytest.mark.asyncio
    async def test_mcp_with_no_auth_passes_through(self, monkeypatch):
        """/mcp with no auth passes through (FastMCP will reject it)."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(path="/mcp", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        # Passes through — FastMCP's auth will handle rejection
        assert inner.called is True

    @pytest.mark.asyncio
    async def test_mcp_api_key_not_converted_when_bearer_exists(self, monkeypatch):
        """When both X-API-Key and Authorization exist, Bearer is kept."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "my-key")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/mcp",
            headers=[
                [b"x-api-key", b"my-key"],
                [b"authorization", b"Bearer existing-token"],
            ],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        # Original Authorization header should be preserved
        forwarded_headers = dict(
            (h[0].decode() if isinstance(h[0], bytes) else h[0], h[1].decode() if isinstance(h[1], bytes) else h[1])
            for h in inner.last_scope.get("headers", [])
        )
        assert forwarded_headers.get("authorization") == "Bearer existing-token"

    @pytest.mark.asyncio
    async def test_sse_with_valid_api_key_passes(self, monkeypatch):
        """/sse with valid API key passes through."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"secret"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_sse_with_invalid_key_returns_401(self, monkeypatch):
        """/sse with invalid API key returns 401."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[[b"x-api-key", b"wrong"]],
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401

    @pytest.mark.asyncio
    async def test_sse_with_no_auth_when_keys_configured_returns_401(self, monkeypatch):
        """/sse without any auth when keys are configured returns 401."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(path="/sse", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is False
        assert recorder.status == 401

    @pytest.mark.asyncio
    async def test_sse_no_keys_configured_passes_through(self, monkeypatch):
        """/sse with no API keys configured passes through (auth disabled)."""
        monkeypatch.delenv("SESSION_BRIDGE_API_KEYS", raising=False)
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(path="/sse", headers=[])
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_sse_query_string_key_passes(self, monkeypatch):
        """/sse with API key in query string passes through."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "qs-key")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = _make_http_scope(
            path="/sse",
            headers=[],
            query_string=b"api_key=qs-key",
        )
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
        assert recorder.status == 200

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self, monkeypatch):
        """Non-http/websocket scope types pass through without auth check."""
        monkeypatch.setenv("SESSION_BRIDGE_API_KEYS", "secret")
        from agentic_bridge.transport import OAuthCompatAuthMiddleware

        inner = _DummyApp()
        mw = OAuthCompatAuthMiddleware(inner)
        scope = {"type": "lifespan"}
        recorder = _Recorder()

        await mw(scope, _noop_receive, recorder)

        assert inner.called is True
