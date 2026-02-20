# Session Dispatch & Context Restore

Session continuity and task delegation capabilities. Agents can restore context from past sessions and dispatch new tasks with that context injected, enabling multi-session workflows and context-aware task handoff.

## Architecture

```
Past Session                       New Task
(transcript)                       (with context)
     |                                 |
     v                                 v
restore_session_context()    -->  dispatch_task()
     |                                 |
     v                                 v
Formatted context blob        Claude CLI subprocess
(metadata + entries)          (prompt + injected context)
                                       |
                                       v
                                Claude executes task
                                with session awareness
```

## Components

### `agentibridge/dispatch.py`

Two main functions:

#### `restore_session_context(session_id, last_n=20) -> str`

Extracts and formats context from a past session:

```
============================================================
RESTORED SESSION CONTEXT
============================================================
Session ID: abc-123-def
Project: /home/user/dev/myapp
Branch: feature/auth
Started: 2025-01-15T10:00:00Z
Last Active: 2025-01-15T11:30:00Z
Stats: 15 user turns, 15 assistant turns, 42 tool calls
Summary: Implemented OAuth2 login flow with JWT tokens
------------------------------------------------------------
RECENT CONVERSATION:
------------------------------------------------------------

[USER] Add password reset endpoint

[ASSISTANT] (tools: Read, Edit, Write) Created /api/auth/reset...

[USER] Now add email verification

[ASSISTANT] (tools: Write, Bash) Added email verification...

============================================================
END OF RESTORED CONTEXT
============================================================
```

#### `dispatch_task(task_description, project, session_id, command, context_turns) -> dict`

Dispatches a task via the Claude CLI as a subprocess:

1. If `session_id` provided, calls `restore_session_context()` to get context
2. Builds a prompt: context + project hint + task description
3. Runs Claude CLI via `agentibridge.claude_runner`
4. Returns result with dispatch metadata

**Return format:**
```json
{
  "dispatched": true,
  "completed": true,
  "exit_code": 0,
  "duration_ms": 45000,
  "timed_out": false,
  "output": {"result": "..."},
  "error": null,
  "context_session": "abc-123-def",
  "prompt_length": 2500
}
```

## MCP Tools Added

### `restore_session`

```
Args: session_id (str), last_n (int, default 20)
Returns: JSON with formatted context string + char_count
```

Load context from a past session for injection into a new conversation. Useful when an agent needs to continue work from a previous session.

### `dispatch_task`

```
Args: task_description (str), project (str), session_id (str),
      command (str, default "default"), context_turns (int, default 10)
Returns: JSON with dispatch result
```

Dispatch a task to the agent, optionally injecting context from a past session. The `command` parameter controls model selection:
- `default` — Sonnet
- `thinkhard` — Sonnet
- `ultrathink` — Opus

## Use Cases

### 1. Session Continuation
An agent can pick up where a previous session left off:

```
restore_session("previous-session-id", last_n=30)
-> Formatted context with recent conversation
```

### 2. Context-Aware Task Delegation
Dispatch a follow-up task with awareness of past work:

```
dispatch_task(
    task_description="Deploy the auth changes to staging",
    session_id="auth-implementation-session",
    command="thinkhard",
    context_turns=15,
)
```

### 3. Multi-Agent Handoff
Agent A completes research, Agent B receives context:

```
# Agent B receives Agent A's session context
context = restore_session("agent-a-research-session", last_n=50)
# Agent B uses this to inform its own work
```

## Dependencies

- `agentibridge.store` — SessionStore for reading sessions
- `agentibridge.claude_runner` — Claude CLI subprocess runner for dispatch
- Claude CLI binary must be available at `CLAUDE_BINARY` path for dispatch_task to work

## Configuration

```bash
# Claude CLI dispatch (for dispatch_task)
CLAUDE_BINARY=claude
CLAUDE_DISPATCH_MODEL=sonnet
CLAUDE_DISPATCH_TIMEOUT=300
```
