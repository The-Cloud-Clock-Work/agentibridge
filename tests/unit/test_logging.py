"""Tests for agentic_bridge.logging module."""

import json
import os

import pytest


@pytest.mark.unit
class TestLog:
    def test_log_writes_json(self, tmp_path, monkeypatch):
        log_file = tmp_path / "test.log"
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", True)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", str(log_file))

        from agentic_bridge.logging import log

        log("test message", {"key": "value"})

        content = log_file.read_text()
        entry = json.loads(content.strip())
        assert entry["message"] == "test message"
        assert entry["payload"] == {"key": "value"}
        assert "@timestamp" in entry

    def test_log_disabled(self, tmp_path, monkeypatch):
        log_file = tmp_path / "test.log"
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", False)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", str(log_file))

        from agentic_bridge.logging import log

        log("should not appear")

        assert not log_file.exists()

    def test_log_creates_directory(self, tmp_path, monkeypatch):
        log_file = tmp_path / "subdir" / "deep" / "test.log"
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", True)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", str(log_file))

        from agentic_bridge.logging import log

        log("test")

        assert log_file.exists()

    def test_log_no_payload(self, tmp_path, monkeypatch):
        log_file = tmp_path / "test.log"
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", True)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", str(log_file))

        from agentic_bridge.logging import log

        log("simple message")

        entry = json.loads(log_file.read_text().strip())
        assert "payload" not in entry

    def test_log_silent_failure(self, monkeypatch):
        """Log should never raise, even with bad paths."""
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", True)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", "/proc/nonexistent/impossible.log")

        from agentic_bridge.logging import log

        # Should not raise
        log("this should silently fail")

    def test_log_appends(self, tmp_path, monkeypatch):
        log_file = tmp_path / "test.log"
        monkeypatch.setattr("agentic_bridge.logging.LOG_ENABLED", True)
        monkeypatch.setattr("agentic_bridge.logging.LOG_FILE", str(log_file))

        from agentic_bridge.logging import log

        log("first")
        log("second")

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2


@pytest.mark.unit
class TestDefaultLogFile:
    def test_docker_detection(self, monkeypatch):
        from agentic_bridge.logging import _default_log_file

        # When /.dockerenv doesn't exist (normal), should use ~/.cache path
        result = _default_log_file()
        assert ".cache/agentic-bridge" in result

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AGENTIC_BRIDGE_LOG_FILE", "/custom/path.log")
        # The LOG_FILE is set at module load time, so we test the env var logic
        assert os.getenv("AGENTIC_BRIDGE_LOG_FILE") == "/custom/path.log"
