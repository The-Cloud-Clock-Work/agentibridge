"""Session restore and task dispatch for AgentiBridge.

Enables:
1. Extracting context from past sessions for injection into new conversations
2. Dispatching tasks to agents via the Claude CLI (no external API needed)
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from agentibridge.logging import log

# ---------------------------------------------------------------------------
# Job store (fire-and-forget background tasks)
# ---------------------------------------------------------------------------

_JOBS_DIR = Path("/tmp/agentibridge_jobs")

# Keep references to running background tasks to prevent GC
_background_tasks: set = set()


def _job_path(job_id: str) -> Path:
    return _JOBS_DIR / f"{job_id}.json"


def _write_job(job_id: str, data: dict) -> None:
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _job_path(job_id).write_text(json.dumps(data))


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Read the current state of a background dispatch job.

    Args:
        job_id: Job UUID returned by dispatch_task

    Returns:
        Dict with status, output, error, etc. or None if not found.
    """
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Session context restore
# ---------------------------------------------------------------------------


def restore_session_context(session_id: str, last_n: int = 20) -> str:
    """Extract relevant context from a past session.

    Builds a formatted context string from the session's metadata and
    recent transcript entries, suitable for injection into a new agent
    call's system prompt or context field.

    Args:
        session_id: Session UUID to restore context from
        last_n: Number of recent turns to include

    Returns:
        Formatted context string
    """
    from agentibridge.store import SessionStore

    store = SessionStore()
    meta = store.get_session_meta(session_id)
    if not meta:
        raise ValueError(f"Session not found: {session_id}")

    entries = store.get_session_entries(session_id, offset=0, limit=10000)

    # Take last N entries
    if last_n and len(entries) > last_n:
        entries = entries[-last_n:]

    # Build context header
    lines = [
        "=" * 60,
        "RESTORED SESSION CONTEXT",
        "=" * 60,
        f"Session ID: {meta.session_id}",
        f"Project: {meta.project_path}",
        f"Branch: {meta.git_branch}",
        f"Started: {meta.start_time}",
        f"Last Active: {meta.last_update}",
        f"Stats: {meta.num_user_turns} user turns, {meta.num_assistant_turns} assistant turns, {meta.num_tool_calls} tool calls",
    ]

    if meta.summary:
        lines.append(f"Summary: {meta.summary}")

    lines.append("-" * 60)
    lines.append("RECENT CONVERSATION:")
    lines.append("-" * 60)

    # Format entries
    for entry in entries:
        if entry.entry_type == "user":
            lines.append(f"\n[USER] {entry.content[:1000]}")
        elif entry.entry_type == "assistant":
            tools = ""
            if entry.tool_names:
                tools = f" (tools: {', '.join(entry.tool_names)})"
            lines.append(f"\n[ASSISTANT]{tools} {entry.content[:1000]}")
        elif entry.entry_type == "summary":
            lines.append(f"\n[SUMMARY] {entry.content[:1000]}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("END OF RESTORED CONTEXT")
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def dispatch_task(
    task_description: str,
    project: str = "",
    session_id: str = "",
    resume_session_id: str = "",
    command: str = "default",
    context_turns: int = 10,
) -> Dict[str, Any]:
    """Dispatch a task to the Claude CLI as a background job.

    Returns immediately with a job_id. Use get_job_status(job_id) to
    check progress and retrieve output when done.

    Args:
        task_description: What the agent should do
        project: Project context hint
        session_id: Past session to pull context from (context injection)
        resume_session_id: Session to resume via --resume flag (actual continuation)
        command: Command preset (default/thinkhard/ultrathink)
        context_turns: Number of turns to include from session context

    Returns:
        Dict with job_id and initial status
    """
    from agentibridge.claude_runner import run_claude

    # Map command presets to model names
    model_map = {
        "default": "sonnet",
        "thinkhard": "sonnet",
        "ultrathink": "opus",
    }
    model = model_map.get(command, "sonnet")

    # Build prompt
    prompt_parts = []

    # Inject session context if provided
    if session_id:
        try:
            context = restore_session_context(session_id, last_n=context_turns)
            prompt_parts.append(
                "The following is context from a previous session. "
                "Use it to inform your work on the current task:\n\n" + context + "\n\n"
            )
        except Exception as e:
            log(
                "dispatch_task: context restore failed",
                {
                    "session_id": session_id,
                    "error": str(e),
                },
            )
            prompt_parts.append(f"(Note: Failed to restore session context: {e})\n\n")

    # Add project context if provided
    if project:
        prompt_parts.append(f"Project: {project}\n\n")

    # Add the actual task
    prompt_parts.append(f"Task: {task_description}")

    full_prompt = "".join(prompt_parts)

    # Generate job ID and write initial state
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _write_job(
        job_id,
        {
            "job_id": job_id,
            "status": "running",
            "started_at": now,
            "task": task_description,
            "project": project,
            "context_session": session_id or None,
            "resumed_session": resume_session_id or None,
            "prompt_length": len(full_prompt),
            "output": None,
            "error": None,
            "exit_code": None,
            "duration_ms": None,
            "completed_at": None,
            "claude_session_id": None,
        },
    )

    log("dispatch_task: started background job", {"job_id": job_id, "prompt_len": len(full_prompt)})

    async def _run_background() -> None:
        try:
            result = await run_claude(
                prompt=full_prompt,
                model=model,
                resume_session_id=resume_session_id or None,
            )
            completed_at = datetime.now(timezone.utc).isoformat()
            _write_job(
                job_id,
                {
                    "job_id": job_id,
                    "status": "completed" if result.success else "failed",
                    "started_at": now,
                    "completed_at": completed_at,
                    "task": task_description,
                    "project": project,
                    "context_session": session_id or None,
                    "resumed_session": resume_session_id or None,
                    "prompt_length": len(full_prompt),
                    "output": result.result,
                    "error": result.error,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "timed_out": result.timed_out,
                    "claude_session_id": result.session_id,
                },
            )
            log("dispatch_task: job finished", {"job_id": job_id, "success": result.success})
        except Exception as e:
            completed_at = datetime.now(timezone.utc).isoformat()
            _write_job(
                job_id,
                {
                    "job_id": job_id,
                    "status": "failed",
                    "started_at": now,
                    "completed_at": completed_at,
                    "task": task_description,
                    "project": project,
                    "error": str(e),
                    "output": None,
                },
            )
            log("dispatch_task: job error", {"job_id": job_id, "error": str(e)})

    task = asyncio.create_task(_run_background())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "dispatched": True,
        "background": True,
        "job_id": job_id,
        "status": "running",
        "context_session": session_id or None,
        "resumed_session": resume_session_id or None,
        "prompt_length": len(full_prompt),
    }
