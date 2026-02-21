"""Tests for the dispatch bridge (HTTP transport + bridge server)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentibridge.claude_runner import ClaudeResult, _run_claude_http, run_claude


# ===========================================================================
# HTTP transport tests (_run_claude_http)
# ===========================================================================


@pytest.mark.unit
class TestRunClaudeHttp:
    """Tests for the HTTP transport that calls the bridge from inside Docker."""

    def test_success(self):
        """Successful dispatch returns a proper ClaudeResult."""

        response_data = {
            "success": True,
            "result": "All done",
            "session_id": "s1",
            "exit_code": 0,
            "duration_ms": 200,
            "timed_out": False,
            "error": None,
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_data
        mock_resp.text = json.dumps(response_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test prompt", "sonnet", 300, "json"))

        assert result.success is True
        assert result.result == "All done"
        assert result.session_id == "s1"
        assert result.exit_code == 0

    def test_auth_failure(self):
        """401 from bridge returns error ClaudeResult."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "401" in result.error

    def test_server_error(self):
        """Non-200 non-401 from bridge returns error with status code."""
        mock_resp = AsyncMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "500" in result.error

    def test_connection_error(self):
        """Connection failure returns error ClaudeResult."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "Cannot connect" in result.error

    def test_timeout(self):
        """HTTP timeout returns timed_out ClaudeResult."""
        import httpx

        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.TimeoutException("Read timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert result.timed_out is True

    def test_secret_header_sent(self):
        """The X-Dispatch-Secret header is sent with the configured secret."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": "ok"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"DISPATCH_SECRET": "my-secret-123"}),
        ):
            asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["headers"]["X-Dispatch-Secret"] == "my-secret-123"

    def test_url_construction(self):
        """URL is built correctly with /dispatch path, trailing slash stripped."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": "ok"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            asyncio.run(_run_claude_http("http://localhost:8101/", "test", "sonnet", 300, "json"))

        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8101/dispatch"


# ===========================================================================
# Routing logic tests (run_claude dispatches based on env)
# ===========================================================================


@pytest.mark.unit
class TestRunClaudeRouting:
    """Tests that run_claude routes to HTTP or local based on CLAUDE_DISPATCH_URL."""

    def test_routes_to_http_when_url_set(self):
        """When CLAUDE_DISPATCH_URL is set, run_claude calls _run_claude_http."""
        mock_http = AsyncMock(return_value=ClaudeResult(success=True, result="via http"))

        with (
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": "http://bridge:8101"}),
            patch("agentibridge.claude_runner._run_claude_http", mock_http),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is True
        assert result.result == "via http"
        mock_http.assert_called_once()

    def test_routes_to_local_when_url_empty(self):
        """When CLAUDE_DISPATCH_URL is empty, run_claude uses local subprocess."""
        output = json.dumps({"result": "local ok"})
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is True
        assert result.result == "local ok"


# ===========================================================================
# Bridge server tests (dispatch_bridge.py ASGI app)
# ===========================================================================


@pytest.mark.unit
class TestDispatchBridgeApp:
    """Tests for the ASGI bridge app."""

    def _make_scope(self, method: str, path: str, headers: list[tuple[bytes, bytes]] | None = None):
        return {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
        }

    async def _call_app(self, scope, body: bytes = b""):
        from agentibridge.dispatch_bridge import app

        responses = []

        async def receive():
            return {"body": body, "more_body": False}

        async def send(message):
            responses.append(message)

        await app(scope, receive, send)
        return responses

    def _parse_response(self, responses):
        status = responses[0]["status"]
        body = json.loads(responses[1]["body"])
        return status, body

    def test_health_endpoint(self):
        scope = self._make_scope("GET", "/health")
        responses = asyncio.run(self._call_app(scope))
        status, body = self._parse_response(responses)
        assert status == 200
        assert body["status"] == "ok"

    def test_not_found(self):
        scope = self._make_scope("GET", "/nonexistent")
        responses = asyncio.run(self._call_app(scope))
        status, body = self._parse_response(responses)
        assert status == 404

    def test_dispatch_missing_secret(self):
        """Request without X-Dispatch-Secret returns 401."""
        scope = self._make_scope("POST", "/dispatch")

        with patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, json.dumps({"prompt": "hi"}).encode()))

        status, body = self._parse_response(responses)
        assert status == 401
        assert "Unauthorized" in body["error"]

    def test_dispatch_wrong_secret(self):
        """Request with wrong secret returns 401."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"wrong-secret"),
            ],
        )

        with patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, json.dumps({"prompt": "hi"}).encode()))

        status, body = self._parse_response(responses)
        assert status == 401

    def test_dispatch_missing_prompt(self):
        """Request without prompt returns 400."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"real-secret"),
            ],
        )

        with patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, json.dumps({"model": "sonnet"}).encode()))

        status, body = self._parse_response(responses)
        assert status == 400
        assert "prompt" in body["error"]

    def test_dispatch_invalid_json(self):
        """Request with invalid JSON body returns 400."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"real-secret"),
            ],
        )

        with patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, b"not json"))

        status, body = self._parse_response(responses)
        assert status == 400
        assert "Invalid JSON" in body["error"]

    def test_dispatch_success(self):
        """Successful dispatch returns ClaudeResult JSON."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"real-secret"),
            ],
        )
        payload = json.dumps({"prompt": "Hello", "model": "sonnet", "timeout": 60}).encode()

        mock_result = ClaudeResult(
            success=True,
            result="Done!",
            session_id="s1",
            exit_code=0,
            duration_ms=100,
        )

        with (
            patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}),
            patch("agentibridge.dispatch_bridge.run_claude", new_callable=AsyncMock, return_value=mock_result),
        ):
            responses = asyncio.run(self._call_app(scope, payload))

        status, body = self._parse_response(responses)
        assert status == 200
        assert body["success"] is True
        assert body["result"] == "Done!"

    def test_dispatch_timeout_capped(self):
        """Timeout is capped at CLAUDE_DISPATCH_TIMEOUT."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"real-secret"),
            ],
        )
        # Request a timeout of 9999, which should be capped to 600
        payload = json.dumps({"prompt": "Hello", "timeout": 9999}).encode()

        mock_result = ClaudeResult(success=True, result="ok", exit_code=0)

        with (
            patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret", "CLAUDE_DISPATCH_TIMEOUT": "600"}),
            patch(
                "agentibridge.dispatch_bridge.run_claude", new_callable=AsyncMock, return_value=mock_result
            ) as mock_run,
        ):
            asyncio.run(self._call_app(scope, payload))

        # Verify timeout was capped
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 600


