"""Tests for agentic_bridge.cli module."""

from unittest.mock import patch, MagicMock

import pytest

from agentic_bridge.cli import main, cmd_version, cmd_help, cmd_connect, cmd_config


@pytest.mark.unit
class TestCmdVersion:
    def test_prints_version(self, capsys):
        args = MagicMock()
        cmd_version(args)
        output = capsys.readouterr().out
        assert "agentic-bridge" in output
        assert "0.2.0" in output


@pytest.mark.unit
class TestCmdHelp:
    def test_shows_tools(self, capsys):
        args = MagicMock()
        cmd_help(args)
        output = capsys.readouterr().out
        assert "list_sessions" in output
        assert "get_session" in output
        assert "search_semantic" in output
        assert "dispatch_task" in output
        assert "CONFIGURATION" in output

    def test_shows_env_vars(self, capsys):
        args = MagicMock()
        cmd_help(args)
        output = capsys.readouterr().out
        assert "REDIS_URL" in output
        assert "SESSION_BRIDGE_TRANSPORT" in output
        assert "EMBEDDING_BACKEND" in output


@pytest.mark.unit
class TestCmdConnect:
    def test_default_connection_strings(self, capsys):
        args = MagicMock()
        args.host = None
        args.port = None
        args.api_key = None
        cmd_connect(args)
        output = capsys.readouterr().out
        assert "Claude Code CLI" in output
        assert "ChatGPT" in output
        assert "localhost:8100" in output
        assert "/sse" in output
        assert "/health" in output

    def test_custom_host_port(self, capsys):
        args = MagicMock()
        args.host = "myserver.com"
        args.port = "9000"
        args.api_key = "secret-key"
        cmd_connect(args)
        output = capsys.readouterr().out
        assert "myserver.com:9000" in output
        assert "secret-key" in output


@pytest.mark.unit
class TestCmdConfig:
    def test_shows_config(self, capsys):
        args = MagicMock()
        args.generate_env = False
        cmd_config(args)
        output = capsys.readouterr().out
        assert "SESSION_BRIDGE_TRANSPORT" in output
        assert "SESSION_BRIDGE_PORT" in output

    def test_generate_env(self, capsys):
        args = MagicMock()
        args.generate_env = True
        cmd_config(args)
        output = capsys.readouterr().out
        assert "SESSION_BRIDGE_TRANSPORT" in output
        assert "REDIS_URL" in output
        assert "EMBEDDING_BACKEND" in output


@pytest.mark.unit
class TestMain:
    def test_no_args_prints_help(self, capsys):
        with patch("sys.argv", ["agentic-bridge"]):
            main()
        output = capsys.readouterr().out
        assert "agentic-bridge" in output.lower() or "usage" in output.lower()

    def test_version_command(self, capsys):
        with patch("sys.argv", ["agentic-bridge", "version"]):
            main()
        output = capsys.readouterr().out
        assert "0.2.0" in output
