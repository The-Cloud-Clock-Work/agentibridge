# Internal Architecture

This document provides a deep dive into AgentiBridge's internal modules and implementation patterns.

## Key Modules

| Module | Purpose | Key Functions/Classes |
|--------|---------|----------------------|
| `server.py` | FastMCP server with 10 tools | `init_server()`, tool handlers |
| `parser.py` | Pure-function JSONL transcript parser | `parse_transcript()`, `parse_entry()` |
| `store.py` | SessionStore (Redis + filesystem fallback) | `SessionStore`, `get_session()`, `list_sessions()` |
| `collector.py` | Background polling daemon | `Collector`, `collect_once()` |
| `transport.py` | SSE/HTTP transport + API key auth | `SSETransport`, auth middleware |
| `embeddings.py` | Semantic search (Phase 2) | `EmbeddingStore`, `search_vectors()` |
| `dispatch.py` | Session restore + task dispatch (Phase 4) | `restore_session()`, `dispatch_task()` |
| `claude_runner.py` | Claude CLI runner (dispatch) | `ClaudeRunner`, `run_task()` |
| `llm_client.py` | OpenAI-compatible embeddings + chat | `LLMClient`, `embed()`, `chat()` |
| `redis_client.py` | Redis helper | `get_redis()`, connection pooling |
| `pg_client.py` | Postgres + pgvector connection | `get_pg_pool()`, vector operations |
| `config.py` | Configuration with validation | `Config`, `load_config()` |
| `cli.py` | CLI helper tool | `status`, `connect`, `tunnel`, `locks` |
| `logging.py` | Structured JSON logging | `get_logger()`, request context |

## Redis + File Fallback Pattern

All stateful operations follow a consistent fallback pattern to ensure reliability even if Redis is unavailable:

### Pattern Overview

```python
# 1. Try Redis first
redis_client = get_redis()
if redis_client:
    try:
        result = redis_client.get(f"{KEY_PREFIX}:sb:{key}")
        if result:
            return json.loads(result)
    except Exception as e:
        logger.warning(f"Redis error, falling back to filesystem: {e}")

# 2. Fall back to filesystem
return read_from_jsonl_file(path)
```

### Key Characteristics

- **Graceful degradation**: If Redis is down, the bridge continues working with direct file reads
- **No partial failures**: Either operation succeeds completely or falls back
- **Namespaced keys**: All Redis keys use `{REDIS_KEY_PREFIX}:sb:{suffix}` format
- **Idempotent operations**: Safe to retry without side effects

### Redis Key Schema

```
agentibridge:sb:sessions                 # Set of all session IDs
agentibridge:sb:session:{id}             # Hash of session metadata
agentibridge:sb:transcript:{id}          # List of transcript entries (truncated)
agentibridge:sb:lock:collect:{project}   # Collection lock (prevents concurrent indexing)
agentibridge:sb:stats:{id}               # Session statistics (tool counts, etc.)
```

### When Redis is Used

- **list_sessions**: Fast ID enumeration (`SMEMBERS sessions`)
- **get_session**: Quick metadata lookup (`HGETALL session:{id}`)
- **Collector**: Locks prevent concurrent processing of same project
- **Transcript caching**: Avoids re-parsing large JSONL files on every request

### When Filesystem is Used

- **Redis unavailable**: All operations fall back to direct file reads
- **Segment queries**: Time-range filters read directly from JSONL (no caching benefit)
- **Full transcript**: If `MAX_ENTRIES=0` or not in Redis, reads from file

## Transcript Format

### File Location

Raw transcripts are stored in: `~/.claude/projects/{path-encoded}/{session-id}.jsonl`

**Path encoding example:**
- Project path: `/home/user/dev/myproject`
- Encoded name: `-home-user-dev-myproject`
- Full path: `~/.claude/projects/-home-user-dev-myproject/`

### Entry Types

Each line in the JSONL file is a JSON object with a `type` field:

**Indexed types:**
- `user` — User input (prompts, commands)
- `assistant` — Assistant responses (text, tool calls)
- `summary` — Session summary metadata
- `system` — System messages (hooks, errors)

**Filtered types (not indexed):**
- `queue-operation` — Internal task queue events
- `file-history-snapshot` — File state snapshots
- `progress` — Progress indicators

### Entry Structure

```json
{
  "type": "assistant",
  "timestamp": "2026-02-20T12:34:56.789Z",
  "content": "Let me help you with that...",
  "tool_calls": [
    {
      "name": "Read",
      "parameters": {"file_path": "/path/to/file.py"}
    }
  ]
}
```

### Parsing Logic

The `parser.py` module provides pure functions:

```python
def parse_transcript(lines: list[str]) -> dict:
    """Parse full transcript from JSONL lines."""
    entries = [parse_entry(line) for line in lines]
    return {
        "entries": [e for e in entries if e["type"] in INDEXED_TYPES],
        "tool_calls": extract_tool_calls(entries),
        "stats": compute_stats(entries)
    }
```

## Collector Daemon

### Polling Loop

```
┌─────────────────────────────┐
│ Every POLL_INTERVAL seconds │
└──────────┬──────────────────┘
           │
           ▼
    ┌──────────────┐
    │ Scan projects│
    │  directory   │
    └──────┬───────┘
           │
           ▼
    ┌──────────────────┐
    │ For each project:│
    │  - Acquire lock  │
    │  - Find new data │
    │  - Parse & index │
    │  - Release lock  │
    └──────────────────┘
```

### Lock Mechanism

Uses Redis locks to prevent concurrent indexing:

