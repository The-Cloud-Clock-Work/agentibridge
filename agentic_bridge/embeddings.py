"""Transcript embedding pipeline for semantic search.

Chunks transcripts into conversation turns, generates embeddings via
agenticore.search infrastructure, stores vectors in Redis, and performs
cosine similarity search.

Requires:
  - Embedding backend configured (EMBEDDING_BACKEND=bedrock or ollama)
  - Redis for vector storage (no file fallback for vectors)
"""

import json
import math
import os
from typing import Any, Dict, List, Optional

from agentic_bridge.logging import log


def _get_embed_fn():
    """Return an embed_query function based on EMBEDDING_BACKEND env var.

    Supported backends:
      - "ollama"  — POST to Ollama /api/embeddings (local, default model nomic-embed-text)
      - "bedrock" — AWS Bedrock via boto3 (model amazon.titan-embed-text-v1)
    """
    backend = os.getenv("EMBEDDING_BACKEND", "").lower()
    if backend == "ollama":
        return _embed_ollama
    if backend == "bedrock":
        return _embed_bedrock
    return None


def _embed_ollama(text: str) -> List[float]:
    """Generate embeddings via Ollama REST API."""
    import httpx

    url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    resp = httpx.post(
        f"{url}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def _embed_bedrock(text: str) -> List[float]:
    """Generate embeddings via AWS Bedrock."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION", "us-east-1"))
    model_id = os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v1")
    body = json.dumps({"inputText": text})
    resp = client.invoke_model(modelId=model_id, body=body, contentType="application/json")
    return json.loads(resp["body"].read())["embedding"]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors (pure Python fallback)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        log("Zero-norm vector in cosine similarity", {"norm_a": norm_a, "norm_b": norm_b})
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_similarity_batch(query: List[float], vectors: List[List[float]]) -> List[float]:
    """Batch cosine similarity — uses numpy when available."""
    try:
        import numpy as np

        q = np.array(query, dtype=np.float32)
        m = np.array(vectors, dtype=np.float32)
        dots = m @ q
        norms = np.linalg.norm(m, axis=1) * np.linalg.norm(q)
        norms[norms == 0] = 1.0
        return (dots / norms).tolist()
    except ImportError:
        return [_cosine_similarity(query, v) for v in vectors]


class TranscriptEmbedder:
    """Chunks transcripts, generates embeddings, stores in Redis."""

    def __init__(self) -> None:
        self._redis = None
        self._redis_checked = False
        self._embed_fn = None
        self._embed_checked = False
        self._prefix = os.getenv("REDIS_KEY_PREFIX", "agenticore")

    # ------------------------------------------------------------------
    # Lazy connections
    # ------------------------------------------------------------------

    def _get_redis(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        try:
            from agentic_bridge.redis_client import get_redis

            self._redis = get_redis()
        except Exception:
            self._redis = None
        return self._redis

    def _get_embed(self):
        if self._embed_checked:
            return self._embed_fn
        self._embed_checked = True
        self._embed_fn = _get_embed_fn()
        return self._embed_fn

    def _rkey(self, suffix: str) -> str:
        return f"{self._prefix}:sb:vec:{suffix}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Check if embedding infrastructure is available."""
        return self._get_embed() is not None

    def embed_session(self, session_id: str) -> int:
        """Chunk session transcript, embed, store vectors.

        Returns number of chunks created.
        """
        embed_fn = self._get_embed()
        if embed_fn is None:
            raise RuntimeError("Embedding backend not available (configure EMBEDDING_BACKEND)")

        r = self._get_redis()
        if r is None:
            raise RuntimeError("Redis required for vector storage")

        from agentic_bridge.store import SessionStore

        store = SessionStore()
        entries = store.get_session_entries(session_id, offset=0, limit=10000)
        meta = store.get_session_meta(session_id)

        if not entries:
            return 0

        chunks = self._chunk_turns(entries)
        chunk_count = 0

        for idx, chunk in enumerate(chunks):
            try:
                vector = embed_fn(chunk["text"][:8000])

                chunk_data = {
                    "session_id": session_id,
                    "chunk_idx": str(idx),
                    "project": meta.project_path if meta else "",
                    "project_encoded": meta.project_encoded if meta else "",
                    "timestamp": chunk.get("timestamp", ""),
                    "text": chunk["text"][:1000],
                    "vector": json.dumps(vector),
                }

                key = self._rkey(f"{session_id}:{idx}")
                r.hset(key, mapping=chunk_data)
                r.sadd(self._rkey("idx"), key)

                if meta:
                    r.sadd(self._rkey(f"proj:{meta.project_encoded}"), key)

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

        # Mark session as embedded
        r.sadd(self._rkey("embedded_sessions"), session_id)

        return chunk_count

    def search_semantic(
        self,
        query: str,
        project: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Embed query, cosine search over stored vectors, return matches."""
        embed_fn = self._get_embed()
        if embed_fn is None:
            raise RuntimeError("Embedding backend not available")

        r = self._get_redis()
        if r is None:
            raise RuntimeError("Redis required for semantic search")

        query_vector = embed_fn(query)

        # Collect candidate vector keys
        if project:
            keys: set = set()
            cursor = 0
            pattern = self._rkey(f"proj:*{project}*")
            while True:
                cursor, batch = r.scan(cursor, match=pattern, count=100)
                for proj_key in batch:
                    keys.update(r.smembers(proj_key))
                if cursor == 0:
                    break
        else:
            keys = r.smembers(self._rkey("idx"))

        if not keys:
            return []

        # Load all vectors and metadata in batches via pipeline
        pipe = r.pipeline()
        key_list = list(keys)
        for key in key_list:
            pipe.hgetall(key)
        results = pipe.execute()

        # Parse vectors and metadata
        vectors = []
        meta_list = []
        for data in results:
            if not data:
                continue
            try:
                vec = json.loads(data.get("vector", "[]"))
                if not vec:
                    continue
                vectors.append(vec)
                meta_list.append(
                    {
                        "session_id": data.get("session_id", ""),
                        "chunk_idx": int(data.get("chunk_idx", 0)),
                        "project": data.get("project", ""),
                        "timestamp": data.get("timestamp", ""),
                        "text_preview": data.get("text", "")[:300],
                    }
                )
            except (json.JSONDecodeError, ValueError):
                continue

        if not vectors:
            return []

        # Batch cosine similarity
        scores = _cosine_similarity_batch(query_vector, vectors)

        # Pair scores with metadata
        scored = []
        for i, score in enumerate(scores):
            item = meta_list[i].copy()
            item["score"] = round(score, 4)
            scored.append(item)

        scored.sort(key=lambda x: x["score"], reverse=True)

        # Deduplicate by session_id (keep highest score per session)
        seen: set = set()
        deduped = []
        for item in scored:
            sid = item["session_id"]
            if sid not in seen:
                seen.add(sid)
                deduped.append(item)
                if len(deduped) >= limit:
                    break

        return deduped

    def generate_summary(self, session_id: str) -> str:
        """Generate session summary via Claude API (Anthropic SDK)."""
        from agentic_bridge.store import SessionStore

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
        try:
            import anthropic

            client = anthropic.Anthropic()
            response = client.messages.create(
                model=os.getenv("AGENTIC_BRIDGE_SUMMARY_MODEL", "claude-sonnet-4-5-20250929"),
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = response.content[0].text
        except ImportError:
            # Fallback: completions API
            try:
                from agentic_bridge.completions import call_completions

                result = call_completions(prompt=prompt, command="default", stateless=True)
                if result.success and result.parsed_output:
                    summary = result.parsed_output.get("result", "Summary generation failed")
                else:
                    return f"Summary generation failed: {result.error or 'unknown error'}"
            except Exception as e:
                return f"Summary generation unavailable: {e}"
        except Exception as e:
            return f"Summary generation failed: {e}"

        # Store summary in session metadata
        r = self._get_redis()
        if r is not None:
            meta_key = f"{self._prefix}:sb:session:{session_id}:meta"
            r.hset(meta_key, "summary", summary)

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
