"""Tests for the dispatch bridge (HTTP transport + bridge server)."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentibridge.claude_runner import ClaudeResult, _run_claude_http, run_claude
from agentibridge.dispatch_bridge import (
    _handle_connection,
    _handle_dispatch,
    _handle_get_job,
    _handle_list_jobs,
)


# ===========================================================================
# HTTP transport tests (_run_claude_http) — submit + poll
# ===========================================================================


@pytest.mark.unit
class TestRunClaudeHttp:
    """Tests for the HTTP transport that calls the bridge from inside Docker."""

    def test_success(self):
        """Submit returns 202, poll returns completed result."""
        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"job_id": "j1", "status": "running"}
        submit_resp.text = json.dumps({"job_id": "j1", "status": "running"})

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {
            "job_id": "j1",
            "status": "completed",
            "result": {
                "success": True,
                "result": "All done",
                "session_id": "s1",
                "exit_code": 0,
                "duration_ms": 200,
                "timed_out": False,
                "error": None,
            },
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.return_value = poll_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test prompt", "sonnet", 300, "json"))

        assert result.success is True
        assert result.result == "All done"
        assert result.session_id == "s1"
        assert result.exit_code == 0

    def test_backward_compat_old_bridge(self):
        """Old bridge returns 200 with direct result (no job_id) — still works."""
        response_data = {
            "success": True,
            "result": "Direct result",
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
        assert result.result == "Direct result"
        # Should NOT have called GET (no polling needed)
        mock_client.get.assert_not_called()

    def test_auth_failure(self):
        """401 from bridge returns error ClaudeResult."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"error": "Unauthorized"}'
        mock_resp.json.return_value = {"error": "Unauthorized"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "401" in result.error

    def test_server_error(self):
        """Non-200/202 non-401 from bridge returns error with status code."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.json.return_value = {"error": "Internal Server Error"}

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

    def test_poll_failed_job(self):
        """Poll returns a failed job — error propagated to ClaudeResult."""
        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"job_id": "j2", "status": "running"}

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {
            "job_id": "j2",
            "status": "failed",
            "result": {"success": False, "error": "CLI crashed"},
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.return_value = poll_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert result.error == "CLI crashed"

    def test_poll_job_not_found(self):
        """If bridge returns 404 on poll, return error."""
        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"job_id": "j3", "status": "running"}

        poll_resp = MagicMock()
        poll_resp.status_code = 404

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.return_value = poll_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "disappeared" in result.error

    def test_no_job_id_in_202_response(self):
        """If bridge returns 202 but no job_id, return error."""
        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"status": "running"}
        submit_resp.text = '{"status": "running"}'

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert "no job_id" in result.error

    def test_poll_deadline_exceeded(self):
        """If polling exceeds deadline, return timed_out."""
        import time

        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"job_id": "j4", "status": "running"}

        # Poll always returns running — will hit deadline
        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {"job_id": "j4", "status": "running"}

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.return_value = poll_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # Make deadline already expired by mocking time.monotonic
        call_count = 0
        orig_monotonic = time.monotonic

        def fake_monotonic():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return orig_monotonic()  # first calls for deadline calc + first check
            return orig_monotonic() + 99999  # immediately expired

        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("agentibridge.claude_runner.time.monotonic", side_effect=fake_monotonic),
        ):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 5, "json"))

        assert result.success is False
        assert result.timed_out is True

    def test_poll_with_null_result(self):
        """If poll returns completed but result is None, handle gracefully."""
        submit_resp = MagicMock()
        submit_resp.status_code = 202
        submit_resp.json.return_value = {"job_id": "j5", "status": "running"}

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {
            "job_id": "j5",
            "status": "failed",
            "result": None,
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = submit_resp
        mock_client.get.return_value = poll_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = asyncio.run(_run_claude_http("http://localhost:8101", "test", "sonnet", 300, "json"))

        assert result.success is False
        assert result.error is None  # no error in empty result


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

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
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

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
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

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
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

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, b"not json"))

        status, body = self._parse_response(responses)
        assert status == 400
        assert "Invalid JSON" in body["error"]

    def test_dispatch_returns_202_with_job_id(self):
        """Successful dispatch returns 202 + job_id (fire-and-forget)."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [
                (b"x-dispatch-secret", b"real-secret"),
            ],
        )
        payload = json.dumps({"prompt": "Hello", "model": "sonnet", "timeout": 60}).encode()

        with (
            patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}),
            patch("agentibridge.dispatch_bridge._run_bridge_job", new_callable=AsyncMock),
        ):
            responses = asyncio.run(self._call_app(scope, payload))

        status, body = self._parse_response(responses)
        assert status == 202
        assert "job_id" in body
        assert body["status"] == "running"

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

        # We check that the run_claude call gets the capped timeout
        captured_kwargs = {}

        async def capture_run_bridge_job(job_id, prompt, model, timeout, output_format, resume_session_id):
            captured_kwargs["timeout"] = timeout

        with (
            patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret", "CLAUDE_DISPATCH_TIMEOUT": "600"}),
            patch("agentibridge.dispatch_bridge._run_bridge_job", side_effect=capture_run_bridge_job),
        ):
            asyncio.run(self._call_app(scope, payload))

        assert captured_kwargs["timeout"] == 600

    def test_dispatch_empty_prompt_string(self):
        """Request with empty string prompt returns 400."""
        scope = self._make_scope(
            "POST",
            "/dispatch",
            [(b"x-dispatch-secret", b"real-secret")],
        )

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
            responses = asyncio.run(self._call_app(scope, json.dumps({"prompt": ""}).encode()))

        status, body = self._parse_response(responses)
        assert status == 400
        assert "prompt" in body["error"]

    def test_non_http_scope_ignored(self):
        """Non-HTTP scope (e.g. websocket) is silently ignored."""

        async def _run():
            from agentibridge.dispatch_bridge import app

            responses = []

            async def receive():
                return {"body": b"", "more_body": False}

            async def send(message):
                responses.append(message)

            await app({"type": "websocket", "path": "/", "method": "GET", "headers": []}, receive, send)
            return responses

        responses = asyncio.run(_run())
        assert responses == []