```python
lock_key = f"{KEY_PREFIX}:sb:lock:collect:{project_hash}"
if redis.set(lock_key, "1", nx=True, ex=300):  # 5-minute lock
    try:
        collect_project(project_path)
    finally:
        redis.delete(lock_key)
```

Without Redis, uses file-based locks:

```
~/.claude/projects/-home-user-dev-project/.agentibridge.lock
```

### Incremental Updates

Tracks last-processed position per session:

```python
position_key = f"{KEY_PREFIX}:sb:position:{session_id}"
last_line = redis.get(position_key) or 0
new_lines = read_jsonl_from_line(transcript_path, last_line)
redis.set(position_key, last_line + len(new_lines))
```

## Transport Layer (Phase 3)

### stdio Transport

For local MCP clients (Claude Code CLI):

```python
# Reads from stdin, writes to stdout
# Used when AGENTIBRIDGE_TRANSPORT=stdio
stdin -> MCP request -> process -> MCP response -> stdout
```

### SSE Transport

For remote MCP clients (ChatGPT, Claude Web, etc.):

```python
# HTTP server on AGENTIBRIDGE_PORT
GET  /health          -> {"status": "ok"}
GET  /sse             -> Server-Sent Events stream
POST /mcp             -> MCP request/response
```

**Authentication**: Optional API key via `X-API-Key` header or `api_key` query param.

## Embedding Pipeline (Phase 2)

### Vector Storage

```
1. Transcript entry (text)
   ↓
2. LLM API (OpenAI-compatible)
   ↓ embed()
3. Vector (e.g., 1536 dimensions)
   ↓
4. PostgreSQL + pgvector
   ↓ similarity search
5. Ranked results
```

### Schema

```sql
CREATE TABLE IF NOT EXISTS session_chunks (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    embedding vector(1536),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_session_chunks_embedding ON session_chunks
USING ivfflat (embedding vector_cosine_ops);
```

### Search Query

```python
def search_semantic(query: str, limit: int = 10) -> list[dict]:
    query_vector = llm_client.embed(query)
    results = pg.execute("""
        SELECT session_id, content,
               1 - (embedding <=> %s::vector) AS similarity
        FROM session_chunks
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (query_vector, query_vector, limit))
    return results
```

## Dispatch Architecture (Phase 4)

### Session Restore

```python
def restore_session(session_id: str) -> str:
    """Load past session context for continuation."""
    session = store.get_session(session_id)
    transcript = session["transcript"]

    # Reconstruct conversation context
    messages = [
        {"role": entry["role"], "content": entry["content"]}
        for entry in transcript
        if entry["type"] in ("user", "assistant")
    ]

    # Return formatted context for injection
    return format_context_for_claude(messages)
```

### Task Dispatch

```python
def dispatch_task(
    prompt: str,
    session_context: str | None = None,
    model: str = "sonnet"
) -> dict:
    """Dispatch a new task with optional session context."""
    runner = ClaudeRunner(
        binary=config.CLAUDE_BINARY,
        model=model,
        timeout=config.CLAUDE_DISPATCH_TIMEOUT
    )

    full_prompt = (
        f"Previous context:\n{session_context}\n\n"
        f"New task:\n{prompt}"
        if session_context else prompt
    )

    result = runner.run(full_prompt)
    return {
        "output": result.stdout,
        "exit_code": result.returncode,
        "duration": result.duration
    }
```

## Error Handling Patterns

### Graceful Fallbacks

```python
# 1. Redis unavailable? Use filesystem
# 2. Anthropic API down? Use LLM_CHAT_MODEL
# 3. Postgres down? Disable semantic search
# 4. Lock acquisition fails? Skip (will retry next cycle)
```

### Structured Errors

```python
class AgentiBridgeError(Exception):
    """Base exception with structured context."""
    def __init__(self, message: str, context: dict | None = None):
        self.message = message
        self.context = context or {}
        super().__init__(message)

# Usage
raise SessionNotFoundError(
    "Session not found",
    context={"session_id": session_id, "project": project_path}
)
```

## Performance Characteristics

### Latency Targets

- `list_sessions`: < 100ms (Redis) or < 500ms (filesystem)
- `get_session`: < 50ms (cached) or < 200ms (uncached)
- `search_sessions`: < 500ms (keyword) or < 2s (semantic)
- `collect_now`: 1-5s (depends on transcript size)

### Memory Usage

- **Redis**: ~1KB per session metadata, ~100KB per cached transcript
- **Collector**: ~50MB baseline + ~1MB per 1000 transcript entries
- **Embeddings**: ~6KB per vector (1536 dims * 4 bytes)

### Scalability Limits

- **Sessions**: Tested with 10,000+ sessions
- **Transcripts**: Individual files up to 10MB (5,000+ entries)
- **Concurrent requests**: 100+ (SSE transport)

## Development Patterns

### Adding a New Tool

1. Add handler in `server.py`:
   ```python
   @mcp.tool()
   async def my_new_tool(arg: str) -> dict:
       """Tool description for MCP registry."""
       result = await store.do_something(arg)
       return {"result": result}
   ```

2. Update `store.py` with business logic
3. Add tests in `tests/unit/test_server.py`
4. Update documentation

### Adding Configuration

1. Add to `config.py`:
   ```python
   MY_NEW_VAR: str = os.getenv("MY_NEW_VAR", "default")
   ```

2. Add validation in `Config.__post_init__()`
3. Update `docs/reference/configuration.md`
4. Add to `.env.example` generation in CLI

## See Also

- [Configuration Reference](../reference/configuration.md)
- [Semantic Search Details](semantic-search.md)
- [Remote Access Setup](remote-access.md)
- [Session Dispatch](session-dispatch.md)