# ===========================================================================
# Resume session tests
# ===========================================================================


@pytest.mark.unit
class TestResumeSession:
    """Tests for the --resume flag path through the dispatch stack."""

    def test_run_claude_http_sends_resume_session_id(self):
        """_run_claude_http includes resume_session_id in the POST body."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "result": "resumed"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            asyncio.run(
                _run_claude_http("http://localhost:8101", "ping", "sonnet", 300, "json", "abc123")
            )

        call_kwargs = mock_client.post.call_args[1]
        assert call_kwargs["json"]["resume_session_id"] == "abc123"

    def test_run_claude_local_uses_resume_flag(self):
        """Local subprocess uses --resume <id> --print instead of -p when resume_session_id set."""
        output = json.dumps({"result": "resumed ok", "session_id": "abc123"})
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with (
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            result = asyncio.run(run_claude("ping", resume_session_id="abc123"))

        assert result.success is True
        assert "--resume" in captured_cmd
        idx = captured_cmd.index("--resume")
        assert captured_cmd[idx + 1] == "abc123"
        assert "--print" in captured_cmd
        assert "-p" not in captured_cmd

    def test_run_claude_local_no_resume_flag_when_not_set(self):
        """Without resume_session_id, local subprocess uses -p flag."""
        output = json.dumps({"result": "ok"})
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        captured_cmd = []

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with (
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False),
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
        ):
            asyncio.run(run_claude("ping"))

        assert "-p" in captured_cmd
        assert "--resume" not in captured_cmd

    def test_run_claude_routes_resume_to_http(self):
        """run_claude forwards resume_session_id to _run_claude_http when URL is set."""
        mock_http = AsyncMock(return_value=ClaudeResult(success=True, result="resumed via http"))

        with (
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": "http://bridge:8101"}),
            patch("agentibridge.claude_runner._run_claude_http", mock_http),
        ):
            asyncio.run(run_claude("ping", resume_session_id="session-xyz"))

        call_kwargs = mock_http.call_args[1] if mock_http.call_args[1] else {}
        call_args = mock_http.call_args[0] if mock_http.call_args[0] else ()
        # resume_session_id is the last positional arg or in kwargs
        assert "session-xyz" in list(call_args) or call_kwargs.get("resume_session_id") == "session-xyz"

    def test_bridge_app_passes_resume_session_id(self):
        """ASGI app extracts resume_session_id from body and passes to run_claude."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/dispatch",
            "headers": [(b"x-dispatch-secret", b"real-secret")],
        }
        payload = json.dumps({
            "prompt": "ping",
            "model": "sonnet",
            "resume_session_id": "sess-abc",
        }).encode()

        mock_result = ClaudeResult(success=True, result="pong", session_id="sess-abc", exit_code=0)

        async def run():
            from agentibridge.dispatch_bridge import app

            responses = []

            async def receive():
                return {"body": payload, "more_body": False}

            async def send(message):
                responses.append(message)

            with (
                patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": "real-secret"}),
                patch(
                    "agentibridge.dispatch_bridge.run_claude",
                    new_callable=AsyncMock,
                    return_value=mock_result,
                ) as mock_run,
            ):
                await app(scope, receive, send)
                return responses, mock_run

        responses, mock_run = asyncio.run(run())
        status = responses[0]["status"]
        assert status == 200
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs.get("resume_session_id") == "sess-abc"


# ===========================================================================
# Bridge main() tests
# ===========================================================================


@pytest.mark.unit
class TestDispatchBridgeMain:
    """Tests for the bridge entrypoint."""

    def test_refuses_to_start_without_secret(self):
        """main() exits with error if DISPATCH_BRIDGE_SECRET is not set."""
        from agentibridge.dispatch_bridge import main

        with (
            patch.dict("os.environ", {"DISPATCH_BRIDGE_SECRET": ""}, clear=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
