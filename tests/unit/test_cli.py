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
    cmd_tunnel,
    cmd_update,
    _extract_tunnel_url,
    _short_digest,
    _validate_env,
    _ensure_stack_dir,
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
        """Creates compose file and docker.env, then exits for first-time setup."""
        stack_dir = tmp_path / "agentibridge"
        with (
            patch("agentibridge.cli._STACK_DIR", stack_dir),
            patch("agentibridge.cli._LEGACY_STACK_DIR", tmp_path / "legacy"),
        ):
            with pytest.raises(SystemExit) as exc:
                _ensure_stack_dir()
            assert exc.value.code == 1
        output = capsys.readouterr().out
        assert "Created" in output
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
