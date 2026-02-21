# Configuration Reference

This document provides a comprehensive reference for all AgentiBridge configuration options.

## Environment Variables

### Redis Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | _(none)_ | Redis connection URL (e.g., `redis://localhost:6379/0`). If not set, falls back to filesystem-only storage |
| `REDIS_KEY_PREFIX` | `agentibridge` | Namespace prefix for all Redis keys (format: `{prefix}:sb:{key}`) |

### Transport Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTIBRIDGE_TRANSPORT` | `stdio` | Transport mode: `stdio` (local MCP via stdin/stdout) or `sse` (HTTP/SSE for remote clients) |
| `AGENTIBRIDGE_HOST` | `127.0.0.1` | Bind address for SSE transport. Use `0.0.0.0` to accept connections from any interface |
| `AGENTIBRIDGE_PORT` | `8100` | HTTP port for SSE transport |
| `AGENTIBRIDGE_API_KEYS` | _(none)_ | Comma-separated list of API keys for authentication. Empty = no auth required |

### Collector Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTIBRIDGE_POLL_INTERVAL` | `60` | How often the collector scans for new transcript data (seconds). Minimum: 5 |
| `AGENTIBRIDGE_MAX_ENTRIES` | `500` | Maximum transcript entries to store per session in Redis. `0` = unlimited (use with caution) |
| `AGENTIBRIDGE_PROJECTS_DIR` | `~/.claude/projects` | Directory where Claude Code stores session transcripts |

### Database Configuration (Phase 2)

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTIBRIDGE_EMBEDDING_ENABLED` | `false` | Enable semantic search. Requires `POSTGRES_URL` and LLM embedding config. Must be explicitly set to `true` |
| `POSTGRES_URL` | _(none)_ | PostgreSQL connection URL with pgvector extension (e.g., `postgresql://user:pass@localhost:5432/agentibridge`). Also accepted as `DATABASE_URL` |
| `PGVECTOR_DIMENSIONS` | `1536` | Embedding vector dimensions. Must match your embedding model (e.g., 1536 for `text-embedding-3-small`) |

### LLM Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | _(none)_ | Anthropic API key for session summary generation (preferred). Uses Claude via official SDK |
| `LLM_API_BASE` | _(none)_ | OpenAI-compatible API base URL for embeddings and chat (e.g., `http://localhost:11434/v1` for Ollama) |
| `LLM_API_KEY` | _(none)_ | API key for the LLM endpoint |
| `LLM_EMBED_MODEL` | _(none)_ | Embedding model name (e.g., `text-embedding-3-small`, `mxbai-embed-large`) |
| `LLM_CHAT_MODEL` | _(none)_ | Chat model for summaries if `ANTHROPIC_API_KEY` is not set (e.g., `gpt-4o-mini`, `llama3`) |
| `CF_ACCESS_CLIENT_ID` | _(none)_ | Cloudflare Access service-token ID. Adds `CF-Access-Client-Id` header to LLM API requests |
| `CF_ACCESS_CLIENT_SECRET` | _(none)_ | Cloudflare Access service-token secret. Adds `CF-Access-Client-Secret` header to LLM API requests |

### OAuth 2.1 Configuration (Optional)

AgentiBridge supports OAuth 2.1 for MCP clients that require it (e.g., claude.ai). Set `OAUTH_ISSUER_URL` to enable.

| Variable | Default | Description |
|----------|---------|-------------|
| `OAUTH_ISSUER_URL` | _(none)_ | OAuth issuer URL. **Setting this enables OAuth 2.1.** Example: `https://bridge.example.com` |
| `OAUTH_RESOURCE_URL` | `{issuer}/mcp` | OAuth resource server URL. Defaults to `{OAUTH_ISSUER_URL}/mcp` |
| `OAUTH_CLIENT_ID` | _(none)_ | Pre-configured client ID. When set, disables dynamic client registration |
| `OAUTH_CLIENT_SECRET` | _(none)_ | Pre-configured client secret. Required when `OAUTH_CLIENT_ID` is set |
| `OAUTH_ALLOWED_REDIRECT_URIS` | _(none)_ | Comma-separated allowed redirect URIs for the pre-configured client |
| `OAUTH_ALLOWED_SCOPES` | _(none)_ | Space-separated OAuth scopes the client may request (e.g., `claudeai`) |

When OAuth is enabled, `AGENTIBRIDGE_API_KEYS` still works as a fallback — Bearer tokens matching an API key are accepted.

