# CLI Command Reference

Complete reference for the `agentibridge` command-line tool.

---

## Docker Stack

### `agentibridge run`

Start the Docker stack (AgentiBridge + Redis + Postgres).

```
agentibridge run [--rebuild]
```

On first run, copies the bundled `docker-compose.yml` and `.env.example` template to
`~/.config/agentibridge/`. If `.env` does not yet exist the command exits immediately
with instructions to edit it before retrying.

Before starting, the command validates that all [required env vars](#env-required-variables)
are present in `.env`. If any are missing it prints them and exits with code 1.

State detection:
- **running** — prints advisory, pulls latest images, and restarts
- **partial** — starts missing containers
- **stopped** — starts the full stack

**Flags**

| Flag | Description |
|------|-------------|
| `--rebuild` | Force `--pull always --build` before starting (equivalent to `docker compose up --build --pull always -d`) |

---

### `agentibridge stop`

Stop the Docker stack.

```
agentibridge stop
```

Runs `docker compose down` against the managed stack in `~/.config/agentibridge/`.

---

### `agentibridge restart`

Restart all containers in the stack without recreating them.

```
agentibridge restart
```

Runs `docker compose restart`.

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
`~/.config/agentibridge/.env`. If `DISPATCH_SECRET` is not set the command exits
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

Inspects the `agentibridge-tunnel` Docker container. Outputs differ by mode:

- **Quick tunnel** — prints the `*.trycloudflare.com` URL and a ready-to-paste
  `~/.mcp.json` snippet including an API key (if `AGENTIBRIDGE_API_KEYS` is set).
- **Named tunnel** — confirms connected state and directs you to the Cloudflare
  Zero Trust dashboard for the hostname.
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

Creates `~/.config/agentibridge/env` (if absent), copies the appropriate `.service`
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
Config files in `~/.config/agentibridge/` are **not** removed.

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

## `.env` Required Variables

The following variables are validated by `_validate_env` before every
`run`, `stop`, `restart`, or `logs` invocation. If any are absent the
command exits with a descriptive error.

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Redis connection URL (e.g. `redis://localhost:6379/0`) |
| `POSTGRES_URL` | Postgres connection URL (e.g. `postgresql://user:pass@localhost:5432/db`) |
| `POSTGRES_USER` | Postgres username |
| `POSTGRES_PASSWORD` | Postgres password |
| `POSTGRES_DB` | Postgres database name |
| `AGENTIBRIDGE_TRANSPORT` | Transport mode: `stdio` or `sse` |
| `AGENTIBRIDGE_PORT` | HTTP port for SSE transport (e.g. `8100`) |

Generate a fully-annotated template:

```bash
agentibridge config --generate-env > ~/.config/agentibridge/.env
```

See [Configuration](configuration.md) for the complete list of optional variables.
