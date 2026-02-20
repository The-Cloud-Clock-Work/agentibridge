"""Shared Postgres + pgvector helper for agentibridge.

Provides a lazy-singleton connection pool and auto-creates the
``transcript_chunks`` table with pgvector HNSW index on first use.

Usage::

    from agentibridge.pg_client import get_pg

    pool = get_pg()
    if pool is not None:
        with pool.connection() as conn:
            conn.execute("SELECT 1")
"""

import os
import sys

# Lazy singleton
_pg_pool = None
_pg_checked = False


def get_pg():
    """Return a ``psycopg_pool.ConnectionPool`` or ``None`` if unavailable.

    Lazily connects on first call.  Returns ``None`` when ``POSTGRES_URL``
    (or ``DATABASE_URL`` fallback) is unset or when the connection cannot be
    established.  Auto-creates the pgvector extension, table, and indexes on
    first successful connection.
    """
    global _pg_pool, _pg_checked

    if _pg_checked:
        return _pg_pool

    _pg_checked = True
    url = os.getenv("POSTGRES_URL") or os.getenv("DATABASE_URL", "")
    if not url:
        return None

    try:
        from psycopg_pool import ConnectionPool

        _pg_pool = ConnectionPool(url, min_size=1, max_size=4, open=True)
        _ensure_schema(_pg_pool)
    except Exception as exc:
        print(f"Postgres connection failed ({url}): {exc}", file=sys.stderr)
        _pg_pool = None

    return _pg_pool


def _ensure_schema(pool) -> None:
    """Create the pgvector extension, table, and indexes if they don't exist."""
    dims = int(os.getenv("PGVECTOR_DIMENSIONS", "1536"))

    with pool.connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS transcript_chunks (
                id              SERIAL PRIMARY KEY,
                session_id      TEXT NOT NULL,
                chunk_idx       INTEGER NOT NULL,
                project         TEXT NOT NULL DEFAULT '',
                project_encoded TEXT NOT NULL DEFAULT '',
                timestamp       TEXT NOT NULL DEFAULT '',
                text_preview    TEXT NOT NULL DEFAULT '',
                embedding       vector({dims}),
                created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
                UNIQUE (session_id, chunk_idx)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tc_session_id
            ON transcript_chunks (session_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tc_project_encoded
            ON transcript_chunks (project_encoded)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tc_embedding_hnsw
            ON transcript_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
        """)
        conn.commit()


def reset_for_testing() -> None:
    """Reset the lazy singleton — for use in tests only."""
    global _pg_pool, _pg_checked
    if _pg_pool is not None:
        try:
            _pg_pool.close()
        except Exception:
            pass
    _pg_pool = None
    _pg_checked = False
