"""Tests for agentibridge.cli module."""

import re
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentibridge import __version__
from agentibridge.cli import (
    main,
    cmd_version,
    cmd_help,
    cmd_connect,
    cmd_config,
    cmd_status,
    cmd_tunnel,
    cmd_update,
    cmd_run,
    cmd_bridge,
    _container_health,
    _systemd_active,
    _cloudflared_hostname,
    _parse_cloudflared_config,
    _extract_tunnel_url,
    _short_digest,
    _validate_env,
    _ensure_stack_dir,
    _maybe_start_bridge,
    _read_env_value,
    _cmd_run_test,
)


@pytest.mark.unit
class TestCmdVersion:
    def test_prints_version(self, capsys):
        args = MagicMock()
        cmd_version(args)
        output = capsys.readouterr().out
        assert "agentibridge" in output
        assert re.search(r"\d+\.\d+\.\d+", output)


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
        import os

        env = os.environ.copy()
        env.pop("AGENTIBRIDGE_HOST", None)
        env.pop("AGENTIBRIDGE_PORT", None)
        with patch.dict("os.environ", env, clear=True):
            cmd_connect(args)
        output = capsys.readouterr().out
        assert "Claude Code CLI" in output
        assert "ChatGPT" in output
        assert "localhost:8100" in output
        assert '"type": "http"' in output
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
class TestCmdStatus:
    """Tests for `agentibridge status`."""

    def _docker_inspect_side_effect(self, container_health: dict):
        """Return a side_effect that handles docker inspect + systemctl calls."""

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            # docker inspect for container health
            if "docker" in cmd_str and "inspect" in cmd_str:
                for name, status in container_health.items():
                    if name in cmd_str:
                        if status is None:
                            return _fail()
                        return _ok(stdout=status)
                return _fail()
            # systemctl --user is-active agentibridge
            if "systemctl" in cmd_str and "--user" in cmd_str:
                return _ok(stdout="inactive")
            # systemctl is-active cloudflared
            if "systemctl" in cmd_str and "cloudflared" in cmd_str:
                return _ok(stdout="inactive")
            return _fail()

        return side_effect

    def test_status_docker_running(self, capsys, tmp_path):
        """When Docker stack is running, shows container health + docker.env values."""
        stack_dir = tmp_path / "agentibridge"
        stack_dir.mkdir()
        env_file = stack_dir / "docker.env"
        env_file.write_text(
            "REDIS_URL=redis://redis:6379/0\n"
            "POSTGRES_URL=postgresql://ab:secret@postgres:5432/agentibridge\n"
            "AGENTIBRIDGE_TRANSPORT=sse\n"
            "AGENTIBRIDGE_PORT=8100\n"
            "AGENTIBRIDGE_POLL_INTERVAL=30\n"
        )

        container_health = {
            "agentibridge-redis": "healthy",
            "agentibridge-postgres": "healthy",
            "agentibridge-tunnel": None,  # not found
            "agentibridge": "running",
        }

        se = self._docker_inspect_side_effect(container_health)

        with (
            patch("agentibridge.cli._is_stack_running", return_value=True),
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli.subprocess.run", side_effect=se),
        ):
            cmd_status(MagicMock())

        output = capsys.readouterr().out

        # Redis section shows container health + docker.env URL
        assert "container: healthy" in output
        assert "redis://redis:6379/0 (Docker internal)" in output

        # Postgres section shows container health + docker.env URL
        assert "postgresql://ab:secret@postgres:5432/agentibridge (Docker internal)" in output

        # Config section reads from docker.env
        assert "transport: sse (docker.env)" in output
        assert "port: 8100 (docker.env)" in output
        assert "poll interval: 30s (docker.env)" in output

    def test_status_docker_not_running(self, capsys):
        """When Docker is NOT running, falls back to host env vars."""

        def se(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "systemctl" in cmd_str and "--user" in cmd_str:
                return _ok(stdout="inactive")
            if "systemctl" in cmd_str and "cloudflared" in cmd_str:
                return _ok(stdout="inactive")
            return _fail()

        with (
            patch("agentibridge.cli._is_stack_running", return_value=False),
            patch("agentibridge.cli.subprocess.run", side_effect=se),
            patch.dict(
                "os.environ",
                {
                    "AGENTIBRIDGE_TRANSPORT": "stdio",
                    "AGENTIBRIDGE_PORT": "8100",
                    "AGENTIBRIDGE_POLL_INTERVAL": "60",
                },
            ),
        ):
            # Mock redis/pg imports to avoid real connections
            mock_get_redis = MagicMock(return_value=None)
            mock_get_pg = MagicMock(return_value=None)
            with (
                patch.dict("sys.modules", {"agentibridge.redis_client": MagicMock(get_redis=mock_get_redis)}),
                patch.dict("sys.modules", {"agentibridge.pg_client": MagicMock(get_pg=mock_get_pg)}),
            ):
                cmd_status(MagicMock())

        output = capsys.readouterr().out

        # Config should show host env values (no "docker.env" annotation)
        assert "transport: stdio" in output
        assert "docker.env" not in output

        # Redis shows unavailable with host env
        assert "unavailable" in output

    def test_status_tunnel_systemd(self, capsys):
        """When cloudflared systemd service is active, shows 'systemd' in output."""

        def se(cmd, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd)
            if "systemctl" in cmd_str and "cloudflared" in cmd_str:
                return _ok(stdout="active")
            if "systemctl" in cmd_str and "--user" in cmd_str:
                return _ok(stdout="inactive")
            return _fail()

        with (
            patch("agentibridge.cli._is_stack_running", return_value=False),
            patch("agentibridge.cli.subprocess.run", side_effect=se),
            patch("agentibridge.cli._cloudflared_hostname", return_value="tunnel.example.com"),
        ):
            mock_get_redis = MagicMock(return_value=None)
            mock_get_pg = MagicMock(return_value=None)
            with (
                patch.dict("sys.modules", {"agentibridge.redis_client": MagicMock(get_redis=mock_get_redis)}),
                patch.dict("sys.modules", {"agentibridge.pg_client": MagicMock(get_pg=mock_get_pg)}),
            ):
                cmd_status(MagicMock())

        output = capsys.readouterr().out
        assert "active (systemd)" in output
        assert "hostname: tunnel.example.com" in output


@pytest.mark.unit
class TestContainerHealth:
    def test_returns_health_status(self):
        with patch("agentibridge.cli.subprocess.run", return_value=_ok(stdout="healthy")):
            assert _container_health("agentibridge-redis") == "healthy"

    def test_returns_none_when_not_found(self):
        with patch("agentibridge.cli.subprocess.run", return_value=_fail()):
            assert _container_health("agentibridge-redis") is None

    def test_returns_none_on_exception(self):
        with patch("agentibridge.cli.subprocess.run", side_effect=Exception("no docker")):
            assert _container_health("agentibridge-redis") is None


@pytest.mark.unit
class TestSystemdActive:
    def test_returns_active(self):
        with patch("agentibridge.cli.subprocess.run", return_value=_ok(stdout="active")):
            assert _systemd_active("cloudflared") == "active"

    def test_returns_inactive(self):
        with patch("agentibridge.cli.subprocess.run", return_value=_ok(stdout="inactive")):
            assert _systemd_active("cloudflared") == "inactive"

    def test_returns_none_on_exception(self):
        with patch("agentibridge.cli.subprocess.run", side_effect=FileNotFoundError):
            assert _systemd_active("cloudflared") is None


def _mock_cloudflared_dir(tmp_path):
    """Create a .cloudflared dir under tmp_path and patch the constants."""
    cf_dir = tmp_path / ".cloudflared"
    cf_dir.mkdir()
    return cf_dir


@pytest.mark.unit
class TestCloudflaredHostname:
    def test_extracts_hostname(self, tmp_path):
        cf_dir = _mock_cloudflared_dir(tmp_path)
        (cf_dir / "config.yml").write_text(
            "tunnel: abc-123\ningress:\n  - hostname: bridge.example.com\n    service: http://localhost:8100\n"
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            assert _cloudflared_hostname() == "bridge.example.com"

    def test_returns_none_when_no_file(self, tmp_path):
        with patch("pathlib.Path.home", return_value=tmp_path):
            assert _cloudflared_hostname() is None


@pytest.mark.unit
class TestParseCloudflaredConfig:
    def test_parses_full_config(self, tmp_path):
        cf_dir = _mock_cloudflared_dir(tmp_path)
        (cf_dir / "config.yml").write_text(
            "tunnel: abc-123\n"
            "credentials-file: /home/user/.cloudflared/abc-123.json\n"
            "ingress:\n"
            "  - hostname: bridge.example.com\n"
            "    service: http://localhost:8100\n"
            "  - service: http_status:404\n"
        )
        with patch("pathlib.Path.home", return_value=tmp_path):
            info = _parse_cloudflared_config()
        assert info["tunnel_id"] == "abc-123"
        assert info["hostname"] == "bridge.example.com"
        assert info["service"] == "http://localhost:8100"
        assert info["credentials_file"] == "/home/user/.cloudflared/abc-123.json"

    def test_returns_empty_when_no_file(self, tmp_path):
        with patch("pathlib.Path.home", return_value=tmp_path):
            assert _parse_cloudflared_config() == {}


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
        assert re.search(r"\d+\.\d+\.\d+", output)


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
    def test_tunnel_no_docker_no_systemd(self, capsys):
        with (
            patch("shutil.which", return_value=None),
            patch("agentibridge.cli._systemd_active", return_value="inactive"),
        ):
            args = MagicMock()
            cmd_tunnel(args)
        output = capsys.readouterr().out
        assert "not running" in output

    def test_tunnel_not_running_no_systemd(self, capsys):
        with patch("shutil.which", return_value="/usr/bin/docker"):
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stdout = ""
            with (
                patch("agentibridge.cli.subprocess.run", return_value=mock_result),
                patch("agentibridge.cli._systemd_active", return_value="inactive"),
            ):
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
        assert '"type": "http"' in output
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

    def test_tunnel_systemd_with_config(self, capsys):
        """When no Docker tunnel but cloudflared runs via systemd, shows config."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        cfg_info = {
            "tunnel_id": "abc-123",
            "hostname": "bridge.example.com",
            "service": "http://localhost:8100",
            "credentials_file": "/home/user/.cloudflared/abc-123.json",
        }

        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("agentibridge.cli.subprocess.run", return_value=mock_result),
            patch("agentibridge.cli._systemd_active", return_value="active"),
            patch("agentibridge.cli._parse_cloudflared_config", return_value=cfg_info),
        ):
            args = MagicMock()
            cmd_tunnel(args)

        output = capsys.readouterr().out
        assert "active (systemd)" in output
        assert "tunnel id: abc-123" in output
        assert "hostname:  bridge.example.com" in output
        assert "service:   http://localhost:8100" in output
        assert "https://bridge.example.com/health" in output
        assert "https://bridge.example.com/mcp" in output
        assert '"type": "http"' in output


@pytest.mark.unit
class TestShortDigest:
    def test_shortens_sha256(self):
        assert _short_digest("sha256:abcdef123456789") == "sha256:abcdef123456"

    def test_handles_none_digest(self):
        assert _short_digest("<none>") == "(none)"
        assert _short_digest("") == "(none)"

    def test_no_algo_prefix(self):
        assert _short_digest("abcdef1234567890") == "abcdef123456"


def _make_stack_dir() -> Path:
    """Return a temp Path with compose + docker.env files for testing."""
    d = Path(tempfile.mkdtemp())
    (d / "docker-compose.yml").write_text("services: {}\n")
    (d / "docker.env").write_text(
        "REDIS_URL=redis://r:6379/0\n"
        "POSTGRES_URL=postgresql://a:a@localhost/a\n"
        "POSTGRES_USER=a\nPOSTGRES_PASSWORD=a\nPOSTGRES_DB=a\n"
        "AGENTIBRIDGE_TRANSPORT=sse\nAGENTIBRIDGE_PORT=8100\n"
    )
    return d


def _ok(stdout="", stderr=""):
    """Helper: return a MagicMock subprocess result with rc=0."""
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = stderr
    return r


def _fail(stdout="", stderr=""):
    """Helper: return a MagicMock subprocess result with rc=1."""
    r = MagicMock()
    r.returncode = 1
    r.stdout = stdout
    r.stderr = stderr
    return r


@pytest.mark.unit
class TestCmdUpdate:
    """Tests for `agentibridge update`.

    Each test records every subprocess.run call so we can assert on the
    exact commands, their order, and that the right arguments are passed.
    """

    def _run_update(self, *, docker_flag=False, has_docker=False, side_effect=None):
        """Run cmd_update with mocks, return (calls, output).

        calls: list of (cmd_list, kwargs) tuples for every subprocess.run call.
        """
        calls = []
        original_side_effect = side_effect

        def recording_side_effect(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            return original_side_effect(cmd, **kwargs)

        args = MagicMock()
        args.docker = docker_flag

        docker_path = "/usr/bin/docker" if has_docker else None
        with patch("shutil.which", return_value=docker_path):
            with patch("agentibridge.cli.subprocess.run", side_effect=recording_side_effect):
                cmd_update(args)

        return calls

    # ── pip upgrade: correct command ──────────────────────────────────

    def test_calls_pip_install_upgrade(self, capsys):
        """Verifies the exact pip install --upgrade command."""
        import sys as _sys

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout="Version: 0.3.0\n")
            return _fail()

        calls = self._run_update(side_effect=se)
        capsys.readouterr()  # consume output

        # First call should be pip install --upgrade agentibridge
        pip_cmd = calls[0][0]
        assert pip_cmd[0] == _sys.executable
        assert pip_cmd[1:] == ["-m", "pip", "install", "--upgrade", "agentibridge"]

        # Second call should be pip show agentibridge
        show_cmd = calls[1][0]
        assert show_cmd[0] == _sys.executable
        assert show_cmd[1:] == ["-m", "pip", "show", "agentibridge"]

        # pip install must capture output (not print pip noise)
        assert calls[0][1].get("capture_output") is True
        assert calls[0][1].get("text") is True

    # ── pip upgrade: version change detected ──────────────────────────

    def test_pip_version_change_reported(self, capsys):
        """Reports old -> new version when pip upgrade changes version."""

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout="Version: 0.5.0\n")
            return _fail()

        self._run_update(side_effect=se)
        output = capsys.readouterr().out
        # Current version (from __version__) vs new version from pip show
        assert "Updated:" in output
        assert "0.5.0" in output

    # ── pip upgrade: already latest ───────────────────────────────────

    def test_pip_already_latest(self, capsys):
        """Shows 'already up to date' when pip show returns same version."""

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            return _fail()

        self._run_update(side_effect=se)
        output = capsys.readouterr().out
        assert "Already up to date" in output

    # ── pip failure: exits with error ─────────────────────────────────

    def test_pip_failure_exits(self, capsys):
        """Exits with error when pip install fails (no --docker)."""

        def se(cmd, **kw):
            return _fail(stderr="Could not find a version")

        with pytest.raises(SystemExit):
            self._run_update(side_effect=se)

        output = capsys.readouterr().out
        assert "ERROR" in output

    # ── docker skipped when not installed ─────────────────────────────

    def test_no_docker_skips_docker(self, capsys):
        """When docker is not installed, no docker commands are run."""

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            return _fail()

        calls = self._run_update(has_docker=False, side_effect=se)
        capsys.readouterr()

        # Only pip commands should have been called (no docker)
        for cmd, _ in calls:
            assert "docker" not in " ".join(cmd)

    # ── docker skipped when stack not running ─────────────────────────

    def test_docker_skipped_when_not_running(self, capsys):
        """When docker exists but stack is stopped, docker update is skipped."""

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            if "inspect" in cmd_str:
                return _fail()  # container not found
            return _fail()

        calls = self._run_update(has_docker=True, side_effect=se)
        output = capsys.readouterr().out

        # Should mention it was skipped
        assert "skipped" in output.lower()

        # No docker compose pull should have been called
        for cmd, _ in calls:
            assert "pull" not in cmd

    # ── docker forced with --docker flag ──────────────────────────────

    def test_docker_forced_with_flag(self, capsys):
        """--docker flag forces docker update even when stack is stopped."""
        images_calls = {"n": 0}

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            if "inspect" in cmd_str:
                return _fail()  # stack not running
            if "images" in cmd_str:
                images_calls["n"] += 1
                return _ok(stdout="sha256:aaa111222333")
            if "pull" in cmd_str:
                return _ok()
            if "ps" in cmd_str and "State" in cmd_str:
                return _ok(stdout="")  # stopped
            return _ok()

        with patch("agentibridge.cli._STACK_DIR", _make_stack_dir()):
            calls = self._run_update(docker_flag=True, has_docker=True, side_effect=se)

        capsys.readouterr()

        # docker compose pull agentibridge should have been called
        pull_calls = [cmd for cmd, _ in calls if "pull" in cmd]
        assert len(pull_calls) == 1
        assert "agentibridge" in pull_calls[0]

    # ── docker: pull + recreate when stack running ────────────────────

    def test_docker_pull_and_recreate_commands(self, capsys):
        """Verifies the exact docker compose commands for pull + recreate."""
        images_calls = {"n": 0}
        stack_dir = _make_stack_dir()

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            if "inspect" in cmd_str:
                return _ok(stdout="running")
            if "images" in cmd_str:
                images_calls["n"] += 1
                if images_calls["n"] == 1:
                    return _ok(stdout="sha256:old000000000")
                return _ok(stdout="sha256:new111111111")
            if "pull" in cmd_str:
                return _ok()
            if "ps" in cmd_str and "State" in cmd_str:
                return _ok(stdout="running\nrunning\n")
            if "up" in cmd_str:
                return _ok()
            if "ps" in cmd_str:
                return _ok(stdout="agentibridge\tUp 1s\t8100/tcp")
            return _ok()

        with patch("agentibridge.cli._STACK_DIR", stack_dir):
            calls = self._run_update(has_docker=True, side_effect=se)

        output = capsys.readouterr().out

        # Verify pull command uses compose with correct file and env
        pull_calls = [cmd for cmd, _ in calls if "pull" in cmd]
        assert len(pull_calls) == 1
        pull_cmd = pull_calls[0]
        assert pull_cmd[:2] == ["docker", "compose"]
        assert "-f" in pull_cmd
        assert str(stack_dir / "docker-compose.yml") in pull_cmd
        assert "--env-file" in pull_cmd
        assert str(stack_dir / "docker.env") in pull_cmd
        assert pull_cmd[-1] == "agentibridge"

        # Verify recreate command: up -d --no-deps --force-recreate agentibridge
        up_calls = [cmd for cmd, _ in calls if "up" in cmd]
        assert len(up_calls) == 1
        up_cmd = up_calls[0]
        assert "--no-deps" in up_cmd
        assert "--force-recreate" in up_cmd
        assert "-d" in up_cmd
        assert up_cmd[-1] == "agentibridge"

        # Verify digest comparison output
        assert "Image updated:" in output

    # ── docker: image already up to date ──────────────────────────────

    def test_docker_image_already_current(self, capsys):
        """When docker digest unchanged, reports 'already up to date'."""
        stack_dir = _make_stack_dir()

        def se(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "pip" in cmd_str and "install" in cmd_str:
                return _ok()
            if "pip" in cmd_str and "show" in cmd_str:
                return _ok(stdout=f"Version: {__version__}\n")
            if "inspect" in cmd_str:
                return _ok(stdout="running")
            if "images" in cmd_str:
                return _ok(stdout="sha256:same00000000")
            if "pull" in cmd_str:
                return _ok()
            if "ps" in cmd_str and "State" in cmd_str:
                return _ok(stdout="running\nrunning\n")
            if "up" in cmd_str:
                return _ok()
            if "ps" in cmd_str:
                return _ok(stdout="agentibridge\tUp 1s")
            return _ok()

        with patch("agentibridge.cli._STACK_DIR", stack_dir):
            self._run_update(has_docker=True, side_effect=se)

        output = capsys.readouterr().out
        assert "Image already up to date" in output


@pytest.mark.unit
class TestValidateEnv:
    def test_passes_when_all_vars_present(self, tmp_path):
        """No exit when all required vars are present."""
        env_file = tmp_path / "docker.env"
        env_file.write_text(
            "REDIS_URL=redis://redis:6379/0\n"
            "POSTGRES_URL=postgresql://a:a@postgres:5432/a\n"
            "AGENTIBRIDGE_TRANSPORT=sse\n"
            "AGENTIBRIDGE_PORT=8100\n"
            "POSTGRES_USER=agentibridge\n"
            "POSTGRES_PASSWORD=agentibridge\n"
            "POSTGRES_DB=agentibridge\n"
        )
        _validate_env(env_file)  # should not raise

    def test_exits_when_vars_missing(self, tmp_path, capsys):
        """Exits with code 1 listing missing variables."""
        env_file = tmp_path / "docker.env"
        env_file.write_text("REDIS_URL=redis://redis:6379/0\n")
        with pytest.raises(SystemExit) as exc:
            _validate_env(env_file)
        assert exc.value.code == 1
        output = capsys.readouterr().out
        assert "missing required variables" in output


@pytest.mark.unit
class TestEnsureStackDir:
    def test_scaffolds_compose_and_env(self, tmp_path, capsys):
        """Creates compose file and docker.env, returns stack_dir on first run."""
        stack_dir = tmp_path / "agentibridge"
        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli._LEGACY_STACK_DIR", tmp_path / "legacy"),
        ):
            result = _ensure_stack_dir()
        assert result == stack_dir
        output = capsys.readouterr().out
        assert "Created" in output
        assert "default configuration" in output
        assert "github.com" in output
        assert (stack_dir / "docker-compose.yml").exists()
        assert (stack_dir / "docker.env").exists()

    def test_migrates_old_env_with_docker_vars(self, tmp_path, capsys):
        """Moves .env to docker.env when it contains Docker vars."""
        stack_dir = tmp_path / "agentibridge"
        stack_dir.mkdir()
        # Write a compose file so it doesn't trigger first-run exit
        import shutil

        from agentibridge.cli import DATA_DIR

        shutil.copy2(DATA_DIR / "docker-compose.yml", stack_dir / "docker-compose.yml")
        # Create .env with Docker vars
        old_env = stack_dir / ".env"
        old_env.write_text(
            "REDIS_URL=redis://redis:6379/0\n"
            "POSTGRES_URL=postgresql://a:a@postgres:5432/a\n"
            "AGENTIBRIDGE_TRANSPORT=sse\n"
            "AGENTIBRIDGE_PORT=8100\n"
            "POSTGRES_USER=agentibridge\n"
            "POSTGRES_PASSWORD=agentibridge\n"
            "POSTGRES_DB=agentibridge\n"
        )
        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli._LEGACY_STACK_DIR", tmp_path / "legacy"),
        ):
            result = _ensure_stack_dir()
        output = capsys.readouterr().out
        assert "Migrated" in output
        assert (stack_dir / "docker.env").exists()
        assert result == stack_dir


@pytest.mark.unit
class TestReadEnvValue:
    """Tests for _read_env_value()."""

    def test_reads_existing_key(self, tmp_path):
        env = tmp_path / "test.env"
        env.write_text("FOO=bar\nBAZ=qux\n")
        assert _read_env_value("FOO", env) == "bar"
        assert _read_env_value("BAZ", env) == "qux"

    def test_returns_none_for_missing_key(self, tmp_path):
        env = tmp_path / "test.env"
        env.write_text("FOO=bar\n")
        assert _read_env_value("MISSING", env) is None

    def test_skips_comments(self, tmp_path):
        env = tmp_path / "test.env"
        env.write_text("# FOO=commented\nFOO=real\n")
        assert _read_env_value("FOO", env) == "real"

    def test_skips_lines_without_equals(self, tmp_path):
        env = tmp_path / "test.env"
        env.write_text("no_equals_here\nKEY=value\n")
        assert _read_env_value("KEY", env) == "value"


@pytest.mark.unit
class TestMaybeStartBridge:
    """Tests for _maybe_start_bridge()."""

    def test_no_env_file_returns_early(self, tmp_path):
        """No action when env file doesn't exist."""
        _maybe_start_bridge(tmp_path, env_file=tmp_path / "nonexistent.env")
        # Should not raise

    def test_no_secret_returns_early(self, tmp_path):
        """No action when DISPATCH_SECRET is not set."""
        env = tmp_path / "docker.env"
        env.write_text("REDIS_URL=redis://localhost\n")
        _maybe_start_bridge(tmp_path, env_file=env)
        # Should not raise

    def test_placeholder_secret_skipped(self, tmp_path):
        """Placeholder secret is skipped unless allow_placeholder=True."""
        env = tmp_path / "docker.env"
        env.write_text("DISPATCH_SECRET=changeme-generate-a-random-secret\n")
        _maybe_start_bridge(tmp_path, env_file=env)
        # Should not raise — placeholder is ignored

    def test_already_running_skipped(self, tmp_path):
        """If bridge process already running, skip start."""
        env = tmp_path / "docker.env"
        env.write_text("DISPATCH_SECRET=real-secret\n")

        with patch("agentibridge.cli.subprocess.run", return_value=_ok()) as mock_run:
            _maybe_start_bridge(tmp_path, env_file=env)

        # pgrep should have been called
        mock_run.assert_called_once()
        assert "pgrep" in mock_run.call_args[0][0]

    def test_starts_bridge_successfully(self, tmp_path, capsys):
        """Starts bridge when secret is set and not already running."""
        env = tmp_path / "docker.env"
        env.write_text("DISPATCH_SECRET=real-secret\nDISPATCH_BRIDGE_PORT=9999\n")

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        mock_proc.pid = 12345

        with (
            patch("agentibridge.cli.subprocess.run", return_value=_fail()),  # pgrep = not running
            patch("agentibridge.cli.subprocess.Popen", return_value=mock_proc),
            patch("agentibridge.cli.time.sleep"),
        ):
            _maybe_start_bridge(tmp_path, env_file=env)

        output = capsys.readouterr().out
        assert "auto-started" in output
        assert "12345" in output

    def test_bridge_fails_to_start(self, tmp_path, capsys):
        """Reports warning when bridge process exits immediately."""
        env = tmp_path / "docker.env"
        env.write_text("DISPATCH_SECRET=real-secret\n")

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # exited immediately

        with (
            patch("agentibridge.cli.subprocess.run", return_value=_fail()),
            patch("agentibridge.cli.subprocess.Popen", return_value=mock_proc),
            patch("agentibridge.cli.time.sleep"),
        ):
            _maybe_start_bridge(tmp_path, env_file=env)

        output = capsys.readouterr().out
        assert "WARNING" in output

    def test_popen_exception_handled(self, tmp_path, capsys):
        """Exception during Popen is caught gracefully."""
        env = tmp_path / "docker.env"
        env.write_text("DISPATCH_SECRET=real-secret\n")

        with (
            patch("agentibridge.cli.subprocess.run", return_value=_fail()),
            patch("agentibridge.cli.subprocess.Popen", side_effect=OSError("No such file")),
        ):
            _maybe_start_bridge(tmp_path, env_file=env)

        output = capsys.readouterr().out
        assert "WARNING" in output


@pytest.mark.unit
class TestCmdRunTest:
    """Tests for _cmd_run_test() dev mode."""

    def test_exits_if_not_in_repo_root(self, tmp_path):
        """Exits with error when Dockerfile is missing."""
        with (
            patch("os.getcwd", return_value=str(tmp_path)),
            patch("agentibridge.cli.Path.exists", return_value=False),
            pytest.raises(SystemExit),
        ):
            _cmd_run_test()

    def test_runs_docker_compose_build(self, tmp_path, capsys):
        """Full test mode: backup, compose up --build, bridge start."""
        # Create repo-root-like structure
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        (tmp_path / "docker-compose.yml").write_text("services: {}\n")
        (tmp_path / ".env.example").write_text(
            "REDIS_URL=redis://localhost\n# DISPATCH_SECRET=changeme-generate-a-random-secret\n"
        )

        stack_dir = tmp_path / "stack"
        stack_dir.mkdir()

        calls = []

        def se(cmd, **kw):
            calls.append(list(cmd) if not isinstance(cmd, list) else cmd)
            return _ok()

        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli.subprocess.run", side_effect=se),
            patch("agentibridge.cli._maybe_start_bridge"),
            patch("agentibridge.cli._ensure_stack_dir", return_value=stack_dir),
            patch("agentibridge.cli.Path.exists", side_effect=lambda self=None: True),
            patch("agentibridge.cli.shutil.copytree"),
            patch("agentibridge.cli.shutil.rmtree"),
            patch("agentibridge.cli.shutil.copy2"),
            patch("builtins.open", MagicMock()),
            patch(
                "agentibridge.cli.Path.read_text", return_value="# DISPATCH_SECRET=changeme-generate-a-random-secret\n"
            ),
            patch("agentibridge.cli.Path.write_text"),
        ):
            _cmd_run_test()

        output = capsys.readouterr().out
        assert "Stack started from local source" in output


@pytest.mark.unit
class TestCmdRun:
    """Tests for cmd_run()."""

    def test_no_docker_exits(self, capsys):
        """Exits when docker is not installed."""
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(SystemExit),
        ):
            cmd_run(MagicMock(test=False, rebuild=False))

    def test_test_mode_delegates(self):
        """--test flag delegates to _cmd_run_test."""
        with (
            patch("shutil.which", return_value="/usr/bin/docker"),
            patch("agentibridge.cli._cmd_run_test") as mock_test,
        ):
            cmd_run(MagicMock(test=True))
        mock_test.assert_called_once()


