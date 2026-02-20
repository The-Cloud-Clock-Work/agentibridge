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
  cosine_similarity()        <- brute-force over stored vectors (numpy-accelerated)
        |
        v
  deduplicate by session     <- one result per session (highest score)
        |
        v
  ranked results             <- [{session_id, score, text_preview, project}]
```

## Components

### `agentibridge/embeddings.py` — TranscriptEmbedder

Core class for the embedding pipeline:

| Method | Description |
|--------|-------------|
| `is_available()` | Check if embedding backend is configured |
| `embed_session(session_id)` | Chunk transcript into turns, embed each, store vectors |
| `search_semantic(query, project, limit)` | Embed query, cosine search, return ranked matches |
| `generate_summary(session_id)` | Generate AI summary via Claude API |

### Chunking Strategy

Transcripts are chunked by **conversation turns** — each user message + its assistant response forms one chunk:

```
Chunk 0: "User: Fix the login bug\nAssistant: Looking at auth.py...\nTools used: Read, Edit\n"
Chunk 1: "User: Now add tests\nAssistant: Writing pytest cases...\nTools used: Write\n"
```

Each chunk is embedded independently and stored with metadata (session_id, project, timestamp).

### Vector Storage (Redis)

Vectors are stored in Redis hashes with a set index:

```
{prefix}:sb:vec:{session_id}:{chunk_idx}  -> Hash {session_id, chunk_idx, project, timestamp, text, vector}
{prefix}:sb:vec:idx                        -> Set of all vector keys
{prefix}:sb:vec:proj:{project_encoded}     -> Set of vector keys per project
{prefix}:sb:vec:embedded_sessions          -> Set of session IDs that have been embedded
```

### Search Algorithm

1. **Embed query** via `agentibridge.llm_client.embed_text()` (OpenAI-compatible API)
2. **Load all vectors** from Redis via pipeline (batched for performance)
3. **Cosine similarity** computed in batch (numpy when available, pure Python fallback)
4. **Deduplicate** by session_id, keeping the highest-scoring chunk per session
5. **Return** top-K results sorted by score

For ~5000 vectors (500 sessions x 10 chunks), search takes <100ms with numpy.

### Summary Generation

Uses the Anthropic SDK directly (or falls back to `llm_client.chat_completion()`):

1. Loads session entries from store
2. Builds readable transcript (truncated to 12K chars)
3. Sends to Claude Sonnet with summarization prompt
4. Stores result in session metadata for caching

## MCP Tools Added

### `search_semantic`

```
Args: query (str), project (str, optional), limit (int, default 10)
Returns: JSON with ranked matches [{session_id, score, text_preview, project, timestamp}]
```

Requires:
- LLM API configured (`LLM_API_BASE` + `LLM_EMBED_MODEL` env vars)
- Redis available
- Sessions embedded via `embed_session()`

### `generate_summary`

```
Args: session_id (str)
Returns: JSON with AI-generated summary
```

Uses Claude Sonnet to produce 2-3 sentence session summaries.

## Configuration

```bash
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
- `numpy` — optional, for batch cosine similarity acceleration
- `anthropic` — optional, for summary generation (falls back to `llm_client.chat_completion()`)
- Redis — required for vector storage (no file fallback for vectors)
