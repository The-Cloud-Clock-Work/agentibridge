# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**AgentiBridge** is a standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 10 MCP tools. It was extracted from the [agenticore](https://github.com/The-Cloud-Clock-Work/agenticore) project to run independently.

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
│  10 tools across 4 phases           │
│                                     │
│  Phase 1: list/get/search sessions  │
│  Phase 2: semantic search + summary │
│  Phase 3: SSE/HTTP transport + auth │
│  Phase 4: restore context + dispatch│
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
| `agentibridge/server.py` | FastMCP server with 10 tools |
| `agentibridge/parser.py` | Pure-function JSONL transcript parser |
| `agentibridge/store.py` | SessionStore (Redis + filesystem fallback) |
| `agentibridge/collector.py` | Background polling daemon |
| `agentibridge/transport.py` | SSE/HTTP transport + API key auth |
| `agentibridge/embeddings.py` | Semantic search (Phase 2) |
| `agentibridge/dispatch.py` | Session restore + task dispatch (Phase 4) |
| `agentibridge/completions.py` | Completions API client |
| `agentibridge/redis_client.py` | Redis helper |
| `agentibridge/config.py` | Configuration with validation |
| `agentibridge/cli.py` | CLI helper tool (status/connect/install) |
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

# Logging
CLAUDE_HOOK_LOG_ENABLED=true
```

## MCP Tools (10 total)

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
- `dispatch_task` — Dispatch a task with optional session context

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
