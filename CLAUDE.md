# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**AgentiBridge** is a standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 16 MCP tools. It was extracted from the [agenticore](https://github.com/The-Cloud-Clock-Work/agenticore) project to run independently.

## Build & Development

```bash
# Install dependencies
pip install -e .

# Run locally (stdio transport — for Claude Code CLI)
python -m agentibridge

# Run with SSE transport (for remote MCP clients)
AGENTIBRIDGE_TRANSPORT=sse python -m agentibridge

# Docker (full stack with Redis)
docker compose up --build -d

# Run unit tests
pytest tests/unit -v -m unit --cov=agentibridge

# Lint + format
ruff check agentibridge/ tests/
ruff format --check agentibridge/ tests/

# Run integration tests (requires Docker)
python tests/integration/test_docker.py --start
python tests/integration/test_docker.py --test
python tests/integration/test_docker.py --stop

# CLI
agentibridge version
agentibridge status
agentibridge help
```

## Architecture

```
┌─────────────────────────────────────┐
│  MCP Server (server.py)             │
│  16 tools across 5 phases           │
│                                     │
│  Phase 1: list/get/search sessions  │
│  Phase 2: semantic search + summary │
│  Phase 3: SSE/HTTP transport + auth │
│  Phase 4: restore context + dispatch│
│  Phase 5: memory, plans, history    │
└─────────────┬───────────────────────┘
              │
    ┌─────────┴─────────┐
    │                   │
    ▼                   ▼
┌──────────┐    ┌──────────────┐
│ Collector│    │ SessionStore │
│ (daemon) │───▶│ Redis + file │
│ polls    │    │ fallback     │
│ ~/.claude│    └──────────────┘
└──────────┘
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `agentibridge/server.py` | FastMCP server with 16 tools |
| `agentibridge/parser.py` | Pure-function JSONL transcript parser |
| `agentibridge/store.py` | SessionStore (Redis + filesystem fallback) |
| `agentibridge/collector.py` | Background polling daemon |
| `agentibridge/transport.py` | SSE/HTTP transport + API key auth |
| `agentibridge/oauth_provider.py` | OAuth 2.1 authorization server (opt-in) |
| `agentibridge/embeddings.py` | Semantic search (Phase 2) |
| `agentibridge/dispatch.py` | Background job dispatch, session restore (Phase 4) |
| `agentibridge/dispatch_bridge.py` | Host-side HTTP bridge for Docker dispatch |
| `agentibridge/claude_runner.py` | Claude CLI runner (dispatch) |
| `agentibridge/llm_client.py` | OpenAI-compatible embeddings + chat |
| `agentibridge/redis_client.py` | Redis helper |
| `agentibridge/pg_client.py` | Postgres + pgvector connection |
| `agentibridge/config.py` | Configuration with validation |
| `agentibridge/cli.py` | CLI helper tool (status/connect/install) |
| `agentibridge/catalog.py` | Knowledge catalog: memory, plans, history (Phase 5) |
| `agentibridge/logging.py` | Structured JSON logging |

## Key Environment Variables

```bash
# Redis
REDIS_URL=redis://redis:6379/0
REDIS_KEY_PREFIX=agentibridge

# Transport
AGENTIBRIDGE_TRANSPORT=stdio    # or "sse"
AGENTIBRIDGE_HOST=0.0.0.0
AGENTIBRIDGE_PORT=8100
AGENTIBRIDGE_API_KEYS=          # comma-separated, empty = no auth

# Collector
AGENTIBRIDGE_POLL_INTERVAL=60
AGENTIBRIDGE_MAX_ENTRIES=500

# Semantic search (Phase 2)
AGENTIBRIDGE_EMBEDDING_ENABLED=false  # set to "true" to enable (requires POSTGRES_URL + LLM config)

# Postgres + pgvector (vector storage for semantic search)
POSTGRES_URL=postgresql://DB_USER:DB_PASSWORD@localhost:5432/agentibridge
PGVECTOR_DIMENSIONS=1536

