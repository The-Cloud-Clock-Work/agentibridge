"""Tests for agentibridge.collector module."""

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentibridge.collector import SessionCollector
from agentibridge.store import SessionStore


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
        monkeypatch.setenv("AGENTIBRIDGE_POLL_INTERVAL", "30")
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

        with patch("agentibridge.collector.parse_transcript_meta") as mock_meta:
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


@pytest.mark.unit
class TestCollectorEmbedding:
    """Tests for automatic embedding in the collector."""

    def test_no_embedder_no_embedding(self, temp_projects_dir):
        """Without an embedder, collect_once works as before."""
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        collector = SessionCollector(store)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["sessions_embedded"] == 0
        assert stats["sessions_updated"] >= 1

    def test_embedder_called_for_updated_sessions(self, temp_projects_dir):
        """Embedder is called for each session that had new entries."""
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        embedder = MagicMock()
        embedder.is_available.return_value = True
        embedder.embed_session.return_value = 5  # 5 chunks created

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["sessions_updated"] >= 1
        assert stats["sessions_embedded"] >= 1
        assert embedder.embed_session.call_count == stats["sessions_updated"]

    def test_embedder_not_available_skips(self, temp_projects_dir):
        """When embedder.is_available() is False, embedding is skipped."""
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        embedder = MagicMock()
        embedder.is_available.return_value = False

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["sessions_embedded"] == 0
        embedder.embed_session.assert_not_called()

    def test_embedder_error_does_not_block(self, temp_projects_dir):
        """Embedding errors are logged but don't prevent other sessions."""
        store = MagicMock(spec=SessionStore)
        store.get_file_position.return_value = 0
        embedder = MagicMock()
        embedder.is_available.return_value = True
        embedder.embed_session.side_effect = RuntimeError("LLM API down")

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        # Sessions were still indexed even though embedding failed
        assert stats["sessions_updated"] >= 1
        assert stats["sessions_embedded"] == 0

    def test_no_updated_sessions_no_embedding(self, temp_projects_dir):
        """When all sessions are up to date, embedder is not called."""
        store = MagicMock(spec=SessionStore)

        # Position matches file size — nothing new
        def fake_position(filepath):
            return Path(filepath).stat().st_size

        store.get_file_position.side_effect = fake_position

        embedder = MagicMock()
        embedder.is_available.return_value = True

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        stats = collector.collect_once()

        assert stats["sessions_updated"] == 0
        assert stats["sessions_embedded"] == 0
        embedder.embed_session.assert_not_called()

    def test_init_stores_embedder(self):
        """Embedder is stored as an attribute."""
        store = MagicMock(spec=SessionStore)
        embedder = MagicMock()
        collector = SessionCollector(store, embedder=embedder)
        assert collector._embedder is embedder

    def test_init_embedder_defaults_none(self):
        """Embedder defaults to None when not provided."""
        store = MagicMock(spec=SessionStore)
        collector = SessionCollector(store)
        assert collector._embedder is None

    def test_backfill_embeds_unembedded_sessions(self, temp_projects_dir):
        """Backfill embeds sessions in Redis that have no chunks in Postgres."""
        store = MagicMock(spec=SessionStore)

        # All sessions already indexed (no new entries)
        def fake_position(filepath):
            return Path(filepath).stat().st_size

        store.get_file_position.side_effect = fake_position
        store.list_session_ids.return_value = ["s1", "s2", "s3"]

        embedder = MagicMock()
        embedder.is_available.return_value = True
        embedder.embed_session.return_value = 5

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        # Mock Postgres to say no sessions are embedded yet
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("agentibridge.pg_client.get_pg", return_value=mock_pool):
            stats = collector.collect_once()

        assert stats["sessions_updated"] == 0
        assert stats["sessions_embedded"] == 3
        assert embedder.embed_session.call_count == 3

    def test_backfill_skips_already_embedded(self, temp_projects_dir):
        """Backfill doesn't re-embed sessions already in Postgres."""
        store = MagicMock(spec=SessionStore)

        def fake_position(filepath):
            return Path(filepath).stat().st_size

        store.get_file_position.side_effect = fake_position
        store.list_session_ids.return_value = ["s1", "s2", "s3"]

        embedder = MagicMock()
        embedder.is_available.return_value = True
        embedder.embed_session.return_value = 5

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        # s1 and s2 already embedded
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [("s1",), ("s2",)]
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("agentibridge.pg_client.get_pg", return_value=mock_pool):
            stats = collector.collect_once()

        # Only s3 should be embedded
        assert stats["sessions_embedded"] == 1
        embedder.embed_session.assert_called_once_with("s3")

    def test_backfill_respects_batch_limit(self, temp_projects_dir):
        """Backfill processes at most _BACKFILL_BATCH per cycle."""
        store = MagicMock(spec=SessionStore)

        def fake_position(filepath):
            return Path(filepath).stat().st_size

        store.get_file_position.side_effect = fake_position
        store.list_session_ids.return_value = [f"s{i}" for i in range(50)]

        embedder = MagicMock()
        embedder.is_available.return_value = True
        embedder.embed_session.return_value = 3

        collector = SessionCollector(store, embedder=embedder)
        collector._projects_dir = temp_projects_dir

        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        with patch("agentibridge.pg_client.get_pg", return_value=mock_pool):
            stats = collector.collect_once()

        assert stats["sessions_embedded"] == SessionCollector._BACKFILL_BATCH
        assert embedder.embed_session.call_count == SessionCollector._BACKFILL_BATCH