# ===========================================================================
# Bridge fire-and-forget tests (GET /job/{id}, GET /jobs)
# ===========================================================================


@pytest.mark.unit
class TestBridgeFireAndForget:
    """Tests for the fire-and-forget job tracking endpoints."""

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

    def test_get_job_not_found(self):
        """GET /job/{unknown_id} returns 404."""
        scope = self._make_scope("GET", "/job/nonexistent-id")
        responses = asyncio.run(self._call_app(scope))
        status, body = self._parse_response(responses)
        assert status == 404
        assert "not found" in body["error"].lower()

    def test_get_job_running(self):
        """GET /job/{id} returns running job state."""
        import agentibridge.dispatch_bridge as bridge

        # Inject a job directly
        bridge._jobs["test-job-1"] = {
            "status": "running",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": None,
            "result": None,
        }

        try:
            scope = self._make_scope("GET", "/job/test-job-1")
            responses = asyncio.run(self._call_app(scope))
            status, body = self._parse_response(responses)
            assert status == 200
            assert body["job_id"] == "test-job-1"
            assert body["status"] == "running"
            assert body["result"] is None
        finally:
            bridge._jobs.pop("test-job-1", None)

    def test_get_job_completed(self):
        """GET /job/{id} returns completed job with result."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["test-job-2"] = {
            "status": "completed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:01:00+00:00",
            "result": {"success": True, "result": "Done!"},
        }

        try:
            scope = self._make_scope("GET", "/job/test-job-2")
            responses = asyncio.run(self._call_app(scope))
            status, body = self._parse_response(responses)
            assert status == 200
            assert body["status"] == "completed"
            assert body["result"]["success"] is True
            assert body["result"]["result"] == "Done!"
        finally:
            bridge._jobs.pop("test-job-2", None)

    def test_get_job_failed(self):
        """GET /job/{id} returns failed job with error."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["test-job-fail"] = {
            "status": "failed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:05+00:00",
            "result": {"success": False, "error": "CLI not found"},
        }

        try:
            scope = self._make_scope("GET", "/job/test-job-fail")
            responses = asyncio.run(self._call_app(scope))
            status, body = self._parse_response(responses)
            assert status == 200
            assert body["status"] == "failed"
            assert body["result"]["error"] == "CLI not found"
        finally:
            bridge._jobs.pop("test-job-fail", None)

    def test_list_jobs_empty(self):
        """GET /jobs returns empty list when no jobs exist."""
        import agentibridge.dispatch_bridge as bridge

        saved = dict(bridge._jobs)
        bridge._jobs.clear()

        try:
            scope = self._make_scope("GET", "/jobs")
            responses = asyncio.run(self._call_app(scope))
            status, body = self._parse_response(responses)
            assert status == 200
            assert body["count"] == 0
            assert body["jobs"] == []
        finally:
            bridge._jobs.update(saved)

    def test_list_jobs_with_entries(self):
        """GET /jobs returns all job summaries."""
        import agentibridge.dispatch_bridge as bridge

        saved = dict(bridge._jobs)
        bridge._jobs.clear()
        bridge._jobs["j1"] = {"status": "running", "started_at": "t1", "completed_at": None, "result": None}
        bridge._jobs["j2"] = {
            "status": "completed",
            "started_at": "t2",
            "completed_at": "t3",
            "result": {"success": True},
        }

        try:
            scope = self._make_scope("GET", "/jobs")
            responses = asyncio.run(self._call_app(scope))
            status, body = self._parse_response(responses)
            assert status == 200
            assert body["count"] == 2
            job_ids = {j["job_id"] for j in body["jobs"]}
            assert job_ids == {"j1", "j2"}
            # Summaries should not include full result
            for j in body["jobs"]:
                assert "result" not in j
        finally:
            bridge._jobs.clear()
            bridge._jobs.update(saved)

    def test_list_jobs_summary_fields(self):
        """GET /jobs summaries contain expected fields."""
        import agentibridge.dispatch_bridge as bridge

        saved = dict(bridge._jobs)
        bridge._jobs.clear()
        bridge._jobs["j1"] = {
            "status": "completed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:01:00+00:00",
            "result": {"success": True, "result": "big output"},
        }

        try:
            scope = self._make_scope("GET", "/jobs")
            responses = asyncio.run(self._call_app(scope))
            _, body = self._parse_response(responses)
            job = body["jobs"][0]
            assert "job_id" in job
            assert "status" in job
            assert "started_at" in job
            assert "completed_at" in job
        finally:
            bridge._jobs.clear()
            bridge._jobs.update(saved)


