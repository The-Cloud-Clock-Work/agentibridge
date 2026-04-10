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
from typing import Any, Dict, List, Optional

from agentibridge.logging import log
from agentibridge.redis_client import get_redis

# ---------------------------------------------------------------------------
# Job store (fire-and-forget background tasks)
# ---------------------------------------------------------------------------

_JOBS_DIR = Path("/tmp/agentibridge_jobs")
_JOB_TTL = 86400  # 24h — matches session TTL
_KEY_PREFIX: str = "agentibridge"

# Keep references to running background tasks to prevent GC
_background_tasks: set = set()


def _rkey(suffix: str) -> str:
    """Build a namespaced Redis key for job storage."""
    return f"{_KEY_PREFIX}:sb:{suffix}"


def _job_path(job_id: str) -> Path:
    return _JOBS_DIR / f"{job_id}.json"


def _write_file(job_id: str, data: dict) -> None:
    """Write job state to file (always, as fallback)."""
    _JOBS_DIR.mkdir(parents=True, exist_ok=True)
    _job_path(job_id).write_text(json.dumps(data))


def _write_job(job_id: str, data: dict) -> None:
    """Write job state to Redis (primary) and file (fallback)."""
    # Always write file as fallback
    _write_file(job_id, data)

    # Write to Redis if available
    r = get_redis()
    if r is not None:
        try:
            hash_key = _rkey(f"job:{job_id}")
            # Store as hash — flatten values to strings
            flat = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in data.items()}
            r.hset(hash_key, mapping=flat)
            r.expire(hash_key, _JOB_TTL)
            # Add to sorted set index (score = started_at timestamp or now)
            started = data.get("started_at", "")
            try:
                score = datetime.fromisoformat(started).timestamp()
            except (ValueError, TypeError):
                score = datetime.now(timezone.utc).timestamp()
            r.zadd(_rkey("idx:jobs"), {job_id: score})
        except Exception as e:
            log("dispatch: Redis write failed, file fallback used", {"job_id": job_id, "error": str(e)})


def _read_job_redis(job_id: str) -> Optional[Dict[str, Any]]:
    """Read job state from Redis hash."""
    r = get_redis()
    if r is None:
        return None
    try:
        data = r.hgetall(_rkey(f"job:{job_id}"))
        if not data:
            return None
        # Deserialize JSON-encoded values
        result = {}
        for k, v in data.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result
    except Exception:
        return None


def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    """Read the current state of a background dispatch job.

    Tries Redis first, falls back to file.

    Args:
        job_id: Job UUID returned by dispatch_task

    Returns:
        Dict with status, output, error, etc. or None if not found.
    """
    # Try Redis first
    data = _read_job_redis(job_id)
    if data is not None:
        return data

    # Fall back to file
    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _job_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a job dict with the output field excluded for listing."""
    return {k: v for k, v in data.items() if k != "output"}


def _list_jobs_redis(status: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    """List jobs from Redis, newest first. Returns None if Redis unavailable."""
    r = get_redis()
    if r is None:
        return None
    try:
        jobs: List[Dict[str, Any]] = []
        job_ids = r.zrevrange(_rkey("idx:jobs"), 0, -1)
        for jid in job_ids:
            data = _read_job_redis(jid)
            if data is None:
                continue
            if status and data.get("status") != status:
                continue
            jobs.append(_job_summary(data))
            if len(jobs) >= limit:
                break
        return jobs
    except Exception as e:
        log("dispatch: Redis list_jobs failed, trying file fallback", {"error": str(e)})
        return None


def _list_jobs_files(status: str, limit: int) -> List[Dict[str, Any]]:
    """List jobs from file fallback, newest first."""
    if not _JOBS_DIR.exists():
        return []
    jobs: List[Dict[str, Any]] = []
    files = sorted(_JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        if status and data.get("status") != status:
            continue
        jobs.append(_job_summary(data))
        if len(jobs) >= limit:
            break
    return jobs


def list_jobs(status: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """List dispatch jobs, newest first.

    Args:
        status: Filter by status (e.g. "running", "completed", "failed").
                Empty string means all.
        limit: Maximum number of jobs to return (default: 20).

    Returns:
        List of job summary dicts (output field excluded for brevity).
    """
    result = _list_jobs_redis(status, limit)
    if result is not None:
        return result
    return _list_jobs_files(status, limit)


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


async def handoff(
    project_path: str,
    summary: str,
    decisions: str,
    next_steps: str,
    context: str = "",
    source_session_id: str = "",
    model: str = "sonnet",
) -> Dict[str, Any]:
    """Create a seeded conversation in a target project.

    Blocks until the session is created. Returns the session_id
    so the operator can `claude --resume <session_id>`.
    """
    from agentibridge.claude_runner import run_claude

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Build structured handoff prompt
    parts = [
        f"# HANDOFF — {date_str}\n\n",
        "You are receiving a context handoff from a sibling Claude Code session.\n\n",
        f"## Summary\n{summary}\n\n",
        f"## Key Decisions\n{decisions}\n\n",
        f"## Next Steps\n{next_steps}\n\n",
    ]

    if context:
        parts.append(f"## Additional Context\n{context}\n\n")

    # Inject source session context if provided
    if source_session_id:
        try:
            restored = restore_session_context(source_session_id, last_n=20)
            parts.append(f"## Source Session Context\n{restored}\n\n")
        except Exception as e:
            parts.append(f"## Source Session Context\n(Failed to restore: {e})\n\n")

    parts.append(
        "---\n"
        "Acknowledge this handoff. Summarize your understanding in 2-3 sentences.\n"
        "Do NOT take any action — wait for the operator.\n"
    )

    prompt = "".join(parts)

    # Auto-generate session name from project + date
    project_name = Path(project_path).name
    session_name = f"handoff-{project_name}-{date_str}"

    log("handoff: starting", {"project": project_path, "prompt_len": len(prompt)})

    result = await run_claude(
        prompt=prompt,
        model=model,
        cwd=project_path,
        max_turns=1,
        session_name=session_name,
    )

    if result.success:
        log("handoff: completed", {
            "project": project_path,
            "session_id": result.session_id,
        })
        resume_cmd = f"cd {project_path} && claude --resume {result.session_id}"
        return {
            "success": True,
            "session_id": result.session_id,
            "project_path": project_path,
            "resume_command": resume_cmd,
            "session_name": session_name,
            "output": result.result,
            "duration_ms": result.duration_ms,
        }

    log("handoff: failed", {"project": project_path, "error": result.error})
    return {
        "success": False,
        "project_path": project_path,
        "error": result.error,
        "exit_code": result.exit_code,
    }
