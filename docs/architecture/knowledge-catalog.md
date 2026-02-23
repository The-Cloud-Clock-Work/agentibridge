# Knowledge Catalog

The Knowledge Catalog (Phase 5) exposes Claude Code's local knowledge layer through 5 MCP tools. It makes project memory, implementation plans, and prompt history searchable and accessible from any MCP client.

## Data Sources

| Source | Location | Description |
|--------|----------|-------------|
| **Memory files** | `~/.claude/projects/{project}/memory/*.md` | Curated project knowledge — the highest-signal content per project |
| **Plans** | `~/.claude/plans/*.md` | Implementation blueprints with three-word codenames (e.g., `moonlit-rolling-reddy`) |
| **History** | `~/.claude/history.jsonl` | Every user prompt across all sessions, with timestamps and session IDs |

## MCP Tools

### Memory

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_memory_files` | `project` (optional filter) | List all memory files with metadata (size, modified date). No content returned — use `get_memory_file` for content |
| `get_memory_file` | `project` (required), `filename` (default: `MEMORY.md`) | Read a specific memory file's content |

**Example queries:**
- "What memory files exist across my projects?"
- "Show me the MEMORY.md for the antoncore project"
- "Read the cloudflare-ai-bots.md memory from antoncore"

### Plans

| Tool | Parameters | Description |
|------|-----------|-------------|
| `list_plans` | `project`, `codename`, `limit`, `offset`, `include_agent_plans` | List plans sorted by recency. Agent subplans hidden by default |
| `get_plan` | `codename` (required), `include_agent_plans` | Read full plan content. Optionally includes agent subplans |

**Agent subplans:** Plans with suffix `-agent-{hex_hash}` are subplans created by Claude Code's agent subprocess system. They're linked to the parent plan by codename prefix. Use `include_agent_plans=True` to retrieve them.

**Codename-session linking:** When the collector indexes transcripts, it extracts the `slug` field from JSONL entries and builds a codename-to-session index. This allows plans to show which sessions they were used in.

**Example queries:**
- "List my most recent plans"
- "Show me the plan called soft-churning-orbit"
- "Get the plan with all its agent subplans"

### History

| Tool | Parameters | Description |
|------|-----------|-------------|
| `search_history` | `query`, `project`, `session_id`, `limit`, `offset`, `since` | Search the global prompt history with keyword, time, project, and session filters |

**Example queries:**
- "Find prompts where I mentioned docker"
- "Show my prompts from the last 24 hours"
- "What did I ask in session abc123?"

## Architecture

### Collector Integration

The background collector runs 3 scan passes for the knowledge catalog after transcript indexing:

```
collect_once()
  ├── Transcript scan (existing)
  ├── Memory scan    → scan_memory_files() → upsert to Redis
  ├── Plans scan     → scan_plans_dir()    → upsert to Redis + link sessions
  └── History scan   → parse_history()     → append to Redis (incremental)
```

### Incremental History Parsing

The `history.jsonl` file can grow large (1MB+). The collector tracks a byte offset and only reads new lines on each cycle:

1. Seek to last known offset
2. Detect line boundary (peek at byte before offset)
3. Skip partial line if mid-line
4. Parse new complete JSON lines
5. Store updated offset

### Redis Key Schema

```
agentibridge:sb:memory:{project}:{filename}   # Hash: metadata + content
agentibridge:sb:idx:memory                     # Sorted set: all memory keys (score = mtime)
agentibridge:sb:plan:{codename}                # Hash: metadata + content
agentibridge:sb:plan:{codename}:agents         # List: agent subplan codenames
agentibridge:sb:idx:plans                      # Sorted set: all plans (score = mtime)
agentibridge:sb:codename:{slug}                # Set: session IDs for a codename
agentibridge:sb:history                        # List: JSON history entries
agentibridge:sb:pos:history                    # String: byte offset
```

### File Fallback

When Redis is unavailable, all operations fall back to direct filesystem reads:

- **Memory**: Scans `~/.claude/projects/*/memory/*.md` directly
- **Plans**: Scans `~/.claude/plans/*.md` directly
- **History**: Reads `~/.claude/history.jsonl` from the beginning

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENTIBRIDGE_PLANS_DIR` | `~/.claude/plans` | Plans directory path |
| `AGENTIBRIDGE_HISTORY_FILE` | `~/.claude/history.jsonl` | History file path |
| `AGENTIBRIDGE_MAX_HISTORY_ENTRIES` | `5000` | Max history entries in Redis |
| `AGENTIBRIDGE_MAX_MEMORY_CONTENT` | `51200` | Max bytes per memory file (50KB) |
| `AGENTIBRIDGE_MAX_PLAN_CONTENT` | `102400` | Max bytes per plan file (100KB) |

Defaults derive from `AGENTIBRIDGE_PROJECTS_DIR`'s parent directory, so if you change the projects dir, plans and history paths adjust automatically.

## Docker

The `docker-compose.yml` mounts the entire `~/.claude` directory read-only:

```yaml
volumes:
  - ${CLAUDE_DIR:-~/.claude}:/home/appuser/.claude:ro
```

This gives the container access to transcripts, plans, history, and memory files without separate mount entries.

## See Also

- [Internal Architecture](internals.md) — Full module reference and design patterns
- [Configuration Reference](../reference/configuration.md) — All environment variables
