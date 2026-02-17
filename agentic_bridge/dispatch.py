"""Session restore and task dispatch for Agentic Bridge.

Enables:
1. Extracting context from past sessions for injection into new conversations
2. Dispatching tasks to agents with optional session context

Uses agentic_bridge.completions for agent dispatch via /completions API.
"""

from typing import Any, Dict, Optional

from agentic_bridge.logging import log


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
    from agentic_bridge.store import SessionStore

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


def dispatch_task(
    task_description: str,
    project: str = "",
    session_id: str = "",
    command: str = "default",
    context_turns: int = 10,
) -> Dict[str, Any]:
    """Dispatch a task to the agent, optionally injecting session context.

    1. If session_id is provided, extracts context from that session
    2. Builds prompt combining task description + session context
    3. Calls agent via /completions API
    4. Returns result

    Args:
        task_description: What the agent should do
        project: Project context hint
        session_id: Past session to pull context from (optional)
        command: Command preset (default/thinkhard/ultrathink)
        context_turns: Number of turns to include from session context

    Returns:
        Dict with dispatch result, including success status and output
    """
    from agentic_bridge.completions import call_completions

    # Build prompt
    prompt_parts = []

    # Inject session context if provided
    if session_id:
        try:
            context = restore_session_context(session_id, last_n=context_turns)
            prompt_parts.append(
                "The following is context from a previous session. "
                "Use it to inform your work on the current task:\n\n"
                + context
                + "\n\n"
            )
        except Exception as e:
            log("dispatch_task: context restore failed", {
                "session_id": session_id,
                "error": str(e),
            })
            prompt_parts.append(
                f"(Note: Failed to restore session context: {e})\n\n"
            )

    # Add project context if provided
    if project:
        prompt_parts.append(f"Project: {project}\n\n")

    # Add the actual task
    prompt_parts.append(f"Task: {task_description}")

    full_prompt = "".join(prompt_parts)

    # Dispatch via completions API
    result = call_completions(
        prompt=full_prompt,
        command=command,
        wait=True,
        stateless=True,
    )

    return {
        "dispatched": True,
        "completed": result.success,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "output": result.parsed_output,
        "error": result.error,
        "context_session": session_id or None,
        "prompt_length": len(full_prompt),
    }
