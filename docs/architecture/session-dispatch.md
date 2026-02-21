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

The agent will call `get_dispatch_job` on the relevant job_id without needing it repeated. If checking from a **new conversation**, note the job_id or add `list_dispatch_jobs` (not yet implemented) to browse recent jobs.

## Permissions

All dispatched Claude CLI invocations run with `--dangerously-skip-permissions`. No approval prompts will block background jobs.

## Job Storage

Job state is persisted to `/tmp/agentibridge_jobs/<job_id>.json`. Jobs survive MCP server restarts as long as the filesystem is intact. Background tasks are held in memory via `asyncio.create_task()` with GC protection while running.

## Command Presets

| `command`    | Model  |
|-------------|--------|
| `default`   | Sonnet |
| `thinkhard` | Sonnet |
| `ultrathink`| Opus   |

## Components

| File | Role |
|------|------|
| `agentibridge/dispatch.py` | Job management, context restore, background task runner |
| `agentibridge/claude_runner.py` | Claude CLI subprocess / HTTP bridge proxy |
| `agentibridge/dispatch_bridge.py` | Host-side HTTP bridge (Docker mode) |

## Configuration

```bash
CLAUDE_BINARY=claude
CLAUDE_DISPATCH_MODEL=sonnet
CLAUDE_DISPATCH_TIMEOUT=300

# Docker mode — bridge on host proxies CLI calls
CLAUDE_DISPATCH_URL=http://host.docker.internal:8101
DISPATCH_SECRET=<shared-secret>
```
