# Agentic Bridge

Standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 10 tools across 4 phases. Any Claude Code session, ChatGPT, or other AI client can connect and ask "what have my agents been doing?"

## TL;DR — Fastest Start

```bash
git clone https://github.com/The-Cloud-Clock-Work/agentic-bridge.git
cd agentic-bridge
docker compose up --build -d
curl http://localhost:8100/health
# {"status": "ok", "service": "session-bridge"}
```

Add to your `~/.mcp.json` and start using the 10 MCP tools:

```json
{
  "mcpServers": {
    "session-bridge": {
      "url": "http://localhost:8100/sse"
    }
  }
}
```

Done. Your Claude Code sessions are now searchable.

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

## Installation Options

### Option 1: Docker Compose (recommended)

```bash
git clone https://github.com/The-Cloud-Clock-Work/agentic-bridge.git
cd agentic-bridge
docker compose up --build -d
```

Separate containers for app and Redis. The `docker-compose.yml` mounts `~/.claude/projects` read-only and starts session-bridge on port `8100`.

### Option 2: All-in-One Docker

```bash
docker run -d -p 8100:8100 \
  -v ~/.claude/projects:/home/appuser/.claude/projects:ro \
  --name agentic-bridge \
  ghcr.io/the-cloud-clock-work/agentic-bridge:allinone-latest
```

Single container with embedded Redis. No external dependencies.

### Option 3: pip install (local/development)

```bash
pip install -e .
python -m agentic_bridge          # stdio transport (local MCP)

# Or with SSE for remote clients:
SESSION_BRIDGE_TRANSPORT=sse python -m agentic_bridge
```

### Option 4: systemd service (auto-start on boot)

```bash
pip install -e .
agentic-bridge install --docker    # Docker-based
# or
agentic-bridge install --native    # Native Python
```

## Expose via Cloudflare Tunnel

Access your bridge from anywhere — no port forwarding needed.

### Quick tunnel (no Cloudflare account)

```bash
docker compose --profile tunnel up -d
agentic-bridge tunnel   # prints the public URL
```

### Named tunnel (persistent hostname)

Use the interactive setup script to create a named tunnel with a stable subdomain:

```bash
chmod +x automation/cloudfared.sh
./automation/cloudfared.sh
```

The script is idempotent — safe to re-run. It installs `cloudflared`, authenticates, creates the tunnel, routes DNS, writes the config, and optionally installs a systemd service.

Alternatively, set a tunnel token and use Docker:

```bash
CLOUDFLARE_TUNNEL_TOKEN=xxx docker compose --profile tunnel-named up -d
```

See [docs/cloudflare-tunnel.md](docs/cloudflare-tunnel.md) for full details.

## Connect Your AI Client

### Claude Code CLI

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "session-bridge": {
      "url": "http://localhost:8100/sse",
      "headers": {"X-API-Key": "your-key"}
    }
  }
}
```

### Other Clients

Run `agentic-bridge connect` for ready-to-paste configs for ChatGPT, Claude Web, Grok, and generic MCP clients.

See [docs/connecting-clients.md](docs/connecting-clients.md) for detailed setup instructions.

## MCP Tools (10 total)

| Phase | Tool | Description |
|-------|------|-------------|
| 1 | `list_sessions` | List sessions across all projects |
| 1 | `get_session` | Get full session metadata + transcript |
| 1 | `get_session_segment` | Paginated/time-range transcript retrieval |
| 1 | `get_session_actions` | Extract tool calls with counts |
| 1 | `search_sessions` | Keyword search across transcripts |
| 1 | `collect_now` | Trigger immediate collection |
| 2 | `search_semantic` | Semantic search using embeddings |
| 2 | `generate_summary` | AI-generated session summary |
| 4 | `restore_session` | Load session context for continuation |
| 4 | `dispatch_task` | Dispatch task with optional session context |

## CLI

```bash
agentic-bridge version              # Print version
agentic-bridge status               # Service status, Redis, session count
agentic-bridge help                 # Tools reference, config guide
agentic-bridge connect              # Connection strings for all clients
agentic-bridge tunnel               # Cloudflare Tunnel status and URL
agentic-bridge config               # Current config dump
agentic-bridge config --generate-env  # Generate .env template
agentic-bridge locks                # Show Redis keys, file locks, bridge resources
agentic-bridge locks --clear        # Clear position locks (forces re-index)
agentic-bridge install --docker     # Install as systemd service (Docker)
agentic-bridge install --native     # Install as systemd service (native)
agentic-bridge uninstall            # Remove systemd service
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | _(none)_ | Redis connection URL (optional — falls back to filesystem) |
| `REDIS_KEY_PREFIX` | `agenticore` | Redis key namespace |
| `SESSION_BRIDGE_TRANSPORT` | `stdio` | `stdio` (local MCP) or `sse` (HTTP remote) |
| `SESSION_BRIDGE_HOST` | `127.0.0.1` | Bind address for SSE transport |
| `SESSION_BRIDGE_PORT` | `8100` | HTTP port for SSE transport |
| `SESSION_BRIDGE_API_KEYS` | _(none)_ | Comma-separated API keys (empty = no auth) |
| `SESSION_BRIDGE_POLL_INTERVAL` | `60` | Collector poll interval in seconds (min: 5) |
| `SESSION_BRIDGE_MAX_ENTRIES` | `500` | Max entries per session in Redis (0 = unlimited) |
| `SESSION_BRIDGE_PROJECTS_DIR` | `~/.claude/projects` | Claude transcript directory |
| `EMBEDDING_BACKEND` | _(none)_ | `ollama` or `bedrock` for semantic search |
| `AGENTIC_BRIDGE_SUMMARY_MODEL` | `claude-sonnet-4-5-20250929` | Model for AI summaries |
| `CLAUDE_HOOK_LOG_ENABLED` | `true` | Enable/disable logging |
| `AGENTIC_BRIDGE_LOG_FILE` | _auto_ | Log file path (auto-detects Docker vs native) |

