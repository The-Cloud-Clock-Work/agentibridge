---
title: Session Dispatch
nav_order: 3
---

# Session Dispatch & Context Restore

Session continuity and task delegation. Agents can dispatch tasks to the Claude CLI as **fire-and-forget background jobs**, restore context from past sessions, or truly resume an existing session thread.

## Architecture

```
                         dispatch_task()
                               |
                    ┌──────────┴──────────┐
                    │                     │
              session_id?          resume_session_id?
                    │                     │
                    v                     v
        restore_session_context()   --resume <id> flag
        (inject context into prompt)  (continue thread)
                    │                     │
                    └──────────┬──────────┘
                               │
                               v
                    asyncio.create_task()
                    (fire and forget)
                               │
                               v
                    Returns job_id immediately
                               │
                    ┌──────────┴──────────┐
                    │                     │
              job running          get_dispatch_job(job_id)
              in background         → status / output
```

## MCP Tools

### `dispatch_task`

Dispatches a task to the Claude CLI as a **background job**. Returns immediately with a `job_id` — does not wait for completion.

```
Args:
  task_description  (str)           — what to do
  project           (str, optional) — project path hint
  session_id        (str, optional) — inject context from past session
  resume_session_id (str, optional) — resume an existing session thread
  command           (str, default "default") — model preset
  context_turns     (int, default 10)        — turns to include from context
```

**Returns immediately:**
```json
{
  "success": true,
  "dispatched": true,
  "background": true,
  "job_id": "71dad581-e7d2-40c2-81a5-bed77bcb813d",
  "status": "running",
  "context_session": null,
  "resumed_session": null,
  "prompt_length": 162
}
```

### `get_dispatch_job`

Poll a background job for status and output.

```
Args:
  job_id  (str) — UUID returned by dispatch_task
```

**While running:**
```json
{
  "job_id": "71dad581...",
  "status": "running",
  "started_at": "2026-02-21T11:04:31Z",
  "output": null
}
```

**When done:**
```json
{
  "job_id": "71dad581...",
  "status": "completed",
  "started_at": "2026-02-21T11:04:31Z",
  "completed_at": "2026-02-21T11:05:05Z",
  "output": "Created tests/test_hello.py with the requested test.",
  "error": null,
  "exit_code": 0,
  "duration_ms": 17412,
  "timed_out": false,
  "claude_session_id": "a0f4dd54-5a16-4463-b7b2-24ca41901e12"
}
```

`status` values: `running` · `completed` · `failed`

### `list_dispatch_jobs`

List dispatch jobs with optional status filter. Returns job summaries (newest first) without the full output field.

```
Args:
  status  (str, optional) — filter by status ("running", "completed", "failed")
  limit   (int, default 20) — max jobs to return
```

**Response:**
```json
{
  "success": true,
  "count": 3,
  "jobs": [
    {
      "job_id": "71dad581...",
      "status": "completed",
      "started_at": "2026-02-27T10:00:00Z",
      "completed_at": "2026-02-27T10:01:00Z",
      "task": "Fix the failing tests"
    },
    {
      "job_id": "a3bc9e12...",
      "status": "running",
      "started_at": "2026-02-27T10:05:00Z"
    }
  ]
}
```

### `restore_session`

Load context from a past session for injection into a new conversation.

```
Args: session_id (str), last_n (int, default 20)
Returns: JSON with formatted context string + char_count
```

## Two Ways to Use a Past Session

### 1. Context Injection (`session_id`)

Extracts recent turns from the past session and injects them as text into the new prompt. Claude starts a **fresh conversation** but is aware of the previous work.

```python
dispatch_task(
    task_description="Deploy the auth changes to staging",
    session_id="abc-123",        # context injected as text
    context_turns=15,
)
```

Use when: continuing work thematically, summarising, or handing off across projects.

### 2. True Session Resume (`resume_session_id`)

Passes `--resume <id>` to the Claude CLI. The existing session thread is **literally continued** — same conversation, same memory, same context window.

```python
dispatch_task(
    task_description="Continue where we left off — fix the failing tests",
    resume_session_id="abc-123",  # --resume flag passed to CLI
)
```

