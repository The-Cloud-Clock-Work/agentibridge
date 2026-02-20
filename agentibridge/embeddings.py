"""Transcript embedding pipeline for semantic search.

Chunks transcripts into conversation turns, generates embeddings via
an OpenAI-compatible API (see llm_client.py), stores vectors in
Postgres + pgvector, and performs cosine similarity search via HNSW index.

Requires:
  - LLM API configured (LLM_API_BASE, LLM_API_KEY, LLM_EMBED_MODEL)
  - Postgres with pgvector for vector storage (POSTGRES_URL)
"""

import math
import os
from typing import Any, Dict, List, Optional

from agentibridge.logging import log


def _cosine_similarity_batch(query: List[float], vectors: List[List[float]]) -> List[float]:
    """Compute cosine similarity between a query vector and a batch of vectors.

    Returns a list of similarity scores in [0, 1] (or [-1, 1] for negative components).
    """
    scores = []
    q_norm = math.sqrt(sum(x * x for x in query))
    if q_norm == 0:
        return [0.0] * len(vectors)
    for vec in vectors:
        dot = sum(a * b for a, b in zip(query, vec))
        v_norm = math.sqrt(sum(x * x for x in vec))
        if v_norm == 0:
            scores.append(0.0)
        else:
            scores.append(dot / (q_norm * v_norm))
    return scores


def _get_embed_fn():
    """Return an embed function if LLM API is configured for embeddings."""
    from agentibridge import llm_client

    if llm_client.is_embed_configured():
        return llm_client.embed_text
    return None