Generate a `.env` template: `agentic-bridge config --generate-env`

## Key Modules

| Module | Purpose |
|--------|---------|
| `server.py` | FastMCP server with 10 tools |
| `parser.py` | Pure-function JSONL transcript parser |
| `store.py` | SessionStore (Redis + filesystem fallback) |
| `collector.py` | Background polling daemon |
| `transport.py` | SSE/HTTP transport + API key auth |
| `embeddings.py` | Semantic search (Phase 2) |
| `dispatch.py` | Session restore + task dispatch (Phase 4) |
| `completions.py` | Completions API client |
| `redis_client.py` | Redis helper |
| `config.py` | Configuration with validation |
| `cli.py` | CLI helper tool (status, locks, connect, tunnel) |
| `logging.py` | Structured JSON logging |

### Redis + File Fallback

All stateful operations follow a consistent pattern:
1. Try Redis via `get_redis()` (returns client or `None`)
2. Fall back to reading directly from `~/.claude/projects/` JSONL files
3. Redis keys are namespaced: `{REDIS_KEY_PREFIX}:sb:{key}`

### Transcript Format

Raw transcripts live in `~/.claude/projects/{path-encoded}/` as `.jsonl` files:
- **Path encoding**: `/home/user/dev/project` → `-home-user-dev-project`
- **Entry types**: `user`, `assistant`, `summary`, `system`
- **Filtered types**: `queue-operation`, `file-history-snapshot`, `progress`

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run unit tests (365 tests)
pytest tests/unit -v -m unit --cov=agentic_bridge

# Run lint + format check
ruff check agentic_bridge/ tests/
ruff format --check agentic_bridge/ tests/

# Run stress tests
pytest tests/stress -v -m stress
```

### Integration Tests

Docker-based integration tests validate the full stack (app + Redis):

```bash
python tests/integration/test_docker.py --start
python tests/integration/test_docker.py --test
python tests/integration/test_docker.py --stop
```

### E2E Smoke Tests

End-to-end tests that call all 6 Phase 1 MCP tools via the Claude CLI against a live bridge:

```bash
# Requires: claude CLI, .mcp.json with session-bridge config, running bridge
./tests/e2e/test_mcp_smoke.sh
```

These also run on a daily schedule via GitHub Actions (`e2e-smoke.yml`).

### Automation

| Script | Purpose |
|--------|---------|
| `automation/cloudfared.sh` | Idempotent Cloudflare Tunnel setup (install, auth, create, DNS route, config, systemd) |

## CI/CD

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| `test.yml` | Push/PR | Unit tests (Python 3.11 + 3.12 matrix), lint (ruff) |
| `build.yml` | Push to main | Builds Docker images → GHCR (standard + all-in-one) |
| `e2e-smoke.yml` | Daily + manual | Runs 6 MCP tool smoke tests via Claude CLI against live tunnel |
| `claude.yml` | Issue/PR comments | Claude Code integration for automated code review |

## Documentation

- [Connecting Clients](docs/connecting-clients.md) — Setup guides for Claude Code, ChatGPT, Claude Web, Grok
- [Cloudflare Tunnel](docs/cloudflare-tunnel.md) — Expose to internet securely (quick & named tunnels)
- [Reverse Proxy](docs/reverse-proxy.md) — Nginx, Caddy, Cloudflare Tunnel, Traefik configs
- [Phase 2: Semantic Search](docs/phase2-semantic-search.md) — Embedding backends and semantic search
- [Phase 3: Remote Access](docs/phase3-remote-access.md) — SSE/HTTP transport and API key auth
- [Phase 4: Dispatch](docs/phase4-dispatch.md) — Session restore and task dispatch

## License

MIT
