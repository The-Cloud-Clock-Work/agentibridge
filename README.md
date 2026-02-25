# AgentiBridge

### Cloudflare-backed persistent session controller for your Claude Code agents

![AgentiBridge - Persistent session controller for your AI Agents](docs/media/agentibridge-readme-banner.jpg)

[![PyPI](https://img.shields.io/pypi/v/agentibridge)](https://pypi.org/project/agentibridge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/The-Cloud-Clock-Work/agentibridge/blob/main/LICENSE)
[![Tests](https://github.com/The-Cloud-Clock-Work/agentibridge/actions/workflows/test.yml/badge.svg)](https://github.com/The-Cloud-Clock-Work/agentibridge/actions/workflows/test.yml)
[![Docker](https://img.shields.io/docker/v/tccw/agentibridge?label=Docker%20Hub)](https://hub.docker.com/r/tccw/agentibridge)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)

```mermaid
flowchart LR
    E([Any AI Client]) -->|query| D{{MCP Tools}}
    D -->|read| C[(SessionStore)]
    C -->|indexed by| B[Collector]
    B -->|watches| A([Claude Code sessions])

    classDef sessions fill:#6366f1,stroke:#4338ca,color:#fff
    classDef collector fill:#f59e0b,stroke:#d97706,color:#fff
    classDef store fill:#10b981,stroke:#059669,color:#fff
    classDef tools fill:#8b5cf6,stroke:#7c3aed,color:#fff
    classDef client fill:#06b6d4,stroke:#0284c7,color:#fff

    class A sessions
    class B collector
    class C store
    class D tools
    class E client
```

## Why AgentiBridge?

Your Claude Code sessions disappear when the terminal closes. AgentiBridge indexes every transcript automatically and makes them searchable, resumable, and dispatchable — from any MCP client.

- 🔒 **Security-first** — OAuth 2.1 with PKCE, API key auth, Cloudflare Tunnel with zero inbound ports. Your data never leaves your infrastructure.
- 🔍 **AI-powered search** — Semantic search with pgvector embeddings. Ask natural language questions across all your past sessions.
- ⚙️ **Automatic indexing** — Background collector watches `~/.claude/projects/` and incrementally indexes new transcripts. No manual exports.
- 🌐 **Multi-client** — Works with Claude Code CLI, claude.ai, ChatGPT, Grok, and any MCP-compatible client.
- 🏠 **Fully self-hosted** — Postgres, Redis, and your data stay on your machine. No SaaS, no vendor lock-in.
- 🚀 **Background dispatch** — Fire-and-forget task dispatch with session restore. Resume work where you left off.
- ⚡ **Zero config to start** — Filesystem fallback means no Redis or Postgres required for basic use. Scale up when you need to.

---

## Quick Start

```bash
pip install agentibridge
agentibridge run
curl http://localhost:8100/health
```

Then add AgentiBridge to `~/.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": {
      "url": "http://localhost:8100/mcp"
    }
  }
}
```

> If you set `AGENTIBRIDGE_API_KEYS`, add `"headers": {"X-API-Key": "your-key"}` to the block above.

That's it. Your Claude Code sessions are now searchable from any MCP-compatible client.

---

## CLI Commands

### Stack

| Command | What it does |
|---------|-------------|
| `agentibridge run` | Start the stack |
| `agentibridge run --rebuild` | Force pull and rebuild before starting |
| `agentibridge stop` | Stop the stack |
| `agentibridge restart` | Restart the stack |
| `agentibridge logs` | View recent logs |
| `agentibridge logs --follow` | Stream logs live |

### Status

| Command | What it does |
|---------|-------------|
| `agentibridge status` | Service health, container status, session count |
| `agentibridge version` | Print version |
| `agentibridge config` | View current configuration |
| `agentibridge config --generate-env` | Generate a `.env` template |
| `agentibridge help` | Full reference |

### Cloudflare Tunnel

| Command | What it does |
|---------|-------------|
| `agentibridge tunnel` | Show tunnel status and current URL |
| `agentibridge tunnel setup` | Interactive wizard: install, auth, DNS, config |

### Dispatch Bridge

| Command | What it does |
|---------|-------------|
| `agentibridge bridge start` | Start the host-side dispatch bridge |
| `agentibridge bridge stop` | Stop the dispatch bridge |
| `agentibridge bridge logs` | Tail dispatch bridge logs |

### Client Setup

| Command | What it does |
|---------|-------------|
| `agentibridge connect` | Print ready-to-paste configs for all clients |
| `agentibridge install --docker` | Install systemd service (Docker) |
| `agentibridge install --native` | Install systemd service (native Python) |
| `agentibridge locks` | View or clear Redis locks |

---

## MCP Tools

### Foundation

| Tool | Example use |
|------|------------|
| `list_sessions` | "Show me my recent sessions" |
| `get_session` | "Get the full transcript for session abc123" |
| `get_session_segment` | "Show me the last 20 messages from that session" |
| `get_session_actions` | "What tools did I use most in that session?" |
| `search_sessions` | "Find sessions where I worked on authentication" |
| `collect_now` | "Refresh the index now" |

### AI-Powered

| Tool | Example use |
|------|------------|
| `search_semantic` | "What were my sessions about database migrations?" |
| `generate_summary` | "Summarize what happened in session abc123" |

> Requires embeddings + LLM configured. See [Semantic Search](docs/architecture/semantic-search.md).

### Dispatch

| Tool | Example use |
|------|------------|
| `restore_session` | "Load the context from my last session on this project" |
| `dispatch_task` | "Continue that refactor task in the background" |
| `get_dispatch_job` | "What's the status of job xyz?" |

> Requires the dispatch bridge running on the host. See [Session Dispatch](docs/architecture/session-dispatch.md).

### Knowledge Catalog

| Tool | Example use |
|------|------------|
| `list_memory_files` | "What memory files exist across my projects?" |
| `get_memory_file` | "Show me the MEMORY.md for the antoncore project" |
| `list_plans` | "What plans have I created recently?" |
| `get_plan` | "Show me the plan called moonlit-rolling-reddy" |
| `search_history` | "Find prompts where I mentioned docker" |

> Exposes Claude Code's knowledge layer: project memory files, implementation plans, and prompt history.

---

## Configuration

### Remote Access

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENTIBRIDGE_TRANSPORT` | `stdio` | Set to `sse` for remote clients |
| `AGENTIBRIDGE_HOST` | `127.0.0.1` | Bind address |
| `AGENTIBRIDGE_PORT` | `8100` | Listen port |
| `AGENTIBRIDGE_API_KEYS` | *(empty)* | Comma-separated API keys; empty = no auth |

### Optional Features

| Variable | Purpose |
|----------|---------|
| `POSTGRES_URL` | Enables semantic search (pgvector) |
| `LLM_API_BASE` | OpenAI-compatible embeddings/chat endpoint |
| `LLM_EMBED_MODEL` | Embedding model (e.g. `text-embedding-3-small`) |
| `LLM_CHAT_MODEL` | Chat model for summaries (e.g. `gpt-4o-mini`) |
| `ANTHROPIC_API_KEY` | Preferred for `generate_summary` (falls back to `LLM_CHAT_MODEL`) |
| `CLAUDE_DISPATCH_URL` | Bridge URL for Docker → host Claude CLI dispatch |
| `AGENTIBRIDGE_PLANS_DIR` | Plans directory (default: `~/.claude/plans`) |
| `AGENTIBRIDGE_HISTORY_FILE` | History file (default: `~/.claude/history.jsonl`) |

See [Configuration Reference](docs/reference/configuration.md) for the full list.

---

## MCP Configuration

AgentiBridge supports two connection modes: **local** (stdio, zero-config) and **remote** (HTTP with API key auth). Use one or both depending on your setup.

### Option A — Local (stdio)

Runs AgentiBridge as a subprocess alongside Claude Code. No server to manage, no auth needed. Best for single-machine use.

```bash
pip install agentibridge
```

To persist configuration, create `~/.agentibridge/.env` (loaded automatically):

```bash
mkdir -p ~/.agentibridge
cp .env.example ~/.agentibridge/.env
# edit ~/.agentibridge/.env with your settings
```

Add to your project `.mcp.json` or `~/.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": {
      "command": "python",
      "args": ["-m", "agentibridge"]
    }
  }
}
```

### Option B — Remote (HTTP + API key)

Runs AgentiBridge as a persistent server — access your sessions from any device or MCP client over the network. Requires `AGENTIBRIDGE_API_KEYS` set on the server.

```json
{
  "mcpServers": {
    "agentibridge": {
      "type": "http",
      "url": "https://bridge.yourdomain.com/mcp",
      "headers": {
        "X-API-Key": "sk-ab-your-api-key-here"
      }
    }
  }
}
```

### Using Both

You can run both side by side — local for low-latency access to your own machine, remote for accessing sessions on another machine or from your phone:

```json
{
  "mcpServers": {
    "agentibridge": {
      "command": "python",
      "args": ["-m", "agentibridge"]
    },
    "agentibridge-remote": {
      "type": "http",
      "url": "https://bridge.yourdomain.com/mcp",
      "headers": {
        "X-API-Key": "sk-ab-your-api-key-here"
      }
    }
  }
}
```

Run `agentibridge connect` to get ready-to-paste configs for other clients (ChatGPT, Claude Web, Grok, generic MCP).

---

## Connect to Claude.ai

Claude.ai requires **OAuth 2.1** to connect to remote MCP servers. AgentiBridge has a built-in OAuth 2.1 authorization server with PKCE — just enable it with one env var.

**1. Enable OAuth on your server:**

Add to your `.env`:

```bash
# Required — enables OAuth 2.1
OAUTH_ISSUER_URL=https://bridge.yourdomain.com

# Optional — lock to a single client (recommended for production)
OAUTH_CLIENT_ID=my-bridge-client
OAUTH_CLIENT_SECRET=generate-a-strong-secret-here
OAUTH_ALLOWED_REDIRECT_URIS=https://claude.ai/api/mcp/auth_callback
OAUTH_ALLOWED_SCOPES=claudeai
```

**2. Expose your server over HTTPS:**

```bash
agentibridge tunnel setup    # Cloudflare Tunnel (easiest)
# or use your own reverse proxy (nginx, Caddy, etc.)
```

**3. Add to claude.ai:**

Go to [claude.ai/settings/connectors](https://claude.ai/settings/connectors), add your server URL:

```
https://bridge.yourdomain.com/mcp
```

Claude.ai will automatically:
1. Discover OAuth metadata at `/.well-known/oauth-authorization-server`
2. Register as a client (or use your pre-configured credentials)
3. Complete the PKCE authorization flow
4. Store the access token and refresh it automatically

No manual JSON config needed — claude.ai handles the entire OAuth flow.

**4. Verify OAuth is working:**

```bash
curl https://bridge.yourdomain.com/.well-known/oauth-authorization-server
curl https://bridge.yourdomain.com/health
```

### OAuth Configuration Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OAUTH_ISSUER_URL` | Yes | Public URL of your server (e.g., `https://bridge.yourdomain.com`) |
| `OAUTH_CLIENT_ID` | No | Pre-configured client ID — disables dynamic registration |
| `OAUTH_CLIENT_SECRET` | No | Pre-configured client secret (required with `OAUTH_CLIENT_ID`) |
| `OAUTH_ALLOWED_REDIRECT_URIS` | No | Comma-separated callback URIs (e.g., `https://claude.ai/api/mcp/auth_callback`) |
| `OAUTH_ALLOWED_SCOPES` | No | Space-separated scopes (claude.ai requests `claudeai`) |

> API key auth (`X-API-Key` header) continues to work alongside OAuth. Both auth methods are active simultaneously.

See [Remote Access & Auth](docs/architecture/remote-access.md) for the full reference.

---

## Cloudflare Tunnel

### Quick tunnel (no account needed)

Gets you a temporary `*.trycloudflare.com` URL — useful for testing, changes on restart.

```bash
docker compose --profile tunnel up -d
agentibridge tunnel    # prints the current public URL
```

### Named tunnel (your own domain)

Gets you a persistent `https://mcp.yourdomain.com` that survives restarts.

**Requires:** A [Cloudflare account](https://dash.cloudflare.com/sign-up) with a domain added.

```bash
agentibridge tunnel setup    # interactive wizard
agentibridge run
curl https://mcp.yourdomain.com/health
```

The wizard installs `cloudflared`, authenticates, creates the DNS record, and writes the config. The bridge itself has no domain config — it just listens on `localhost:8100` and the tunnel routes your domain to it.

See [Cloudflare Tunnel Guide](docs/deployment/cloudflare-tunnel.md) for full details.

---

## Developer Setup

```bash
git clone https://github.com/The-Cloud-Clock-Work/agentibridge
cp .env.example .env
docker compose up --build -d
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for testing, linting, and CI details.

---

## Resources

- [Connecting Clients](docs/getting-started/connecting-clients.md) — Claude Code, ChatGPT, Claude Web, Grok setup
- [Configuration Reference](docs/reference/configuration.md) — All environment variables
- [CLI Commands](docs/reference/cli-commands.md) — Full command and flag reference
- [Semantic Search](docs/architecture/semantic-search.md) — Embedding backends and natural language search
- [Remote Access & Auth](docs/architecture/remote-access.md) — SSE/HTTP transport and API key auth
- [Session Dispatch](docs/architecture/session-dispatch.md) — Background task dispatch and context restore
- [Cloudflare Tunnel](docs/deployment/cloudflare-tunnel.md) — Expose to the internet securely
- [Reverse Proxy](docs/deployment/reverse-proxy.md) — Nginx, Caddy, and Traefik configs
- [Releases & CI/CD](docs/deployment/releases.md) — Release process and automation
- [Internal Architecture](docs/architecture/internals.md) — Key modules and design patterns
- [Knowledge Catalog](docs/architecture/knowledge-catalog.md) — Memory files, plans, and prompt history
- [Contributing](CONTRIBUTING.md)

---

## FAQ

**Isn't this just session history?**

History is the data layer. The product is remote fleet control — dispatch tasks from your phone, search sessions from any MCP client, monitor jobs from claude.ai. You go from 0% productivity away from your desk to controlling your agents from anywhere.

**VS Code / Cursor already has conversation history.**

IDE conversation history is excellent for local replay within that IDE. AgentiBridge serves CLI-first developers and adds capabilities no IDE provides: remote multi-client access, background dispatch from any device, and semantic search across your full session history. When you leave your desk, your IDE history can't dispatch a background task from your phone. AgentiBridge can.

**Won't Anthropic build this natively?**

AgentiBridge is self-hosted, vendor-neutral infrastructure. Native features optimize for one vendor's client. AgentiBridge works with Claude Code, claude.ai, ChatGPT, Grok, and any MCP client. Your data stays on your machine, and you control the storage backend, embedding model, and access policies. MIT licensed — no lock-in.

**Do I need Redis and Postgres?**

No. `pip install agentibridge && agentibridge run` works with zero dependencies — filesystem-only storage out of the box. Add Redis for caching and Postgres for semantic search when you need them.

**Is my data sent anywhere?**

No. No telemetry, no SaaS dependencies. Cloudflare Tunnel is opt-in, and even then only MCP tool responses traverse the tunnel — your transcripts stay local.

**Which clients are supported?**

Claude Code CLI, claude.ai, ChatGPT, Grok, and any MCP-compatible client. Run `agentibridge connect` for ready-to-paste configs.

---

## Code Quality

This project is continuously analyzed by [SonarQube](https://sonar.homeofanton.com/dashboard?id=agentibridge) for code quality, security vulnerabilities, and test coverage.

## License

MIT