# ===========================================================================
# Bridge _run_bridge_job tests
# ===========================================================================


@pytest.mark.unit
class TestRunBridgeJob:
    """Tests for _run_bridge_job — the background coroutine on the bridge."""

    def test_clears_dispatch_url_env(self):
        """_run_bridge_job clears CLAUDE_DISPATCH_URL to prevent recursion."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["bj1"] = {
            "status": "running",
            "started_at": "t",
            "completed_at": None,
            "result": None,
        }
        captured_env = {}

        async def mock_run_claude(**kwargs):
            captured_env["CLAUDE_DISPATCH_URL"] = os.environ.get("CLAUDE_DISPATCH_URL", "NOT_SET")
            return ClaudeResult(success=True, result="ok", exit_code=0)

        async def _run():
            with (
                patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": "http://host.docker.internal:8101"}),
                patch("agentibridge.dispatch_bridge.run_claude", side_effect=mock_run_claude),
            ):
                await bridge._run_bridge_job("bj1", "test", "sonnet", 30, "json", None)
                # Env var should be restored after
                assert os.environ.get("CLAUDE_DISPATCH_URL") == "http://host.docker.internal:8101"

        asyncio.run(_run())

        # During run_claude, the env var should have been cleared
        assert captured_env["CLAUDE_DISPATCH_URL"] == "NOT_SET"
        # Job should be completed
        assert bridge._jobs["bj1"]["status"] == "completed"
        bridge._jobs.pop("bj1", None)

    def test_restores_env_on_error(self):
        """CLAUDE_DISPATCH_URL is restored even if run_claude raises."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["bj2"] = {
            "status": "running",
            "started_at": "t",
            "completed_at": None,
            "result": None,
        }

        async def mock_run_claude(**kwargs):
            raise RuntimeError("boom")

        async def _run():
            with (
                patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": "http://bridge:8101"}),
                patch("agentibridge.dispatch_bridge.run_claude", side_effect=mock_run_claude),
            ):
                await bridge._run_bridge_job("bj2", "test", "sonnet", 30, "json", None)
                assert os.environ.get("CLAUDE_DISPATCH_URL") == "http://bridge:8101"

        asyncio.run(_run())
        assert bridge._jobs["bj2"]["status"] == "failed"
        assert "boom" in bridge._jobs["bj2"]["result"]["error"]
        bridge._jobs.pop("bj2", None)

    def test_failed_result_sets_failed_status(self):
        """A non-success ClaudeResult sets job status to 'failed'."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["bj3"] = {
            "status": "running",
            "started_at": "t",
            "completed_at": None,
            "result": None,
        }

        mock_result = ClaudeResult(success=False, error="CLI error", exit_code=1)

        async def _run():
            with patch("agentibridge.dispatch_bridge.run_claude", new_callable=AsyncMock, return_value=mock_result):
                await bridge._run_bridge_job("bj3", "test", "sonnet", 30, "json", None)

        asyncio.run(_run())
        assert bridge._jobs["bj3"]["status"] == "failed"
        assert bridge._jobs["bj3"]["result"]["error"] == "CLI error"
        bridge._jobs.pop("bj3", None)

    def test_no_dispatch_url_env_no_crash(self):
        """If CLAUDE_DISPATCH_URL is not set at all, no crash on pop."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["bj4"] = {
            "status": "running",
            "started_at": "t",
            "completed_at": None,
            "result": None,
        }
        mock_result = ClaudeResult(success=True, result="ok", exit_code=0)

        async def _run():
            env = dict(os.environ)
            env.pop("CLAUDE_DISPATCH_URL", None)
            with (
                patch.dict("os.environ", env, clear=True),
                patch("agentibridge.dispatch_bridge.run_claude", new_callable=AsyncMock, return_value=mock_result),
            ):
                await bridge._run_bridge_job("bj4", "test", "sonnet", 30, "json", None)

        asyncio.run(_run())
        assert bridge._jobs["bj4"]["status"] == "completed"
        bridge._jobs.pop("bj4", None)


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
            asyncio.run(_run_claude_http("http://localhost:8101", "ping", "sonnet", 300, "json", "abc123"))

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
        """ASGI app accepts resume_session_id and returns 202."""
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/dispatch",
            "headers": [(b"x-dispatch-secret", b"real-secret")],
        }
        payload = json.dumps(
            {
                "prompt": "ping",
                "model": "sonnet",
                "resume_session_id": "sess-abc",
            }
        ).encode()

        captured = {}

        async def capture_bridge_job(job_id, prompt, model, max_seconds, output_format, resume_session_id):
            captured["resume_session_id"] = resume_session_id

        async def run():
            from agentibridge.dispatch_bridge import app

            responses = []

            async def receive():
                return {"body": payload, "more_body": False}

            async def send(message):
                responses.append(message)

            with (
                patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}),
                patch("agentibridge.dispatch_bridge._run_bridge_job", side_effect=capture_bridge_job),
            ):
                await app(scope, receive, send)
                return responses

        responses = asyncio.run(run())
        status = responses[0]["status"]
        body = json.loads(responses[1]["body"])
        assert status == 202
        assert "job_id" in body
        assert body["status"] == "running"
        assert captured["resume_session_id"] == "sess-abc"


