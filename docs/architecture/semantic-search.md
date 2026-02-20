# Semantic Search

AI-powered semantic search for AgentiBridge, enabling natural language queries across all indexed Claude Code transcripts. Instead of exact keyword matching, users can ask questions like "how does authentication work" and get semantically relevant results.

## Architecture

```
Query: "how does auth work?"
        |
        v
  embed_text()               <- agentibridge/llm_client.py (OpenAI-compatible API)
        |
        v
  pgvector <=> operator      <- HNSW index on transcript_chunks table
  (cosine distance)
        |
        v
  deduplicate by session     <- ROW_NUMBER() OVER (PARTITION BY session_id)
        |
        v
  ranked results             <- [{session_id, score, text_preview, project}]
```

## Components

### `agentibridge/embeddings.py` — TranscriptEmbedder

Core class for the embedding pipeline:

| Method | Description |
|--------|-------------|
| `is_available()` | Check if embedding backend and Postgres are configured |
| `embed_session(session_id)` | Chunk transcript into turns, embed each, store vectors in Postgres |
| `search_semantic(query, project, limit)` | Embed query, pgvector cosine search, return ranked matches |
| `generate_summary(session_id)` | Generate AI summary via Claude API |

### Chunking Strategy

Transcripts are chunked by **conversation turns** — each user message + its assistant response forms one chunk:

```
Chunk 0: "User: Fix the login bug\nAssistant: Looking at auth.py...\nTools used: Read, Edit\n"
Chunk 1: "User: Now add tests\nAssistant: Writing pytest cases...\nTools used: Write\n"
```

Each chunk is embedded independently and stored with metadata (session_id, project, timestamp).

### Vector Storage (Postgres + pgvector)

Vectors are stored in a `transcript_chunks` table with an HNSW index:

```sql
CREATE TABLE transcript_chunks (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL,
    chunk_idx       INTEGER NOT NULL,
    project         TEXT NOT NULL DEFAULT '',
    project_encoded TEXT NOT NULL DEFAULT '',
    timestamp       TEXT NOT NULL DEFAULT '',
    text_preview    TEXT NOT NULL DEFAULT '',
    embedding       vector(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, chunk_idx)
);

-- HNSW index for fast cosine similarity search
CREATE INDEX idx_tc_embedding_hnsw ON transcript_chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
```

### Search Algorithm

Single SQL query with pgvector cosine distance operator (`<=>`), deduplication via window function:

```sql
WITH ranked AS (
    SELECT session_id, chunk_idx, project, timestamp,
           LEFT(text_preview, 300) AS text_preview,
           1 - (embedding <=> query_vector::vector) AS score,
           ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY embedding <=> query_vector::vector) AS rn
    FROM transcript_chunks
)
SELECT ... FROM ranked WHERE rn = 1
ORDER BY score DESC LIMIT N
```

### Summary Generation

Uses the Anthropic SDK directly (or falls back to `llm_client.chat_completion()`):

1. Loads session entries from store
2. Builds readable transcript (truncated to 12K chars)
3. Sends to Claude Sonnet with summarization prompt
4. Stores result in Redis session metadata for caching

## MCP Tools Added

### `search_semantic`

```
Args: query (str), project (str, optional), limit (int, default 10)
Returns: JSON with ranked matches [{session_id, score, text_preview, project, timestamp}]
```

Requires:
- LLM API configured (`LLM_API_BASE` + `LLM_EMBED_MODEL` env vars)
- Postgres with pgvector (`POSTGRES_URL`)
- Sessions embedded via `embed_session()`

### `generate_summary`

```
Args: session_id (str)
Returns: JSON with AI-generated summary
```

Uses Claude Sonnet to produce 2-3 sentence session summaries.

## Configuration

```bash
# Postgres + pgvector (required for vector storage)
POSTGRES_URL=postgresql://agentibridge:agentibridge@localhost:5432/agentibridge
PGVECTOR_DIMENSIONS=1536

# Enable/disable embedding (default: false — opt-in)
AGENTIBRIDGE_EMBEDDING_ENABLED=false

# OpenAI-compatible API for embeddings + chat
LLM_API_BASE=http://localhost:11434/v1
LLM_API_KEY=your-api-key
LLM_EMBED_MODEL=text-embedding-3-small
LLM_CHAT_MODEL=gpt-4o-mini

# Required for summary generation (preferred over LLM_CHAT_MODEL):
ANTHROPIC_API_KEY=...
```

## Dependencies

- `agentibridge.llm_client` — `embed_text()` and `chat_completion()` (OpenAI-compatible API)
- `psycopg` + `psycopg-pool` — Postgres connection pool (required for vector storage)
- `pgvector` — Postgres extension for vector similarity search (installed in Postgres, not Python)
- `anthropic` — optional, for summary generation (falls back to `llm_client.chat_completion()`)
