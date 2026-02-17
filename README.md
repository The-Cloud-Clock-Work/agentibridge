# Agentic Bridge

Standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 10 tools across 4 phases.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build -d
curl http://localhost:8100/health
```

### Local

```bash
pip install -e .
python -m agentic_bridge  # stdio transport (default)
```

### Connect from Claude Code

Add to your `.mcp.json`:

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

## Tools

| Phase | Tool | Description |
|-------|------|-------------|
| 1 | `list_sessions` | List sessions across all projects |
| 1 | `get_session` | Get full session metadata + transcript |
| 1 | `get_session_segment` | Paginated transcript retrieval |
| 1 | `get_session_actions` | Extract tool calls with counts |
| 1 | `search_sessions` | Keyword search |
| 1 | `collect_now` | Trigger immediate collection |
| 2 | `search_semantic` | Semantic search using embeddings |
| 2 | `generate_summary` | AI-generated session summary |
| 4 | `restore_session` | Load session context for continuation |
| 4 | `dispatch_task` | Dispatch task with session context |

## Configuration

See [`.env.example`](.env.example) for all available environment variables.

## Documentation

- [Phase 2: Semantic Search](docs/phase2-semantic-search.md)
- [Phase 3: Remote Access](docs/phase3-remote-access.md)
- [Phase 4: Dispatch](docs/phase4-dispatch.md)
