# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

**AgentiBridge** is a standalone MCP server that indexes Claude Code CLI transcripts and exposes them via MCP tools and REST endpoints. Extracted from [agenticore](https://github.com/The-Cloud-Clock-Work/agenticore).

## Build & Development

```bash
pip install -e .
python -m agentibridge                         # stdio transport
AGENTIBRIDGE_TRANSPORT=sse python -m agentibridge  # SSE transport
docker compose up --build -d                   # full stack with Redis
pytest tests/unit -v -m unit --cov=agentibridge
ruff check agentibridge/ tests/
```

## Architecture

```
MCP Server (server.py) — 27 tools across 6 phases
  Phase 1: list/get/search sessions
  Phase 2: semantic search + summary
  Phase 3: SSE/HTTP transport + auth
  Phase 4: restore context + dispatch
  Phase 5: memory, plans, history
  Phase 6: A2A agent registry
        │
   ┌────┴────┐
   ▼         ▼
Collector   SessionStore / Registry
(daemon)    Redis + file fallback
```

## Key Modules

| Module | Purpose |
|--------|---------|
| `server.py` | FastMCP server — all MCP tools |
| `parser.py` | Pure-function JSONL transcript parser |
| `store.py` | SessionStore (Redis + filesystem fallback) |
| `collector.py` | Background polling daemon |
| `transport.py` | SSE/HTTP transport + REST endpoints |
| `registry.py` | A2A agent registry (Phase 6) |
| `oauth_provider.py` | OAuth 2.1 authorization server (opt-in) |
| `embeddings.py` | Semantic search (Phase 2) |
| `dispatch.py` | Background job dispatch + session restore |
| `dispatch_bridge.py` | Host-side HTTP bridge for Docker dispatch |
| `catalog.py` | Knowledge catalog: memory, plans, history |
| `config.py` | Configuration with validation |
| `cli.py` | CLI helper tool |

## Key Environment Variables

```bash
REDIS_URL=redis://redis:6379/0
REDIS_KEY_PREFIX=agentibridge
AGENTIBRIDGE_TRANSPORT=stdio        # or "sse"
AGENTIBRIDGE_HOST=0.0.0.0
AGENTIBRIDGE_PORT=8100
AGENTIBRIDGE_API_KEYS=              # comma-separated, empty = no auth
CLAUDE_CODE_HOME_DIR=~/.claude
AGENTIBRIDGE_POLL_INTERVAL=60
AGENTIBRIDGE_MAX_ENTRIES=500
AGENTIBRIDGE_EMBEDDING_ENABLED=false
POSTGRES_URL=postgresql://user:pass@localhost:5432/agentibridge
LLM_API_BASE=http://localhost:11434/v1
LLM_EMBED_MODEL=text-embedding-3-small
LLM_CHAT_MODEL=gpt-4o-mini
CLAUDE_BINARY=claude
DISPATCH_SECRET=
DISPATCH_BRIDGE_PORT=8101
AGENTIBRIDGE_HEARTBEAT_TTL=120      # seconds; agent goes offline if exceeded
```

## MCP Tools

### Phase 1 — Foundation
`list_sessions`, `get_session`, `get_session_segment`, `get_session_actions`, `search_sessions`, `collect_now`

### Phase 2 — Semantic Search
`search_semantic`, `generate_summary`

### Phase 4 — Write-back & Dispatch
`restore_session`, `dispatch_task`, `get_dispatch_job`, `list_dispatch_jobs`, `plan_task`, `get_dispatch_plan`, `list_dispatch_plans`, `execute_plan`

### Phase 5 — Knowledge Catalog
`list_memory_files`, `get_memory_file`, `list_plans`, `get_plan`, `search_history`

### Phase 6 — A2A Agent Registry
- `register_agent` — Upsert agent registration (capabilities as JSON string)
- `deregister_agent` — Remove agent from registry
- `heartbeat_agent` — Update heartbeat timestamp + status
- `list_agents` — List agents; filters: `agent_type`, `capability`, `status`
- `get_agent` — Full agent details by agent_id
- `find_agents` — Find agents by capability tag

## A2A Agent Registry (Phase 6)

**Module:** `agentibridge/registry.py` — `AgentRecord` dataclass, Redis storage, file fallback.

**REST endpoints** (in `transport.py`):
- `POST /agents/register`
- `POST /agents/{agent_id}/heartbeat`
- `DELETE /agents/{agent_id}`
- `GET /agents`
- `GET /agents/{agent_id}`

**Redis keys** (prefix `agentibridge:sb:`):
- `agent:{agent_id}` — Hash, TTL = `heartbeat_ttl * 2`
- `idx:agents` — Sorted set by `last_heartbeat`
- `idx:agents:cap:{capability}` — Set of agent_ids per capability

**Auto-offline:** if `last_heartbeat` age exceeds `heartbeat_ttl`, `effective_status` returns `"offline"` at read time — no writes needed.

## Related Projects

| Project | Description |
|---------|-------------|
| **agenticore** | Parent orchestration project |
| **agentihooks** | Hook system & MCP tool server for Claude Code agents |

## Redis + File Fallback Pattern

1. Try Redis via `agentibridge.redis_client` (`get_redis()` → client or `None`)
2. Fall back to reading from `~/.claude/projects/` JSONL files
3. Keys namespaced: `{REDIS_KEY_PREFIX}:sb:{key}`

## Dispatch Bridge

AgentiBridge (Docker) has no Claude CLI binary. Dispatch routes to a host process via HTTP:

```
AgentiBridge (Docker) ──HTTP──▶ Dispatch Bridge (host:8101) ──▶ claude CLI
```

- `CLAUDE_DISPATCH_URL=http://host.docker.internal:8101` — Docker compose only, never in shell env
- stdio process on host calls `claude` directly; if `CLAUDE_DISPATCH_URL` leaks into shell, dispatch fails with "Name or service not known"

## Troubleshooting

See [docs/reference/troubleshooting.md](docs/reference/troubleshooting.md). Key gotchas:
- `AGENTIBRIDGE_EMBEDDING_ENABLED=true` must be set explicitly (defaults `false`)
- `POSTGRES_PASSWORD` only takes effect on first volume init
- `agentibridge restart` does not reload `docker.env` — use `stop && run`