# Embeddings + LLM (OpenAI-compatible API)
LLM_API_BASE=http://localhost:11434/v1
LLM_API_KEY=
LLM_EMBED_MODEL=text-embedding-3-small
LLM_CHAT_MODEL=gpt-4o-mini

# Summary generation (Anthropic SDK preferred, falls back to LLM_CHAT_MODEL)
ANTHROPIC_API_KEY=

# Dispatch (Claude CLI)
CLAUDE_BINARY=claude
CLAUDE_DISPATCH_MODEL=sonnet
CLAUDE_DISPATCH_TIMEOUT=300

# Dispatch bridge (Docker mode — host-side proxy for Claude CLI)
DISPATCH_SECRET=                # shared secret for bridge auth
DISPATCH_BRIDGE_PORT=8101       # port the dispatch bridge listens on

# Cloudflare Tunnel (optional — use docker compose --profile tunnel)
CLOUDFLARE_TUNNEL_TOKEN=        # set for named tunnel; leave empty for quick tunnel

# Knowledge Catalog (Phase 5)
AGENTIBRIDGE_PLANS_DIR=~/.claude/plans
AGENTIBRIDGE_HISTORY_FILE=~/.claude/history.jsonl
AGENTIBRIDGE_MAX_HISTORY_ENTRIES=5000
AGENTIBRIDGE_MAX_MEMORY_CONTENT=51200
AGENTIBRIDGE_MAX_PLAN_CONTENT=102400

# Logging
CLAUDE_HOOK_LOG_ENABLED=true
```

## MCP Tools (16 total)

### Phase 1 — Foundation
- `list_sessions` — List sessions across all projects
- `get_session` — Get full session metadata + transcript
- `get_session_segment` — Paginated/time-range transcript retrieval
- `get_session_actions` — Extract tool calls with counts
- `search_sessions` — Keyword search across transcripts
- `collect_now` — Trigger immediate collection

### Phase 2 — Semantic Search
- `search_semantic` — Semantic search using embeddings
- `generate_summary` — Auto-generate session summary via LLM

### Phase 4 — Write-back & Dispatch
- `restore_session` — Load session context for continuation
- `dispatch_task` — Fire-and-forget background job dispatch (returns job_id immediately)
- `get_dispatch_job` — Poll a background job for status and output

### Phase 5 — Knowledge Catalog
- `list_memory_files` — List memory files across projects
- `get_memory_file` — Read a specific memory file
- `list_plans` — List plans sorted by recency
- `get_plan` — Read a plan by codename (with optional agent subplans)
- `search_history` — Search the global prompt history

## Related Projects

| Project | Repo | Description |
|---------|------|-------------|
| **agenticore** | [The-Cloud-Clock-Work/agenticore](https://github.com/The-Cloud-Clock-Work/agenticore) | The parent orchestration project AgentiBridge was extracted from |
| **agentihooks** | [The-Cloud-Clock-Work/agentihooks](https://github.com/The-Cloud-Clock-Work/agentihooks) | Hook system & MCP tool server for Claude Code agents, designed for agenticore |

## Redis + File Fallback Pattern

All stateful operations follow a consistent pattern:
1. Try Redis via `agentibridge.redis_client` (`get_redis()` returns client or `None`)
2. Fall back to reading directly from `~/.claude/projects/` JSONL files
3. Redis keys are namespaced: `{REDIS_KEY_PREFIX}:sb:{key}`

## Claude CLI Transcript Format

Raw transcripts live in `~/.claude/projects/{path-encoded}/` as `.jsonl` files:
- **Path encoding**: `/home/user/dev/project` -> `-home-user-dev-project`
- **Entry types**: `user`, `assistant`, `summary`, `system`
- **Filtered types**: `queue-operation`, `file-history-snapshot`, `progress`
