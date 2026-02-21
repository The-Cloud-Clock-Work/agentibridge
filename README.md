# AgentiBridge

**Memory for your AI agents.** AgentiBridge indexes your Claude Code sessions and makes them searchable by any AI client through 10 MCP tools.

**Use cases:**
- 🔍 Search past sessions: "What did I work on last week?"
- 🤖 Cross-session context: Let one agent learn from another's work
- 📊 Session analytics: Track tool usage and patterns
- 🔄 Resume work: Restore session context and continue

**Connect from:** Claude Code CLI, ChatGPT, Claude Web, Grok, or any MCP client

## Quick Start

### Option 1: Local Only (Same Machine)

```bash
pip install agentibridge
agentibridge run
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

Done. Your Claude Code sessions are now searchable.

### Option 2: Public Access via Cloudflare

If you want a persistent URL like `https://mcp.yourdomain.com`, set up the Cloudflare Tunnel **first**, then start the bridge:

```bash
pip install agentibridge
agentibridge tunnel setup           # 1. interactive wizard: install cloudflared, auth, DNS
agentibridge run                    # 2. starts the bridge on :8100
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

The domain is **not** configured in the bridge itself — it lives in the Cloudflare Tunnel config (`~/.cloudflared/config.yml`), which the setup wizard writes for you. The bridge just listens on `localhost:8100` and the tunnel routes your domain to it.

See [Cloudflare Tunnel Setup](#cloudflare-tunnel-setup) for detailed configuration.

## Core Concepts

### MCP Tools (10 total)

**Phase 1 — Foundation** (6 tools)
- `list_sessions` — Browse all indexed sessions
- `get_session` — Retrieve full session with transcript
- `get_session_segment` — Get paginated transcript chunks
- `get_session_actions` — Analyze tool usage patterns
- `search_sessions` — Keyword search across transcripts
- `collect_now` — Force immediate index update

**Phase 2 — AI-Powered** (2 tools)
- `search_semantic` — Natural language search with embeddings
- `generate_summary` — AI-generated session summaries

**Phase 4 — Write-back** (2 tools)
- `restore_session` — Load past session context
- `dispatch_task` — Start new task with context from past sessions

> **Note:** Phase 3 is the transport layer (SSE/HTTP), not exposed as tools.

### CLI Commands

**Docker Stack:**
```bash
agentibridge run                  # Start the stack (detects state, shows status)
agentibridge run --rebuild        # Force pull + rebuild before starting
agentibridge stop                 # Stop the stack
agentibridge restart              # Restart the stack
agentibridge logs                 # View logs (last 100 lines)
agentibridge logs --follow        # Stream logs live
```

**Status & Info:**
```bash
agentibridge status               # Service health, per-container health checks, Redis, sessions
agentibridge version              # Print version
agentibridge config               # View current configuration
agentibridge config --generate-env  # Generate .env template
```

**Dispatch Bridge (host-side Claude CLI proxy):**
```bash
agentibridge bridge start         # Start dispatch bridge in background
agentibridge bridge stop          # Stop dispatch bridge
agentibridge bridge logs          # Tail dispatch bridge log
```

**Cloudflare Tunnel:**
```bash
agentibridge tunnel               # Show tunnel status and URL
agentibridge tunnel setup         # Interactive wizard: install, auth, DNS, config, systemd
```

**Client & Service Setup:**
```bash
agentibridge connect              # Get connection configs for all clients
agentibridge install --docker     # Set up systemd service (Docker)
agentibridge install --native     # Set up systemd service (native Python)
agentibridge locks                # View/clear Redis locks
```

See `agentibridge help` for the full tool and configuration reference.

### Architecture

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

### Docker Stack via CLI (Recommended)

```bash
pip install agentibridge
agentibridge run
```

`agentibridge run` copies the bundled `docker-compose.yml` and `.env` template to `~/.config/agentibridge/` on first run, validates required env vars, detects current stack state, then runs `docker compose up -d`. Separate containers for app, Redis, and Postgres.

### pip Install (Local/Development)

```bash
pip install -e .
python -m agentibridge          # stdio transport (local MCP)

