"""Tests for agentibridge.claude_runner module."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agentibridge.claude_runner import (
    ClaudeResult,
    parse_claude_output,
    run_claude,
    run_claude_sync,
)


@pytest.mark.unit
class TestClaudeResult:
    def test_to_dict(self):
        r = ClaudeResult(success=True, result="hello", exit_code=0, duration_ms=100)
        d = r.to_dict()
        assert d["success"] is True
        assert d["result"] == "hello"
        assert d["exit_code"] == 0

    def test_defaults(self):
        r = ClaudeResult(success=False)
        assert r.result is None
        assert r.session_id is None
        assert r.timed_out is False
        assert r.error is None


@pytest.mark.unit
class TestParseClaudeOutput:
    def test_valid_json(self):
        raw = json.dumps({"result": "Done", "session_id": "abc-123", "duration_ms": 500})
        parsed = parse_claude_output(raw)
        assert parsed["result"] == "Done"
        assert parsed["session_id"] == "abc-123"
        assert parsed["duration_ms"] == 500

    def test_invalid_json(self):
        parsed = parse_claude_output("not json at all")
        assert parsed["result"] == "not json at all"
        assert parsed["parse_error"] is True

    def test_empty_string(self):
        parsed = parse_claude_output("")
        assert parsed["parse_error"] is True

    def test_error_output(self):
        raw = json.dumps({"result": "Something went wrong", "is_error": True})
        parsed = parse_claude_output(raw)
        assert parsed["is_error"] is True
        assert parsed["result"] == "Something went wrong"


@pytest.mark.unit
class TestRunClaude:
    def test_successful_run(self):
        output = json.dumps({"result": "All done", "session_id": "s1", "duration_ms": 200})

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt", model="sonnet"))

        assert result.success is True
        assert result.result == "All done"
        assert result.session_id == "s1"
        assert result.exit_code == 0

    def test_non_zero_exit(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"Error occurred")
        mock_proc.returncode = 1

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is False
        assert result.exit_code == 1
        assert "Error occurred" in result.error

    def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt", timeout=1))

        assert result.success is False
        assert result.timed_out is True

    def test_binary_not_found(self):
        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is False
        assert "not found" in result.error

    def test_command_construction(self):
        output = json.dumps({"result": "ok"})

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        calls = []

        async def fake_exec(*args, **kwargs):
            calls.append((args, kwargs))
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_BINARY": "/usr/bin/claude", "CLAUDE_DISPATCH_URL": ""}),
        ):
            asyncio.run(run_claude("hello world", model="opus", output_format="json"))

        cmd_args = calls[0][0]
        assert cmd_args[0] == "/usr/bin/claude"
        assert "--model" in cmd_args
        assert "opus" in cmd_args
        assert "--output-format" in cmd_args
        assert "json" in cmd_args
        assert "-p" in cmd_args
        assert "hello world" in cmd_args

    def test_is_error_flag(self):
        output = json.dumps({"result": "Bad input", "is_error": True})

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is False
        assert result.error == "Bad input"

    def test_docker_without_dispatch_url(self):
        with (
            patch("agentibridge.claude_runner._is_docker", return_value=True),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is False
        assert "CLAUDE_DISPATCH_URL" in result.error
        assert "agentibridge bridge start" in result.error

    def test_docker_with_dispatch_url_routes_to_http(self):
        """When inside Docker with CLAUDE_DISPATCH_URL set, should route to HTTP bridge."""
        mock_result = ClaudeResult(success=True, result="bridge ok")

        with (
            patch("agentibridge.claude_runner._is_docker", return_value=True),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": "http://host.docker.internal:8101"}),
            patch("agentibridge.claude_runner._run_claude_http", return_value=mock_result) as mock_http,
        ):
            result = asyncio.run(run_claude("test prompt"))

        assert result.success is True
        assert result.result == "bridge ok"
        mock_http.assert_called_once()


@pytest.mark.unit
class TestRunClaudeSync:
    def test_sync_wrapper(self):
        output = json.dumps({"result": "sync ok", "session_id": "s2"})

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=fake_exec),
            patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}),
        ):
            result = run_claude_sync("test", model="sonnet")

        assert result.success is True
        assert result.result == "sync ok"


# ---------------------------------------------------------------------------
# Plan mode parameter tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunClaudePlanMode:
    """Tests for allowed_tools, max_turns, permission_mode parameters."""

    def test_allowed_tools_in_command(self):
        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b'{"result": "plan output"}', b""))
                mock_exec.return_value = mock_proc

                with patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False):
                    await run_claude(
                        "create a plan",
                        allowed_tools="Read,Glob,Grep",
                        output_format="text",
                    )

                cmd = mock_exec.call_args[0]
                assert "--allowedTools" in cmd
                assert "Read,Glob,Grep" in cmd

        asyncio.run(_run())

    def test_max_turns_in_command(self):
        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
                mock_exec.return_value = mock_proc

                with patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False):
                    await run_claude("plan it", max_turns=15)

                cmd = mock_exec.call_args[0]
                assert "--max-turns" in cmd
                assert "15" in cmd

        asyncio.run(_run())

    def test_permission_mode_replaces_dangerously_skip(self):
        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
                mock_exec.return_value = mock_proc

                with patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False):
                    await run_claude("plan it", permission_mode="bypassPermissions")

                cmd = mock_exec.call_args[0]
                assert "--permission-mode" in cmd
                assert "bypassPermissions" in cmd
                assert "--dangerously-skip-permissions" not in cmd

        asyncio.run(_run())

    def test_default_uses_dangerously_skip(self):
        async def _run():
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = 0
                mock_proc.communicate = AsyncMock(return_value=(b'{"result": "ok"}', b""))
                mock_exec.return_value = mock_proc

                with patch.dict("os.environ", {"CLAUDE_DISPATCH_URL": ""}, clear=False):
                    await run_claude("normal dispatch")

                cmd = mock_exec.call_args[0]
                assert "--dangerously-skip-permissions" in cmd
                assert "--permission-mode" not in cmd

        asyncio.run(_run())