### Dispatch Configuration (Phase 4)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_BINARY` | `claude` | Path to Claude Code CLI binary. Use absolute path if not in `$PATH` |
| `CLAUDE_DISPATCH_MODEL` | `sonnet` | Model to use for dispatched tasks. Options: `sonnet`, `opus`, `haiku` |
| `CLAUDE_DISPATCH_TIMEOUT` | `300` | Maximum execution time for dispatched tasks (seconds) |
| `CLAUDE_DISPATCH_URL` | _(none)_ | Bridge URL for Docker mode (e.g., `http://host.docker.internal:8101`). Empty = local subprocess mode |
| `DISPATCH_SECRET` | _(none)_ | Shared secret sent from the container to the dispatch bridge |
| `DISPATCH_BRIDGE_HOST` | `0.0.0.0` | Bind address for the host-side dispatch bridge |
| `DISPATCH_BRIDGE_PORT` | `8101` | Port for the host-side dispatch bridge |

### Logging Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_HOOK_LOG_ENABLED` | `true` | Enable or disable structured JSON logging |
| `AGENTIBRIDGE_LOG_FILE` | _auto_ | Log file path. Auto-detects: `/app/logs/agentibridge.log` (Docker) or `~/.cache/agentibridge/agentibridge.log` (native) |

## Configuration Profiles

### Minimal Setup (Local Only)

```bash
# No configuration needed - just run:
docker compose up -d
```

Uses defaults: Redis on `redis://redis:6379/0`, HTTP on `localhost:8100`, no authentication.

### Remote Access Setup

```bash
AGENTIBRIDGE_TRANSPORT=sse
AGENTIBRIDGE_HOST=0.0.0.0
AGENTIBRIDGE_PORT=8100
AGENTIBRIDGE_API_KEYS=secret-key-1,secret-key-2
```

Enables HTTP/SSE transport with API key authentication for remote MCP clients.

### Semantic Search Setup (Phase 2)

```bash
# Enable semantic search (required opt-in)
AGENTIBRIDGE_EMBEDDING_ENABLED=true

# Database
POSTGRES_URL=postgresql://agentibridge:password@localhost:5432/agentibridge
PGVECTOR_DIMENSIONS=1536

# Embeddings
LLM_API_BASE=http://localhost:11434/v1
LLM_EMBED_MODEL=text-embedding-3-small
```

Enables `search_semantic` tool with vector similarity search. `AGENTIBRIDGE_EMBEDDING_ENABLED=true` must be set explicitly — embeddings are off by default.

### Full Production Setup

```bash
# Redis
REDIS_URL=redis://redis:6379/0
REDIS_KEY_PREFIX=agentibridge

# Transport
AGENTIBRIDGE_TRANSPORT=sse
AGENTIBRIDGE_HOST=0.0.0.0
AGENTIBRIDGE_PORT=8100
AGENTIBRIDGE_API_KEYS=prod-key-xyz

# Collector
AGENTIBRIDGE_POLL_INTERVAL=30
AGENTIBRIDGE_MAX_ENTRIES=1000

# Database (semantic search)
AGENTIBRIDGE_EMBEDDING_ENABLED=true
POSTGRES_URL=postgresql://agentibridge:secure-password@postgres:5432/agentibridge
PGVECTOR_DIMENSIONS=1536

# LLM
ANTHROPIC_API_KEY=sk-ant-xxxxx
LLM_API_BASE=http://ollama:11434/v1
LLM_EMBED_MODEL=text-embedding-3-small

# Dispatch
CLAUDE_BINARY=/usr/local/bin/claude
CLAUDE_DISPATCH_MODEL=sonnet
CLAUDE_DISPATCH_TIMEOUT=600

# Logging
CLAUDE_HOOK_LOG_ENABLED=true
```

## CLI Configuration Commands

### View Current Configuration

```bash
agentibridge config
```

Shows all active configuration values, including defaults.

### Generate .env Template

```bash
agentibridge config --generate-env
```

Creates a `.env.example` file with all available options and descriptions.

### Validate Configuration

```bash
agentibridge status
```

Checks service health, Redis connectivity, and session count.

## Redis Key Structure

All Redis keys follow the pattern: `{REDIS_KEY_PREFIX}:sb:{suffix}`

**Common keys:**
- `{prefix}:sb:idx:all` — Sorted set of all session IDs (score = last_update timestamp)
- `{prefix}:sb:idx:project:{encoded}` — Sorted set of session IDs per project
- `{prefix}:sb:session:{id}:meta` — Hash of session metadata fields
- `{prefix}:sb:session:{id}:entries` — List of JSON-serialized transcript entries (capped at `AGENTIBRIDGE_MAX_ENTRIES`)
- `{prefix}:sb:pos:{filepath_hash}` — String: byte offset for incremental transcript reading

## Docker Compose Overrides

The `docker-compose.yml` sets these defaults:

```yaml
environment:
  REDIS_URL: redis://redis:6379/0
  AGENTIBRIDGE_TRANSPORT: sse
  AGENTIBRIDGE_HOST: 0.0.0.0
  AGENTIBRIDGE_PORT: 8100
```

Override by creating a `.env` file in the project root or exporting variables before running `docker compose`.

## See Also

- [Architecture Overview](../architecture/internals.md)
- [Remote Access Setup](../architecture/remote-access.md)
- [Semantic Search](../architecture/semantic-search.md)
