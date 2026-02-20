"""Tests for agentibridge.cli module."""

from unittest.mock import patch, MagicMock

import pytest

from agentibridge.cli import (
    main,
    cmd_version,
    cmd_help,
    cmd_connect,
    cmd_config,
    cmd_tunnel,
    _extract_tunnel_url,
)


@pytest.mark.unit
class TestCmdVersion:
    def test_prints_version(self, capsys):
        args = MagicMock()
        cmd_version(args)
        output = capsys.readouterr().out
        assert "agentibridge" in output
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
        assert "AGENTIBRIDGE_TRANSPORT" in output
        assert "LLM_API_BASE" in output
        assert "CLOUDFLARE_TUNNEL_TOKEN" in output


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
        assert "AGENTIBRIDGE_TRANSPORT" in output
        assert "AGENTIBRIDGE_PORT" in output

    def test_generate_env(self, capsys):
        args = MagicMock()
        args.generate_env = True
        cmd_config(args)
        output = capsys.readouterr().out
        assert "AGENTIBRIDGE_TRANSPORT" in output
        assert "REDIS_URL" in output
        assert "LLM_API_BASE" in output


@pytest.mark.unit
class TestMain:
    def test_no_args_prints_help(self, capsys):
        with patch("sys.argv", ["agentibridge"]):
            main()
        output = capsys.readouterr().out
        assert "agentibridge" in output.lower() or "usage" in output.lower()

    def test_version_command(self, capsys):
        with patch("sys.argv", ["agentibridge", "version"]):
            main()
        output = capsys.readouterr().out
        assert "0.2.0" in output


@pytest.mark.unit
class TestExtractTunnelUrl:
    def test_extracts_quick_tunnel_url(self):
        logs = (
            "2024-01-01 INFO Starting quick tunnel...\n"
            "2024-01-01 INFO +----------------------------+\n"
            "2024-01-01 INFO | https://foo-bar-baz.trycloudflare.com |\n"
            "2024-01-01 INFO +----------------------------+\n"
        )
        assert _extract_tunnel_url(logs) == "https://foo-bar-baz.trycloudflare.com"

    def test_returns_none_for_no_url(self):
        assert _extract_tunnel_url("Starting named tunnel...\nConnected.") is None

    def test_returns_none_for_empty(self):
        assert _extract_tunnel_url("") is None


@pytest.mark.unit
class TestCmdTunnel:
    def test_tunnel_no_docker(self, capsys):
        with patch("shutil.which", return_value=None):
            args = MagicMock()
            cmd_tunnel(args)
        output = capsys.readouterr().out
        assert "Docker is not installed" in output

    def test_tunnel_not_running(self, capsys):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            with patch("agentibridge.cli.subprocess.run", return_value=mock_result):
                args = MagicMock()
                cmd_tunnel(args)
        output = capsys.readouterr().out
        assert "not running" in output
        assert "docker compose --profile tunnel up -d" in output

    def test_tunnel_quick_url_detected(self, capsys):
        inspect_result = MagicMock()
        inspect_result.returncode = 0
        inspect_result.stdout = "running"

        log_result = MagicMock()
        log_result.returncode = 0
        log_result.stdout = ""
        log_result.stderr = (
            "INF +-------------------------------------------+\n"
            "INF | https://my-test-tunnel.trycloudflare.com  |\n"
            "INF +-------------------------------------------+\n"
        )

        def side_effect(cmd, **kwargs):
            if "inspect" in cmd:
                return inspect_result
            return log_result

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("agentibridge.cli.subprocess.run", side_effect=side_effect):
                args = MagicMock()
                cmd_tunnel(args)
        output = capsys.readouterr().out
        assert "https://my-test-tunnel.trycloudflare.com" in output
        assert "quick tunnel" in output
        assert "/sse" in output

    def test_tunnel_named_connected(self, capsys):
        inspect_result = MagicMock()
        inspect_result.returncode = 0
        inspect_result.stdout = "running"

        log_result = MagicMock()
        log_result.returncode = 0
        log_result.stdout = "Starting named tunnel...\nConnection registered."
        log_result.stderr = ""

        def side_effect(cmd, **kwargs):
            if "inspect" in cmd:
                return inspect_result
            return log_result

        with patch("shutil.which", return_value="/usr/bin/docker"):
            with patch("agentibridge.cli.subprocess.run", side_effect=side_effect):
                args = MagicMock()
                cmd_tunnel(args)
        output = capsys.readouterr().out
        assert "named tunnel" in output