# Or with SSE for remote clients:
AGENTIBRIDGE_TRANSPORT=sse python -m agentibridge
```

### systemd Service (Auto-start on Boot)

```bash
pip install -e .
agentibridge install --docker    # Docker-based
# or
agentibridge install --native    # Native Python
```

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

## Configuration

### Essential Variables

**For Basic Setup (Docker Compose):**
```bash
# No configuration needed - Docker Compose sets defaults
# Redis, ports, and volumes are pre-configured
```

**For Remote Access (SSE Transport):**
```bash
AGENTIBRIDGE_TRANSPORT=sse
AGENTIBRIDGE_HOST=0.0.0.0
AGENTIBRIDGE_PORT=8100
AGENTIBRIDGE_API_KEYS=your-secret-key  # Optional: comma-separated
```

**For Semantic Search (Phase 2):**
```bash
POSTGRES_URL=postgresql://user:pass@localhost:5432/agentibridge
LLM_API_BASE=http://localhost:11434/v1
LLM_EMBED_MODEL=text-embedding-3-small
```

**For AI Summaries:**
```bash
ANTHROPIC_API_KEY=sk-ant-...  # Preferred for summaries
# OR
LLM_CHAT_MODEL=gpt-4o-mini   # Fallback if no Anthropic key
```

### All Configuration Options

See [Configuration Reference](docs/reference/configuration.md) for the complete list of 20+ environment variables.

**Quick config commands:**
```bash
agentibridge config                  # View current config
agentibridge config --generate-env   # Generate .env template
```

## Cloudflare Tunnel Setup

AgentiBridge can be exposed via Cloudflare Tunnel for remote access with zero configuration and automatic TLS.

### Quick Tunnel (No Account Needed)

Temporary `*.trycloudflare.com` URL that changes on restart. Good for testing.

```bash
agentibridge run
docker compose --profile tunnel up -d   # adds the cloudflared sidecar
agentibridge tunnel                     # prints the temporary public URL
```

### Named Tunnel (Persistent URL)

For a stable URL like `https://mcp.yourdomain.com` that survives restarts.

**Prerequisites:** A [Cloudflare account](https://dash.cloudflare.com/sign-up) with at least one domain added.

**Run the interactive wizard:**

```bash
agentibridge tunnel setup
```

The wizard walks you through 10 steps:
1. Installs `cloudflared` binary (Linux amd64/arm64/arm, macOS via Homebrew)
2. Authenticates with Cloudflare (opens browser)
3. Prompts for tunnel name (default: `agentibridge`)
4. Creates the tunnel (idempotent — skips if it already exists)
5. Prompts for subdomain (e.g., `mcp`)
6. Prompts for domain (e.g., `yourdomain.com`)
7. Creates DNS CNAME route automatically
8. Writes `~/.cloudflared/config.yml` (backs up any existing file)
9. Optionally installs and enables a systemd service (Linux)
10. Runs a health check against your public hostname

**After the wizard finishes**, start the bridge:

```bash
agentibridge run
curl https://mcp.yourdomain.com/health
# {"status": "ok", "service": "agentibridge"}
```

**Check tunnel status at any time:**

```bash
agentibridge tunnel           # shows container status + URL
```

**Alternative (Docker-only):** If you already created a tunnel in the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com/), pass the token directly:

```bash
CLOUDFLARE_TUNNEL_TOKEN=xxx docker compose --profile tunnel-named up -d
```

See [Cloudflare Tunnel Guide](docs/deployment/cloudflare-tunnel.md) for detailed instructions and troubleshooting.

> **How it works:** Cloudflare Tunnel routes your domain → `localhost:8100`. The bridge has no domain config — it's all in `~/.cloudflared/config.yml`.

## What's Next

**Getting Started:**
- [Connecting Clients](docs/getting-started/connecting-clients.md) — Setup for Claude Code, ChatGPT, Claude Web, Grok
- [Configuration Reference](docs/reference/configuration.md) — All environment variables explained

**Advanced Features:**
- [Semantic Search](docs/architecture/semantic-search.md) — Embedding backends and natural language search
- [Session Dispatch](docs/architecture/session-dispatch.md) — Restore context and dispatch tasks
- [Remote Access](docs/architecture/remote-access.md) — SSE/HTTP transport and authentication

**Deployment:**
- [Cloudflare Tunnel](docs/deployment/cloudflare-tunnel.md) — Expose to internet securely
- [Reverse Proxy](docs/deployment/reverse-proxy.md) — Nginx, Caddy, Traefik configs

**Contributing:**
- See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing, and CI/CD
- [Internal Architecture](docs/architecture/internals.md) — Deep dive into key modules and patterns

## License

MIT
