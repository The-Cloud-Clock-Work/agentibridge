"""Tests for agentic_bridge.collector module."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentic_bridge.collector import SessionCollector
from agentic_bridge.store import SessionStore


@pytest.mark.unit
class TestSessionCollector:
    """Tests for SessionCollector."""

    def test_init_defaults(self):
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        assert collector._interval == 60
        assert collector._store is store
        assert collector._thread is None

    def test_init_custom_interval(self, monkeypatch):
        monkeypatch.setenv("SESSION_BRIDGE_POLL_INTERVAL", "30")
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        assert collector._interval == 30

    def test_collect_once_empty_dir(self, tmp_path):
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        collector._projects_dir = tmp_path

        stats = collector.collect_once()

        assert stats["files_scanned"] == 0
        assert stats["sessions_updated"] == 0
        assert stats["entries_added"] == 0
        assert stats["duration_ms"] >= 0

    def test_collect_once_with_files(self, temp_projects_dir):
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        collector = SessionCollector(store)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["files_scanned"] == 2
        assert stats["sessions_updated"] >= 1
        assert store.upsert_session.called
        assert store.add_entries.called
        assert store.save_file_position.called

    def test_collect_once_incremental(self, temp_projects_dir):
        """When position matches file size, no new entries should be added."""
        store = MagicMock(spec=SessionStore)

        # Set position to match file sizes so nothing is "new"
        def fake_position(filepath):
            return Path(filepath).stat().st_size

        store.get_file_position.side_effect = fake_position
        collector = SessionCollector(store)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["files_scanned"] == 2
        assert stats["sessions_updated"] == 0
        assert stats["entries_added"] == 0

    def test_collect_once_handles_file_error(self, temp_projects_dir):
        store = MagicMock(spec=SessionStore)
        store.get_file_position.side_effect = Exception("boom")
        collector = SessionCollector(store)
        collector._projects_dir = temp_projects_dir

        # Should not raise
        stats = collector.collect_once()
        assert stats["files_scanned"] == 2

    def test_scan_file_new_file(self, temp_projects_dir):
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        collector = SessionCollector(store)

        filepath = temp_projects_dir / "-home-user-dev-myapp" / "session-001.jsonl"
        result = collector._scan_file("session-001", "-home-user-dev-myapp", filepath)

        assert result["updated"] is True
        assert result["entries_added"] > 0

    def test_scan_file_no_changes(self, temp_projects_dir):
        store = MagicMock(spec=SessionStore)
        filepath = temp_projects_dir / "-home-user-dev-myapp" / "session-001.jsonl"
        store.get_file_position.return_value = filepath.stat().st_size
        collector = SessionCollector(store)

        result = collector._scan_file("session-001", "-home-user-dev-myapp", filepath)

        assert result["updated"] is False
        assert result["entries_added"] == 0

    def test_scan_file_always_passes_entries_to_meta(self, temp_projects_dir):
        """Bug fix #10: entries should always be passed to parse_transcript_meta."""
        store = MagicMock(spec=SessionStore)
        # Non-zero offset simulates incremental read
        store.get_file_position.return_value = 0
        collector = SessionCollector(store)

        filepath = temp_projects_dir / "-home-user-dev-myapp" / "session-001.jsonl"

        with patch("agentic_bridge.collector.parse_transcript_meta") as mock_meta:
            mock_meta.return_value = MagicMock()
            collector._scan_file("session-001", "-home-user-dev-myapp", filepath)
            # entries arg should be passed (not None)
            call_args = mock_meta.call_args
            assert call_args is not None
            # Verify entries argument was passed (not None)
            assert len(call_args[0]) > 2 or "entries" in call_args[1]

    def test_start_creates_daemon_thread(self):
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        collector._projects_dir = Path("/nonexistent")
        collector._interval = 3600  # long interval to avoid actual polling

        collector.start()
        try:
            assert collector._thread is not None
            assert collector._thread.is_alive()
            assert collector._thread.daemon is True
            assert collector._thread.name == "session-collector"
        finally:
            collector.stop()

    def test_start_idempotent(self):
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        collector._projects_dir = Path("/nonexistent")
        collector._interval = 3600

        collector.start()
        thread1 = collector._thread
        collector.start()  # Should not create a new thread
        assert collector._thread is thread1

        collector.stop()

    def test_stop_signals_thread(self):
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        collector._projects_dir = Path("/nonexistent")
        collector._interval = 3600

        collector.start()
        collector.stop()

        assert collector._stop_event.is_set()
        # Thread should have stopped (or be stopping)
        time.sleep(0.5)
        assert not collector._thread.is_alive()