Use when: mid-task interruption, picking up an in-progress session, or appending turns to an existing thread.

## Natural Language Status Checks

The `job_id` stays in the conversation context, so you can just ask:

> *"How is that job going?"*
> *"Did the antoncore task finish?"*
> *"What did it output?"*

The agent will call `get_dispatch_job` on the relevant job_id without needing it repeated. From a **new conversation**, use `list_dispatch_jobs` to browse recent jobs — no need to remember job IDs.

> *"What jobs have I dispatched recently?"*
> *"Show me the failed jobs"*
> *"Any running jobs?"*

## Permissions

All dispatched Claude CLI invocations run with `--dangerously-skip-permissions`. No approval prompts will block background jobs.

## Job Storage

Job state is persisted with a Redis-primary, file-fallback pattern:

1. **Redis** (primary): stored as a hash at `agentibridge:sb:job:{job_id}` with a sorted set index at `agentibridge:sb:idx:jobs` (scored by start time). TTL is 24 hours.
2. **File** (fallback): always written to `/tmp/agentibridge_jobs/<job_id>.json`, even when Redis is available.

Reads check Redis first; if unavailable, fall back to file. `list_jobs()` reads from the Redis sorted set (newest first) or scans the file directory by mtime. Jobs survive MCP server restarts. Background tasks are held in memory via `asyncio.create_task()` with GC protection while running.

## Command Presets

| `command`    | Model  |
|-------------|--------|
| `default`   | Sonnet |
| `thinkhard` | Sonnet |
| `ultrathink`| Opus   |

## Dispatch Bridge (Docker Mode)

When AgentiBridge runs inside Docker, the `claude` CLI binary isn't available in the container. A lightweight HTTP bridge runs on the **host** and proxies dispatch requests to the local CLI.

```
┌─────────────────────┐        HTTP         ┌──────────────────────┐
│ Docker container     │◄──────────────────►│  Host bridge          │
│ AgentiBridge MCP     │   POST /dispatch    │  dispatch_bridge.py   │
│                      │   → 202 + job_id    │                      │
│ claude_runner.py     │                     │  Spawns claude CLI    │
│  _run_claude_http()  │   GET /job/{id}     │  Returns result       │
│  submit + poll       │   → job state       │                      │
└─────────────────────┘                     └──────────────────────┘
```

### Submit + Poll Pattern

The bridge is **fire-and-forget**: `POST /dispatch` validates the request, spawns a background task, and returns HTTP 202 with a `job_id` immediately. The client polls `GET /job/{id}` with exponential backoff (2s → 10s cap) until the job finishes or the deadline is exceeded.

This replaces the old blocking pattern where the HTTP connection was held open for the entire duration of a Claude CLI run (up to 600s).

### Bridge Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | No | Health check |
| `POST` | `/dispatch` | `X-Dispatch-Secret` | Submit a dispatch request, returns 202 |
| `GET` | `/job/{id}` | No | Get job status and result |
| `GET` | `/jobs` | No | List all bridge-level job summaries |

### Recursion Prevention

The bridge clears `CLAUDE_DISPATCH_URL` from its environment before calling `run_claude()`, preventing the CLI runner from routing back to the bridge (which would cause infinite recursion).

## Components

| File | Role |
|------|------|
| `agentibridge/dispatch.py` | Job management (Redis + file), context restore, background task runner |
| `agentibridge/claude_runner.py` | Claude CLI subprocess / HTTP bridge proxy (submit + poll) |
| `agentibridge/dispatch_bridge.py` | Host-side HTTP bridge (Docker mode), fire-and-forget |

## Configuration

```bash
CLAUDE_BINARY=claude
CLAUDE_DISPATCH_MODEL=sonnet
CLAUDE_DISPATCH_TIMEOUT=300

# Docker mode — bridge on host proxies CLI calls
CLAUDE_DISPATCH_URL=http://host.docker.internal:8101
DISPATCH_SECRET=<shared-secret>
DISPATCH_BRIDGE_HOST=0.0.0.0
DISPATCH_BRIDGE_PORT=8101
```
