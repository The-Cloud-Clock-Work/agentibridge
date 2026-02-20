# Remote Access (SSE Transport)

AgentiBridge can be accessed remotely via SSE (Server-Sent Events) over HTTP, allowing external clients like claude.ai, mobile apps, or other API consumers to query session transcripts without local filesystem access.

## Architecture

```
+------------------+     SSE/HTTP      +--------------------------+
|  claude.ai       | ----------------->|  AgentiBridge          |
|  Mobile app      |   X-API-Key auth  |  SSE transport (:8100)   |
|  API client      | <---------------- |                          |
+------------------+     Events        |  All 10 MCP tools        |
                                       |  + Redis backend         |
                                       +--------------------------+
```

## Transport Modes

AgentiBridge supports two transport modes:

| Mode | Default | Use Case |
|------|---------|----------|
| `stdio` | Yes | Local MCP client (Claude Code CLI) |
| `sse` | No | Remote HTTP clients |

Transport is selected via the `AGENTIBRIDGE_TRANSPORT` environment variable.

## Components

### `agentibridge/transport.py`

Provides SSE transport configuration with API key authentication.

**Key functions:**

| Function | Description |
|----------|-------------|
| `validate_api_key(key)` | Check key against AGENTIBRIDGE_API_KEYS |
| `run_sse_server(mcp)` | Build ASGI stack and start SSE server with auth |

### Authentication

When `AGENTIBRIDGE_API_KEYS` is set, all requests must include a valid API key:

- **Header**: `X-API-Key: your-key`
- **Query param**: `?api_key=your-key`

When no keys are configured, auth is disabled (open access).

### Transport Selection in `server.py`

```python
transport = os.getenv("AGENTIBRIDGE_TRANSPORT", "stdio")
if transport == "sse":
    from agentibridge.transport import run_sse_server
    run_sse_server(mcp)
else:
    mcp.run()  # stdio (default)
```

## Configuration

```bash
# Transport mode
AGENTIBRIDGE_TRANSPORT=stdio     # "stdio" (default) or "sse"

# SSE port
AGENTIBRIDGE_PORT=8100           # HTTP port for SSE transport

# API key auth (comma-separated, empty = no auth)
AGENTIBRIDGE_API_KEYS=key1,key2
```

## Remote Client Configuration

### claude.ai / Claude Desktop

```json
{
  "mcpServers": {
    "agentibridge": {
      "url": "http://your-host:8100/sse",
      "headers": {
        "X-API-Key": "your-api-key"
      }
    }
  }
}
```

### Docker Compose

```bash
docker compose up --build -d
# SSE endpoint: http://localhost:8100/sse
# Health check: http://localhost:8100/health
```

## Dependencies

- `starlette` — ASGI framework (included with `mcp` package)
- `uvicorn` — ASGI server
- `sse-starlette` — SSE support (included with `mcp` package)

## Security Notes

- Always set `AGENTIBRIDGE_API_KEYS` when exposing SSE transport to a network
- Use HTTPS (reverse proxy) for production deployments
- API keys are checked against a simple comma-separated list (no hashing)
- Consider network-level restrictions (firewall, VPN) in addition to API key auth
