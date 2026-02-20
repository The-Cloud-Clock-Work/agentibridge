# AgentiBridge

Standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 10 tools across 4 phases. Any Claude Code session, ChatGPT, or other AI client can connect and ask "what have my agents been doing?"

## TL;DR — Fastest Start

### Local only (same machine)

```bash
git clone https://github.com/The-Cloud-Clock-Work/agentibridge.git
cd agentibridge
docker compose up --build -d
curl http://localhost:8100/health
```

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": { "url": "http://localhost:8100/sse" }
  }
}
```

### With a public domain (access from anywhere)

If you want a persistent URL like `https://mcp.yourdomain.com`, set up the Cloudflare Tunnel **first**, then start the bridge:

```bash
git clone https://github.com/The-Cloud-Clock-Work/agentibridge.git
cd agentibridge
./automation/cloudfared.sh          # 1. sets up tunnel + DNS (interactive)
docker compose up --build -d        # 2. starts the bridge on :8100
curl https://mcp.yourdomain.com/health
```

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": { "url": "https://mcp.yourdomain.com/mcp" }
  }
}
```

The domain is **not** configured in the bridge itself — it lives in the Cloudflare Tunnel config (`~/.cloudflared/config.yml`), which the setup script writes for you. The bridge just listens on `localhost:8100` and the tunnel routes your domain to it.

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
git clone https://github.com/The-Cloud-Clock-Work/agentibridge.git
cd agentibridge
docker compose up --build -d
```

Separate containers for app and Redis. The `docker-compose.yml` mounts `~/.claude/projects` read-only and starts agentibridge on port `8100`.

### Option 2: pip install (local/development)

```bash
pip install -e .
python -m agentibridge          # stdio transport (local MCP)

# Or with SSE for remote clients:
AGENTIBRIDGE_TRANSPORT=sse python -m agentibridge
```

### Option 3: systemd service (auto-start on boot)

```bash
pip install -e .
agentibridge install --docker    # Docker-based
# or
agentibridge install --native    # Native Python
```

## Expose via Cloudflare Tunnel

Access your bridge from anywhere — no port forwarding, no public IP needed. Cloudflare handles TLS and routing.

> **Where does the domain live?** The bridge has no domain config — it only knows about `localhost:8100`. Your domain is configured entirely in Cloudflare's tunnel config (`~/.cloudflared/config.yml`), which maps `mcp.yourdomain.com` → `localhost:8100`. The setup script writes this for you.

### Quick tunnel (no Cloudflare account)

Temporary `*.trycloudflare.com` URL that changes on every restart. Good for testing.

```bash
docker compose --profile tunnel up -d
agentibridge tunnel   # prints the public URL
```

### Named tunnel (persistent hostname)

For a stable URL like `https://mcp.yourdomain.com` that survives restarts.

**Prerequisites:** A [Cloudflare account](https://dash.cloudflare.com/sign-up) with at least one domain added.

**Run the setup script BEFORE `docker compose up`:**

```bash
chmod +x automation/cloudfared.sh
./automation/cloudfared.sh
```

The script walks you through 10 steps interactively:

| Step | What it does | What you provide |
|------|-------------|-----------------|
| 1 | Installs `cloudflared` binary (Linux/macOS) | Nothing — skips if already installed |
| 2 | Authenticates with Cloudflare | Opens your browser to log in (one-time) |
| 3 | Prompts for tunnel name | A name, or press Enter for `agentibridge` |
| 4 | Creates the tunnel | Nothing — skips if it already exists |
| 5 | Prompts for subdomain | **Required** — e.g. `mcp` |
| 6 | Prompts for domain | **Required** — e.g. `yourdomain.com` |
| 7 | Creates DNS CNAME route | Automatic — `mcp.yourdomain.com` → tunnel |
| 8 | Writes `~/.cloudflared/config.yml` | Nothing — backs up existing config if different |
| 9 | Optionally installs systemd service | Answer y/N |
| 10 | Health check | Validates the tunnel is reachable |

The generated config looks like this:

```yaml
# ~/.cloudflared/config.yml (written by the script)
tunnel: <tunnel-uuid>
credentials-file: ~/.cloudflared/<tunnel-uuid>.json

ingress:
  - hostname: mcp.yourdomain.com
    service: http://localhost:8100    # ← points at your bridge
  - service: http_status:404
```

The script is **idempotent** — safe to re-run. If everything is already set up, every step shows "already exists / skipping".

**After the script finishes**, start the bridge:

```bash
docker compose up --build -d
curl https://mcp.yourdomain.com/health
# {"status": "ok", "service": "agentibridge"}
```

**Alternative (Docker-only):** If you already created a tunnel in the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/), pass the token directly:

```bash
CLOUDFLARE_TUNNEL_TOKEN=xxx docker compose --profile tunnel-named up -d
```

See [Cloudflare Tunnel](docs/deployment/cloudflare-tunnel.md) for full details.

## Connect Your AI Client