# ===========================================================================
# Bridge main() tests
# ===========================================================================


@pytest.mark.unit
class TestDispatchBridgeMain:
    """Tests for the bridge entrypoint."""

    def test_refuses_to_start_without_secret(self):
        """main() exits with error if DISPATCH_SECRET is not set."""
        from agentibridge.dispatch_bridge import main

        with (
            patch.dict("os.environ", {"DISPATCH_SECRET": ""}, clear=False),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1


# ===========================================================================
# Bridge helper function tests
# ===========================================================================


@pytest.mark.unit
class TestBridgeHelpers:
    """Tests for bridge helper/utility functions."""

    def test_parse_headers(self):
        """_parse_headers parses raw HTTP headers into lowercase dict."""
        from agentibridge.dispatch_bridge import _parse_headers

        raw = "Content-Type: application/json\r\nX-Custom: value123"
        headers = _parse_headers(raw)
        assert headers["content-type"] == "application/json"
        assert headers["x-custom"] == "value123"

    def test_parse_headers_empty(self):
        """_parse_headers handles empty string."""
        from agentibridge.dispatch_bridge import _parse_headers

        assert _parse_headers("") == {}

    def test_get_header(self):
        """_get_header extracts header from ASGI scope."""
        from agentibridge.dispatch_bridge import _get_header

        scope = {"headers": [(b"x-api-key", b"secret123"), (b"content-type", b"application/json")]}
        assert _get_header(scope, b"x-api-key") == "secret123"
        assert _get_header(scope, b"X-Api-Key") == "secret123"  # case insensitive
        assert _get_header(scope, b"missing") == ""


# ===========================================================================
# Raw asyncio HTTP handler tests (_handle_connection, _handle_dispatch, etc.)
# ===========================================================================


def _make_reader(data: bytes) -> AsyncMock:
    """Create a mock StreamReader that returns data then EOF."""
    reader = AsyncMock(spec=asyncio.StreamReader)
    chunks = [data, b""]
    reader.read = AsyncMock(side_effect=chunks)
    return reader


def _make_writer() -> MagicMock:
    """Create a mock StreamWriter that captures written data."""
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def _build_http_request(method: str, path: str, headers: dict | None = None, body: bytes = b"") -> bytes:
    """Build a raw HTTP/1.1 request."""
    hdrs = headers or {}
    if body:
        hdrs["Content-Length"] = str(len(body))
    header_lines = [f"{method} {path} HTTP/1.1"]
    for k, v in hdrs.items():
        header_lines.append(f"{k}: {v}")
    return ("\r\n".join(header_lines) + "\r\n\r\n").encode() + body


def _parse_http_response(writer: MagicMock) -> tuple[int, dict]:
    """Parse the HTTP response written to a mock writer."""
    raw = b""
    for call in writer.write.call_args_list:
        raw += call[0][0]
    # Split headers from body
    header_end = raw.index(b"\r\n\r\n")
    header_text = raw[:header_end].decode()
    body = raw[header_end + 4 :]
    status = int(header_text.split(" ")[1])
    return status, json.loads(body)


@pytest.mark.unit
class TestRawAsyncioHandler:
    """Tests for the raw asyncio HTTP connection handler."""

    def test_health_endpoint(self):
        """GET /health via raw asyncio returns 200."""
        request = _build_http_request("GET", "/health")
        reader = _make_reader(request)
        writer = _make_writer()

        asyncio.run(_handle_connection(reader, writer))

        status, body = _parse_http_response(writer)
        assert status == 200
        assert body["status"] == "ok"

    def test_not_found(self):
        """Unknown path returns 404."""
        request = _build_http_request("GET", "/unknown")
        reader = _make_reader(request)
        writer = _make_writer()

        asyncio.run(_handle_connection(reader, writer))

        status, body = _parse_http_response(writer)
        assert status == 404

    def test_bad_request_line(self):
        """Malformed request line returns 400."""
        reader = _make_reader(b"INVALID\r\n\r\n")
        writer = _make_writer()

        asyncio.run(_handle_connection(reader, writer))

        status, body = _parse_http_response(writer)
        assert status == 400

    def test_empty_connection_closes(self):
        """Empty read (EOF) closes writer without response."""
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.read = AsyncMock(return_value=b"")
        writer = _make_writer()

        asyncio.run(_handle_connection(reader, writer))

        writer.close.assert_called_once()
        writer.write.assert_not_called()


@pytest.mark.unit
class TestRawDispatchHandler:
    """Tests for _handle_dispatch via raw asyncio HTTP handler."""

    def test_auth_failure(self):
        """Missing secret returns 401."""
        headers = {"content-length": "0"}
        writer = _make_writer()

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
            asyncio.run(_handle_dispatch(headers, b"{}", writer))

        status, body = _parse_http_response(writer)
        assert status == 401

    def test_invalid_json(self):
        """Invalid JSON body returns 400."""
        headers = {"x-dispatch-secret": "real-secret"}
        writer = _make_writer()

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
            asyncio.run(_handle_dispatch(headers, b"not json", writer))

        status, body = _parse_http_response(writer)
        assert status == 400
        assert "Invalid JSON" in body["error"]

    def test_missing_prompt(self):
        """Missing prompt returns 400."""
        headers = {"x-dispatch-secret": "real-secret"}
        writer = _make_writer()
        body = json.dumps({"model": "sonnet"}).encode()

        with patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}):
            asyncio.run(_handle_dispatch(headers, body, writer))

        status, resp = _parse_http_response(writer)
        assert status == 400
        assert "prompt" in resp["error"]

    def test_successful_dispatch_returns_202(self):
        """Valid dispatch returns 202 with job_id."""
        headers = {"x-dispatch-secret": "real-secret"}
        writer = _make_writer()
        body = json.dumps({"prompt": "Hello", "timeout": 60}).encode()

        with (
            patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret"}),
            patch("agentibridge.dispatch_bridge._run_bridge_job", new_callable=AsyncMock),
        ):
            asyncio.run(_handle_dispatch(headers, body, writer))

        status, resp = _parse_http_response(writer)
        assert status == 202
        assert "job_id" in resp

    def test_timeout_capped(self):
        """Timeout is capped to CLAUDE_DISPATCH_TIMEOUT."""
        headers = {"x-dispatch-secret": "real-secret"}
        writer = _make_writer()
        body = json.dumps({"prompt": "Hello", "timeout": 9999}).encode()

        captured = {}

        async def capture_bridge_job(job_id, prompt, model, max_seconds, output_format, resume_session_id):
            captured["max_seconds"] = max_seconds

        with (
            patch.dict("os.environ", {"DISPATCH_SECRET": "real-secret", "CLAUDE_DISPATCH_TIMEOUT": "600"}),
            patch("agentibridge.dispatch_bridge._run_bridge_job", side_effect=capture_bridge_job),
        ):
            asyncio.run(_handle_dispatch(headers, body, writer))

        assert captured["max_seconds"] == 600