class TranscriptEmbedder:
    """Chunks transcripts, generates embeddings, stores in Postgres (pgvector)."""

    def __init__(self) -> None:
        self._pg = None
        self._pg_checked = False
        self._embed_fn = None
        self._embed_checked = False

    # ------------------------------------------------------------------
    # Lazy connections
    # ------------------------------------------------------------------

    def _get_pg(self):
        if self._pg_checked:
            return self._pg
        self._pg_checked = True
        try:
            from agentibridge.pg_client import get_pg

            self._pg = get_pg()
        except Exception:
            self._pg = None
        return self._pg

    def _get_embed(self):
        if self._embed_checked:
            return self._embed_fn
        self._embed_checked = True
        self._embed_fn = _get_embed_fn()
        return self._embed_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Check if embedding infrastructure is available."""
        return self._get_embed() is not None and self._get_pg() is not None

    def embed_session(self, session_id: str) -> int:
        """Chunk session transcript, embed, store vectors.

        Returns number of chunks created.
        """
        embed_fn = self._get_embed()
        if embed_fn is None:
            raise RuntimeError(
                "Embedding backend not available (configure LLM_API_BASE + LLM_API_KEY + LLM_EMBED_MODEL)"
            )

        pool = self._get_pg()
        if pool is None:
            raise RuntimeError("Postgres required for vector storage (configure POSTGRES_URL)")

        from agentibridge.store import SessionStore

        store = SessionStore()
        entries = store.get_session_entries(session_id, offset=0, limit=10000)
        meta = store.get_session_meta(session_id)

        if not entries:
            return 0

        chunks = self._chunk_turns(entries)
        chunk_count = 0

        with pool.connection() as conn:
            for idx, chunk in enumerate(chunks):
                try:
                    vector = embed_fn(chunk["text"][:8000])

                    conn.execute(
                        """
                        INSERT INTO transcript_chunks
                            (session_id, chunk_idx, project, project_encoded,
                             timestamp, text_preview, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, chunk_idx) DO UPDATE SET
                            project=EXCLUDED.project,
                            project_encoded=EXCLUDED.project_encoded,
                            timestamp=EXCLUDED.timestamp,
                            text_preview=EXCLUDED.text_preview,
                            embedding=EXCLUDED.embedding,
                            created_at=now()
                        """,
                        (
                            session_id,
                            idx,
                            meta.project_path if meta else "",
                            meta.project_encoded if meta else "",
                            chunk.get("timestamp", ""),
                            chunk["text"][:1000],
                            str(vector),
                        ),
                    )
                    chunk_count += 1
                except Exception as e:
                    log(
                        "Embedding failed for chunk",
                        {
                            "session_id": session_id,
                            "chunk_idx": idx,
                            "error": str(e),
                        },
                    )
                    continue
            conn.commit()

        return chunk_count

    def search_semantic(
        self,
        query: str,
        project: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Embed query, search via pgvector cosine distance, return matches."""
        embed_fn = self._get_embed()
        if embed_fn is None:
            raise RuntimeError("Embedding backend not available")

        pool = self._get_pg()
        if pool is None:
            raise RuntimeError("Postgres required for semantic search")

        query_vector = embed_fn(query)
        vec_str = str(query_vector)

        with pool.connection() as conn:
            if project:
                rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT session_id, chunk_idx, project, timestamp,
                               LEFT(text_preview, 300) AS text_preview,
                               1 - (embedding <=> %s::vector) AS score,
                               ROW_NUMBER() OVER (
                                   PARTITION BY session_id
                                   ORDER BY embedding <=> %s::vector
                               ) AS rn
                        FROM transcript_chunks
                        WHERE project ILIKE %s OR project_encoded ILIKE %s
                    )
                    SELECT session_id, chunk_idx, project, timestamp,
                           text_preview, ROUND(score::numeric, 4) AS score
                    FROM ranked WHERE rn = 1
                    ORDER BY score DESC LIMIT %s
                    """,
                    (vec_str, vec_str, f"%{project}%", f"%{project}%", limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    WITH ranked AS (
                        SELECT session_id, chunk_idx, project, timestamp,
                               LEFT(text_preview, 300) AS text_preview,
                               1 - (embedding <=> %s::vector) AS score,
                               ROW_NUMBER() OVER (
                                   PARTITION BY session_id
                                   ORDER BY embedding <=> %s::vector
                               ) AS rn
                        FROM transcript_chunks
                    )
                    SELECT session_id, chunk_idx, project, timestamp,
                           text_preview, ROUND(score::numeric, 4) AS score
                    FROM ranked WHERE rn = 1
                    ORDER BY score DESC LIMIT %s
                    """,
                    (vec_str, vec_str, limit),
                ).fetchall()

        results = []
        for row in rows:
            results.append(
                {
                    "session_id": row[0],
                    "chunk_idx": row[1],
                    "project": row[2],
                    "timestamp": row[3],
                    "text_preview": row[4],
                    "score": float(row[5]),
                }
            )

        return results

    def generate_summary(self, session_id: str) -> str:
        """Generate session summary via LLM.

        Priority:
        1. Anthropic SDK (if ANTHROPIC_API_KEY is set)
        2. OpenAI-compatible API via llm_client (if LLM_API_BASE is set)
        """
        from agentibridge.store import SessionStore

        store = SessionStore()
        entries = store.get_session_entries(session_id, offset=0, limit=10000)
        meta = store.get_session_meta(session_id)

        if not entries:
            return "No entries found for this session."

        transcript_text = self._build_transcript_text(entries, max_chars=12000)

        prompt = (
            "Summarize this Claude Code session transcript in 2-3 sentences. "
            "Focus on what was accomplished, key decisions made, and the outcome.\n\n"
            f"Project: {meta.project_path if meta else 'unknown'}\n"
            f"Branch: {meta.git_branch if meta else 'unknown'}\n\n"
            f"Transcript:\n{transcript_text}"
        )

        # Try Anthropic SDK directly
        summary = None
        try:
            import anthropic

            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response.content[0].text
        except ImportError:
            pass  # SDK not installed, try next
        except Exception:
            pass  # SDK failed (no API key, etc.), try next

        # Fallback 2: OpenAI-compatible API via llm_client
        if summary is None:
            try:
                from agentibridge import llm_client

                if llm_client.is_configured():
                    summary = llm_client.chat_completion(prompt=prompt)
            except Exception:
                pass  # LLM API failed, try next

        # Fallback 3: Claude CLI (if binary is available)
        if summary is None:
            try:
                from agentibridge.claude_runner import run_claude_sync

                result = run_claude_sync(prompt, model="haiku")
                if result.success and result.result:
                    summary = result.result
            except Exception:
                pass

        if summary is None:
            return (
                "Summary generation unavailable: configure ANTHROPIC_API_KEY, LLM_API_BASE, or mount claude CLI binary"
            )

        # Store summary in session metadata (Redis)
        try:
            from agentibridge.redis_client import get_redis

            r = get_redis()
            if r is not None:
                prefix = os.getenv("REDIS_KEY_PREFIX", "agentibridge")
                meta_key = f"{prefix}:sb:session:{session_id}:meta"
                r.hset(meta_key, "summary", summary)
        except Exception:
            pass

        return summary

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chunk_turns(self, entries: list) -> List[Dict[str, str]]:
        """Chunk entries into conversation turns (user+assistant pairs)."""
        chunks: list = []
        current: dict = {"text": "", "timestamp": ""}

        for entry in entries:
            if entry.entry_type == "user":
                if current["text"]:
                    chunks.append(current)
                current = {
                    "text": f"User: {entry.content}\n",
                    "timestamp": entry.timestamp,
                }
            elif entry.entry_type == "assistant":
                current["text"] += f"Assistant: {entry.content}\n"
                if entry.tool_names:
                    current["text"] += f"Tools used: {', '.join(entry.tool_names)}\n"
            elif entry.entry_type == "summary":
                current["text"] += f"Summary: {entry.content}\n"

        if current["text"]:
            chunks.append(current)

        return chunks

    def _build_transcript_text(self, entries: list, max_chars: int = 12000) -> str:
        """Build readable transcript from entries, truncated to max_chars."""
        lines = []
        total = 0

        for entry in entries:
            if entry.entry_type == "user":
                line = f"User: {entry.content[:500]}"
            elif entry.entry_type == "assistant":
                tools = f" [tools: {', '.join(entry.tool_names)}]" if entry.tool_names else ""
                line = f"Assistant{tools}: {entry.content[:500]}"
            elif entry.entry_type == "summary":
                line = f"Summary: {entry.content[:500]}"
            else:
                continue

            if total + len(line) > max_chars:
                lines.append("... (truncated)")
                break
            lines.append(line)
            total += len(line)

        return "\n".join(lines)