@pytest.mark.unit
class TestCmdBridge:
    """Tests for cmd_bridge() management command."""

    def test_start_no_env_file(self, tmp_path, capsys):
        """start exits when docker.env doesn't exist."""
        with (
            patch("agentibridge.cli._STACK_DIR", tmp_path / "nodir"),
            pytest.raises(SystemExit),
        ):
            cmd_bridge(MagicMock(action="start"))

    def test_start_no_secret(self, tmp_path, capsys):
        """start exits when DISPATCH_SECRET is not in docker.env."""
        stack_dir = tmp_path / "stack"
        stack_dir.mkdir()
        (stack_dir / "docker.env").write_text("REDIS_URL=redis://localhost\n")

        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            pytest.raises(SystemExit),
        ):
            cmd_bridge(MagicMock(action="start"))

    def test_start_already_running(self, tmp_path, capsys):
        """start reports already running when pgrep finds process."""
        stack_dir = tmp_path / "stack"
        stack_dir.mkdir()
        (stack_dir / "docker.env").write_text("DISPATCH_SECRET=real-secret\n")

        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli.subprocess.run", return_value=_ok(stdout=b"12345")),
        ):
            cmd_bridge(MagicMock(action="start"))

        output = capsys.readouterr().out
        assert "already running" in output

    def test_stop_no_process(self, capsys):
        """stop reports no process when pgrep finds nothing."""
        with patch("agentibridge.cli.subprocess.run", return_value=MagicMock(stdout="", returncode=1)):
            cmd_bridge(MagicMock(action="stop"))

        output = capsys.readouterr().out
        assert "No dispatch bridge" in output

    def test_stop_kills_process(self, capsys):
        """stop kills running bridge processes."""
        calls = []

        def se(cmd, **kw):
            calls.append(cmd)
            if "pgrep" in cmd:
                return MagicMock(stdout="1234\n5678", returncode=0)
            return _ok()

        with patch("agentibridge.cli.subprocess.run", side_effect=se):
            cmd_bridge(MagicMock(action="stop"))

        output = capsys.readouterr().out
        assert "stopped" in output

    def test_logs_no_file(self, capsys):
        """logs exits when log file doesn't exist."""
        with pytest.raises(SystemExit):
            cmd_bridge(MagicMock(action="logs"))
