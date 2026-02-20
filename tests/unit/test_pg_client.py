"""Unit tests for agentibridge.pg_client module.

Tests lazy singleton, schema creation, and reset_for_testing.
"""

from unittest.mock import MagicMock

import pytest

import agentibridge.pg_client as pg_mod


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the pg_client singleton before and after each test."""
    pg_mod.reset_for_testing()
    yield
    pg_mod.reset_for_testing()


# ===================================================================
# get_pg
# ===================================================================


@pytest.mark.unit
class TestGetPg:
    """Tests for get_pg lazy singleton."""

    def test_no_url_returns_none(self, monkeypatch):
        """get_pg returns None when POSTGRES_URL and DATABASE_URL are unset."""
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert pg_mod.get_pg() is None

    def test_empty_url_returns_none(self, monkeypatch):
        """get_pg returns None when POSTGRES_URL is empty string."""
        monkeypatch.setenv("POSTGRES_URL", "")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert pg_mod.get_pg() is None

    def test_bad_url_returns_none(self, monkeypatch):
        """get_pg returns None when connection fails (bad URL)."""
        monkeypatch.setenv("POSTGRES_URL", "postgresql://bad:bad@localhost:1/bad")
        # Connection will fail — should return None, not raise
        result = pg_mod.get_pg()
        assert result is None

    def test_caching_returns_same_result(self, monkeypatch):
        """get_pg caches the result and returns same value on subsequent calls."""
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        first = pg_mod.get_pg()
        assert first is None
        # Second call should hit cache (not re-check env)
        monkeypatch.setenv("POSTGRES_URL", "postgresql://x:x@localhost/x")
        second = pg_mod.get_pg()
        assert second is None  # cached from first call

    def test_database_url_fallback(self, monkeypatch):
        """get_pg falls back to DATABASE_URL when POSTGRES_URL is not set."""
        monkeypatch.delenv("POSTGRES_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "postgresql://bad:bad@localhost:1/bad")
        # Will attempt connection with DATABASE_URL (and fail)
        result = pg_mod.get_pg()
        assert result is None
        # But it tried (checked=True)
        assert pg_mod._pg_checked is True


# ===================================================================
# _ensure_schema
# ===================================================================


@pytest.mark.unit
class TestEnsureSchema:
    """Tests for _ensure_schema SQL execution."""

    def test_creates_extension_and_table(self, monkeypatch):
        """_ensure_schema executes CREATE EXTENSION, CREATE TABLE, and CREATE INDEX."""
        monkeypatch.setenv("PGVECTOR_DIMENSIONS", "1536")

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        pg_mod._ensure_schema(mock_pool)

        # Should have executed 5 statements: extension, table, 3 indexes
        assert mock_conn.execute.call_count == 5
        # Verify key SQL fragments
        calls = [str(c) for c in mock_conn.execute.call_args_list]
        sql_text = " ".join(calls)
        assert "CREATE EXTENSION IF NOT EXISTS vector" in sql_text
        assert "CREATE TABLE IF NOT EXISTS transcript_chunks" in sql_text
        assert "vector(1536)" in sql_text
        assert "idx_tc_session_id" in sql_text
        assert "idx_tc_project_encoded" in sql_text
        assert "idx_tc_embedding_hnsw" in sql_text
        assert "hnsw" in sql_text
        mock_conn.commit.assert_called_once()

    def test_custom_dimensions(self, monkeypatch):
        """_ensure_schema uses PGVECTOR_DIMENSIONS env var."""
        monkeypatch.setenv("PGVECTOR_DIMENSIONS", "768")

        mock_conn = MagicMock()
        mock_pool = MagicMock()
        mock_pool.connection.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)

        pg_mod._ensure_schema(mock_pool)

        calls = [str(c) for c in mock_conn.execute.call_args_list]
        sql_text = " ".join(calls)
        assert "vector(768)" in sql_text


# ===================================================================
# reset_for_testing
# ===================================================================


@pytest.mark.unit
class TestResetForTesting:
    """Tests for reset_for_testing singleton teardown."""

    def test_resets_singleton_state(self):
        """reset_for_testing clears _pg_pool and _pg_checked."""
        pg_mod._pg_checked = True
        pg_mod._pg_pool = MagicMock()
        pg_mod.reset_for_testing()
        assert pg_mod._pg_pool is None
        assert pg_mod._pg_checked is False

    def test_closes_pool_on_reset(self):
        """reset_for_testing calls pool.close() if pool exists."""
        mock_pool = MagicMock()
        pg_mod._pg_pool = mock_pool
        pg_mod._pg_checked = True
        pg_mod.reset_for_testing()
        mock_pool.close.assert_called_once()

    def test_handles_close_error_gracefully(self):
        """reset_for_testing doesn't raise if pool.close() fails."""
        mock_pool = MagicMock()
        mock_pool.close.side_effect = Exception("close failed")
        pg_mod._pg_pool = mock_pool
        pg_mod._pg_checked = True
        # Should not raise
        pg_mod.reset_for_testing()
        assert pg_mod._pg_pool is None