### Claude Code CLI

Add to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": {
      "url": "http://localhost:8100/sse",
      "headers": {"X-API-Key": "your-key"}
    }
  }
}
```

### Other Clients

Run `agentibridge connect` for ready-to-paste configs for ChatGPT, Claude Web, Grok, and generic MCP clients.

See [Connecting Clients](docs/getting-started/connecting-clients.md) for detailed setup instructions.

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
agentibridge version              # Print version
agentibridge status               # Service status, Redis, session count
agentibridge help                 # Tools reference, config guide
agentibridge connect              # Connection strings for all clients
agentibridge tunnel               # Cloudflare Tunnel status and URL
agentibridge config               # Current config dump
agentibridge config --generate-env  # Generate .env template
agentibridge locks                # Show Redis keys, file locks, bridge resources
agentibridge locks --clear        # Clear position locks (forces re-index)
agentibridge install --docker     # Install as systemd service (Docker)
agentibridge install --native     # Install as systemd service (native)
agentibridge uninstall            # Remove systemd service
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `REDIS_URL` | _(none)_ | Redis connection URL (optional — falls back to filesystem) |
| `REDIS_KEY_PREFIX` | `agentibridge` | Redis key namespace |
| `AGENTIBRIDGE_TRANSPORT` | `stdio` | `stdio` (local MCP) or `sse` (HTTP remote) |
| `AGENTIBRIDGE_HOST` | `127.0.0.1` | Bind address for SSE transport |
| `AGENTIBRIDGE_PORT` | `8100` | HTTP port for SSE transport |
| `AGENTIBRIDGE_API_KEYS` | _(none)_ | Comma-separated API keys (empty = no auth) |
| `AGENTIBRIDGE_POLL_INTERVAL` | `60` | Collector poll interval in seconds (min: 5) |
| `AGENTIBRIDGE_MAX_ENTRIES` | `500` | Max entries per session in Redis (0 = unlimited) |
| `AGENTIBRIDGE_PROJECTS_DIR` | `~/.claude/projects` | Claude transcript directory |
| `POSTGRES_URL` | _(none)_ | Postgres connection URL for pgvector (semantic search vectors) |
| `PGVECTOR_DIMENSIONS` | `1536` | Embedding vector dimensions (must match model) |
| `ANTHROPIC_API_KEY` | _(none)_ | Anthropic API key for summary generation |
| `LLM_API_BASE` | _(none)_ | OpenAI-compatible API base URL for embeddings/chat |
| `LLM_API_KEY` | _(none)_ | API key for LLM endpoint |
| `LLM_EMBED_MODEL` | _(none)_ | Embedding model name (e.g. `text-embedding-3-small`) |
| `LLM_CHAT_MODEL` | _(none)_ | Chat model name for summaries (fallback if no Anthropic key) |
| `CLAUDE_BINARY` | `claude` | Path to Claude CLI binary for dispatch |
| `CLAUDE_DISPATCH_MODEL` | `sonnet` | Model for dispatch_task (`sonnet`, `opus`) |
| `CLAUDE_DISPATCH_TIMEOUT` | `300` | Dispatch timeout in seconds |
| `CLAUDE_HOOK_LOG_ENABLED` | `true` | Enable/disable logging |
| `AGENTIBRIDGE_LOG_FILE` | _auto_ | Log file path (auto-detects Docker vs native) |

Generate a `.env` template: `agentibridge config --generate-env`

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
| `claude_runner.py` | Claude CLI runner (dispatch) |
| `llm_client.py` | OpenAI-compatible embeddings + chat |
| `redis_client.py` | Redis helper |
| `pg_client.py` | Postgres + pgvector connection |
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

# Run unit tests (452 tests)
pytest tests/unit -v -m unit --cov=agentibridge

# Run lint + format check
ruff check agentibridge/ tests/
ruff format --check agentibridge/ tests/

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
# Requires: claude CLI, .mcp.json with agentibridge config, running bridge
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
| `build.yml` | Push to main | Builds Docker image → GHCR |
| `e2e-smoke.yml` | Daily + manual | Runs 6 MCP tool smoke tests via Claude CLI against live tunnel |
| `claude.yml` | Issue/PR comments | Claude Code integration for automated code review |

## Documentation

- [Connecting Clients](docs/getting-started/connecting-clients.md) — Setup guides for Claude Code, ChatGPT, Claude Web, Grok
- [Semantic Search](docs/architecture/semantic-search.md) — Embedding backends and semantic search
- [Remote Access](docs/architecture/remote-access.md) — SSE/HTTP transport and API key auth
- [Session Dispatch](docs/architecture/session-dispatch.md) — Session restore and task dispatch
- [Reverse Proxy](docs/deployment/reverse-proxy.md) — Nginx, Caddy, Traefik configs with SSL
- [Cloudflare Tunnel](docs/deployment/cloudflare-tunnel.md) — Expose to internet securely (quick & named tunnels)

## License

MIT
