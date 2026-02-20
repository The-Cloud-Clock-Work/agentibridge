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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.get_event_loop().run_until_complete(
                run_claude("test prompt", model="sonnet")
            )

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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.get_event_loop().run_until_complete(
                run_claude("test prompt")
            )

        assert result.success is False
        assert result.exit_code == 1
        assert "Error occurred" in result.error

    def test_timeout(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.side_effect = asyncio.TimeoutError()

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.get_event_loop().run_until_complete(
                run_claude("test prompt", timeout=1)
            )

        assert result.success is False
        assert result.timed_out is True

    def test_binary_not_found(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            result = asyncio.get_event_loop().run_until_complete(
                run_claude("test prompt")
            )

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
            patch.dict("os.environ", {"CLAUDE_BINARY": "/usr/bin/claude"}),
        ):
            asyncio.get_event_loop().run_until_complete(
                run_claude("hello world", model="opus", output_format="json")
            )

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

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = asyncio.get_event_loop().run_until_complete(
                run_claude("test prompt")
            )

        assert result.success is False
        assert result.error == "Bad input"


@pytest.mark.unit
class TestRunClaudeSync:
    def test_sync_wrapper(self):
        output = json.dumps({"result": "sync ok", "session_id": "s2"})

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (output.encode(), b"")
        mock_proc.returncode = 0

        async def fake_exec(*args, **kwargs):
            return mock_proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = run_claude_sync("test", model="sonnet")

        assert result.success is True
        assert result.result == "sync ok"