@pytest.mark.unit
class TestRawGetJob:
    """Tests for _handle_get_job via raw asyncio handler."""

    def test_job_found(self):
        """Returns 200 with job data when job exists."""
        import agentibridge.dispatch_bridge as bridge

        bridge._jobs["raw-j1"] = {"status": "running", "started_at": "t", "completed_at": None, "result": None}
        writer = _make_writer()

        try:
            asyncio.run(_handle_get_job("raw-j1", writer))
            status, body = _parse_http_response(writer)
            assert status == 200
            assert body["job_id"] == "raw-j1"
        finally:
            bridge._jobs.pop("raw-j1", None)

    def test_job_not_found(self):
        """Returns 404 when job doesn't exist."""
        writer = _make_writer()
        asyncio.run(_handle_get_job("nonexistent", writer))
        status, body = _parse_http_response(writer)
        assert status == 404


@pytest.mark.unit
class TestRawListJobs:
    """Tests for _handle_list_jobs via raw asyncio handler."""

    def test_empty_list(self):
        """Returns empty list when no jobs."""
        import agentibridge.dispatch_bridge as bridge

        saved = dict(bridge._jobs)
        bridge._jobs.clear()
        writer = _make_writer()

        try:
            asyncio.run(_handle_list_jobs(writer))
            status, body = _parse_http_response(writer)
            assert status == 200
            assert body["count"] == 0
        finally:
            bridge._jobs.update(saved)

    def test_list_with_jobs(self):
        """Returns job summaries."""
        import agentibridge.dispatch_bridge as bridge

        saved = dict(bridge._jobs)
        bridge._jobs.clear()
        bridge._jobs["rj1"] = {"status": "running", "started_at": "t1", "completed_at": None, "result": None}
        bridge._jobs["rj2"] = {"status": "completed", "started_at": "t2", "completed_at": "t3", "result": {}}
        writer = _make_writer()

        try:
            asyncio.run(_handle_list_jobs(writer))
            status, body = _parse_http_response(writer)
            assert status == 200
            assert body["count"] == 2
        finally:
            bridge._jobs.clear()
            bridge._jobs.update(saved)
