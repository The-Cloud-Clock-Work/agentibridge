---
title: CLI Commands
nav_order: 1
---

# CLI Command Reference

Complete reference for the `agentibridge` command-line tool.

---

## Docker Stack

### `agentibridge run`

Start the Docker stack (AgentiBridge + Redis + Postgres).

```
agentibridge run [--rebuild] [--test]
```

On first run, copies the bundled `docker-compose.yml` and `docker.env.example` template to
`~/.agentibridge/`. If `docker.env` does not yet exist the command exits immediately
with instructions to edit it before retrying.

Before starting, the command validates that all [required env vars](#env-required-variables)
are present in `docker.env`. If any are missing it prints them and exits with code 1.

State detection:
- **running** — prints advisory, pulls latest images, and restarts
- **partial** — starts missing containers
- **stopped** — starts the full stack

**Flags**

| Flag | Description |
|------|-------------|
| `--rebuild` | Force `--pull always --build` before starting (equivalent to `docker compose up --build --pull always -d`) |
| `--test` | Dev mode: build from local source with fresh config (see below) |

#### `--test` — Local dev testing mode

For developing AgentiBridge itself. Must be run from the repo root (where `Dockerfile` and
`docker-compose.yml` exist).

```bash
cd ~/dev/agentibridge
pip install -e .
agentibridge run --test
```

What it does:

1. **Backs up and resets `~/.agentibridge/`** — copies the directory to `~/.agentibridge-backup`,
   then wipes it so `_ensure_stack_dir()` recreates it with fresh env templates (picks up any
   new variables added to the bundled `docker.env.example`). If a backup already exists it is
   kept as-is (only the first run creates the backup).
2. **Ensures `.env`** — copies `.env.example` to `.env` at the repo root if it doesn't exist.
3. **Builds from local source** — runs `docker compose -f docker-compose.yml --env-file .env up --build -d`
   using the repo root compose file (which has `build: context: .`) instead of the pip-distributed
   compose file that pulls `tccw/agentibridge:latest` from Docker Hub.
4. **Auto-starts the dispatch bridge** if `DISPATCH_SECRET` is configured in `~/.agentibridge/docker.env`.

This bypasses the normal `~/.agentibridge/` managed stack entirely for the Docker build, while still
refreshing the env templates so the bridge and other host-side features pick up new config variables.

---

### `agentibridge stop`

Stop the Docker stack.

```
agentibridge stop
```

Runs `docker compose down` against the managed stack in `~/.agentibridge/`.

---

### `agentibridge restart`

Restart all containers in the stack without recreating them.

```
agentibridge restart
```

Runs `docker compose restart`.

> **Important:** `restart` does **not** reload `docker.env`. Docker Compose `restart` only sends SIGHUP to existing containers — environment variables are baked in at container creation time. If you changed `docker.env` (e.g., enabled OAuth, changed API keys, updated ports), you must recreate the containers:
>
> ```bash
> agentibridge stop   # docker compose down
> agentibridge run    # docker compose up -d (recreates with new env)
> ```

---

### `agentibridge logs`

Stream or tail Docker stack logs.

```
agentibridge logs [--tail N] [--follow]
```

**Flags**

| Flag | Default | Description |
|------|---------|-------------|
| `--tail N` | `100` | Number of lines to show from the end of each container's log |
| `--follow`, `-f` | off | Follow log output (streams until Ctrl-C) |

---

## Status & Info

### `agentibridge status`

Print a multi-section health report.

```
agentibridge status
```

Sections printed:

| Section | What it checks |
|---------|---------------|
| `[Service]` | `systemctl --user is-active agentibridge` |
| `[Docker Stack]` | Health status of `agentibridge`, `agentibridge-redis`, `agentibridge-postgres` containers |
| `[Redis]` | Ping + indexed session count |
| `[Postgres]` | Connection + chunk/session counts from `transcript_chunks` table |
| `[Tunnel]` | Cloudflare Tunnel container state + quick-tunnel URL (if running) |
| `[Transcripts]` | Path to `~/.claude/projects/` and count of `.jsonl` files |
| `[Config]` | Active transport, port, and poll interval |

---

### `agentibridge version`

Print the installed version.

```
agentibridge version
```

---

### `agentibridge help`

Print available MCP tools, configuration variables, and usage examples.

```
agentibridge help
```

---

### `agentibridge config`

Show the current resolved configuration or generate a `.env` template.

```
agentibridge config [--generate-env]
```

Without flags: prints each known environment variable with its current value and
source (`env` = set in environment, `default` = using built-in default).

**Flags**

| Flag | Description |
|------|-------------|
| `--generate-env` | Print a fully-commented `.env` template to stdout. Redirect to a file to bootstrap a new deployment: `agentibridge config --generate-env > .env` |

---

## Dispatch Bridge

The dispatch bridge is a host-side HTTP proxy that allows the Dockerised AgentiBridge
container to call the Claude CLI binary installed on the host machine.

### `agentibridge bridge start`

Start the dispatch bridge as a detached background process.

```
agentibridge bridge start
```

Reads `DISPATCH_SECRET` and `DISPATCH_BRIDGE_PORT` (default `8101`) from
`~/.agentibridge/docker.env`. If `DISPATCH_SECRET` is not set the command exits
with an error.

Checks whether an existing bridge process is already running (via `pgrep`) and
exits early if so.

Log output is written to `/tmp/dispatch_bridge.log`.

---

### `agentibridge bridge stop`

Stop the dispatch bridge.

```
agentibridge bridge stop
```

Sends SIGTERM to all `agentibridge.dispatch_bridge` processes found by `pgrep`.

---

### `agentibridge bridge logs`

Tail the dispatch bridge log file.

```
agentibridge bridge logs
```

Runs `tail -f /tmp/dispatch_bridge.log`. Exits with code 1 if the log file does
not exist.

---

## Cloudflare Tunnel

### `agentibridge tunnel`

Show Cloudflare Tunnel container state and the active URL.

```
agentibridge tunnel [status]
```

Checks both the `agentibridge-tunnel` Docker container and the `cloudflared` systemd service. Outputs differ by mode:

- **Quick tunnel (Docker)** — prints the `*.trycloudflare.com` URL and a ready-to-paste
  `~/.mcp.json` snippet including an API key (if `AGENTIBRIDGE_API_KEYS` is set).
- **Named tunnel (Docker)** — confirms connected state and directs you to the Cloudflare
  Zero Trust dashboard for the hostname.
- **Systemd service** — shows tunnel ID, hostname, service target, and credentials path
  from `~/.cloudflared/config.yml`, plus a ready-to-paste `~/.mcp.json` snippet.
- **Not running** — prints start instructions for both quick and named tunnel modes.

---

### `agentibridge tunnel setup`

Interactive 10-step wizard to install and configure a named Cloudflare tunnel.

```
agentibridge tunnel setup
```

Steps:

| # | Action |
|---|--------|
| 1 | Install `cloudflared` if not already present (Linux amd64/arm64/arm via direct binary, macOS via Homebrew) |
| 2 | Authenticate with Cloudflare (`cloudflared tunnel login`) if not already logged in |
| 3 | Prompt for tunnel name (default: `agentibridge`) |
| 4 | Create the tunnel if it does not already exist (idempotent) |
| 5 | Prompt for subdomain (e.g. `mcp`) |
| 6 | Prompt for domain (e.g. `example.com`) |
| 7 | Set DNS CNAME route (`cloudflared tunnel route dns`) |
| 8 | Write `~/.cloudflared/config.yml` (backs up any existing file with a timestamp suffix) |
| 9 | Optionally install and enable `cloudflared` as a systemd service (Linux only) |
| 10 | Health check: `curl https://<hostname>/health` |

---

## Client & Service Setup

### `agentibridge connect`

Print connection strings for all supported MCP clients.

```
agentibridge connect [--host HOST] [--port PORT] [--api-key KEY]
```

Outputs ready-to-paste configuration for: Claude Code CLI (`~/.mcp.json`),
ChatGPT Custom GPT Actions, Claude Web (MCP), generic SSE API, and a `curl`
health check.

**Flags**

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `localhost` | Server hostname or IP |
| `--port` | `8100` | Server port |
| `--api-key` | `your-api-key` | API key to embed in examples |

---

### `agentibridge install`

Install AgentiBridge as a systemd user service.

```
agentibridge install [--docker | --native]
```

Creates `~/.agentibridge/env` (if absent), copies the appropriate `.service`
file to `~/.config/systemd/user/`, then runs `systemctl --user enable --now agentibridge`.

**Flags**

| Flag | Description |
|------|-------------|
| `--docker` | Use the Docker-based service unit (default) |
| `--native` | Use the native Python service unit |

---

### `agentibridge uninstall`

Remove the systemd user service.

```
agentibridge uninstall
```

Stops and disables the service, removes the `.service` file, and reloads systemd.
Config files in `~/.agentibridge/` are **not** removed.

---

### `agentibridge locks`

Inspect Redis keys, file position locks, and running bridge processes.

```
agentibridge locks [--clear]
```

Sections:

| Section | Content |
|---------|---------|
| `[Redis Keys]` | Session index size, project indexes, file position offsets, session data key counts, memory usage |
| `[File Position Locks]` | `.pos` files under `~/.cache/agentibridge/positions/` with byte offsets |
| `[Bridge Processes]` | Live `agentibridge` processes (via `pgrep`) + Docker container list |

**Flags**

| Flag | Description |
|------|-------------|
| `--clear` | Delete all file `.pos` locks and Redis `pos:*` keys, forcing a full re-index on the next collection cycle |

---

### `agentibridge embeddings`

Show the full embedding pipeline status: configuration, LLM backend connectivity, Postgres vector storage stats, and embedding coverage.

```
agentibridge embeddings [--check-llm]
```

Sections:

| Section | Content |
|---------|---------|
| `[Config]` | `AGENTIBRIDGE_EMBEDDING_ENABLED`, `LLM_API_BASE`, `LLM_API_KEY` (redacted), `LLM_EMBED_MODEL`, `PGVECTOR_DIMENSIONS` |
| `[LLM Backend]` | Whether the LLM endpoint is configured; with `--check-llm`, sends a test embedding request |
| `[Postgres]` | Connection status, `transcript_chunks` table existence, total chunks, sessions embedded, table size, last embedded timestamp |
| `[Coverage]` | Total sessions in Redis vs sessions with embeddings, with a coverage percentage |

**Flags**

| Flag | Description |
|------|-------------|
| `--check-llm` | Send a real (tiny) embedding request to the LLM endpoint to verify connectivity. Off by default to avoid API costs/latency |

---

## `docker.env` Required Variables

The following variables are validated by `_validate_env` before every
`run`, `stop`, `restart`, or `logs` invocation. If any are absent the
command exits with a descriptive error. These are checked in `~/.agentibridge/docker.env`.

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Redis connection URL (e.g. `redis://redis:6379/0`) |
| `POSTGRES_URL` | Postgres connection URL (e.g. `postgresql://user:pass@localhost:5432/db`) |
| `POSTGRES_USER` | Postgres username |
| `POSTGRES_PASSWORD` | Postgres password |
| `POSTGRES_DB` | Postgres database name |
| `AGENTIBRIDGE_TRANSPORT` | Transport mode (should be `sse` for Docker) |
| `AGENTIBRIDGE_PORT` | HTTP port for SSE transport (e.g. `8100`) |

Generate a fully-annotated template:

```bash
agentibridge config --generate-env > ~/.agentibridge/docker.env
```

See [Configuration](configuration.md) for the complete list of optional variables.
