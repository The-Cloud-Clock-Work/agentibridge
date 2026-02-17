#!/usr/bin/env python3
"""Agentic Bridge MCP Server.

Indexes and exposes ALL Claude Code CLI transcripts from
~/.claude/projects/ via MCP tools. Background collector polls
for new data; all tools work with Redis or filesystem fallback.

Usage:
    python -m agentic_bridge

Available tools:
    Phase 1 — Foundation:
    - list_sessions       — List sessions across all projects
    - get_session         — Get full session metadata + transcript
    - get_session_segment — Paginated/time-range transcript retrieval
    - get_session_actions — Extract tool calls with counts
    - search_sessions     — Keyword search across transcripts
    - collect_now         — Trigger immediate collection
    Phase 2 — Semantic Search:
    - search_semantic     — Semantic search using embeddings
    - generate_summary    — Auto-generate session summary via LLM
    Phase 4 — Write-back & Dispatch:
    - restore_session     — Load session context for continuation
    - dispatch_task       — Dispatch a task with optional session context
"""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP

from agentic_bridge.logging import log

# =============================================================================
# MCP SERVER
# =============================================================================

mcp = FastMCP(
    "session-bridge",
    host=os.getenv("SESSION_BRIDGE_HOST", "127.0.0.1"),
    port=int(os.getenv("SESSION_BRIDGE_PORT", "8100")),
)

# Lazy singletons
_store = None
_collector = None
_embedder = None


def _get_store():
    global _store
    if _store is None:
        from agentic_bridge.store import SessionStore
        _store = SessionStore()
    return _store


def _get_collector():
    global _collector
    if _collector is None:
        from agentic_bridge.collector import SessionCollector
        _collector = SessionCollector(_get_store())
        _collector.start()
    return _collector


def _get_embedder():
    global _embedder
    if _embedder is None:
        from agentic_bridge.embeddings import TranscriptEmbedder
        _embedder = TranscriptEmbedder()
    return _embedder


# =============================================================================
# MCP TOOLS
# =============================================================================


@mcp.tool()
def list_sessions(
    project: str = "",
    limit: int = 20,
    offset: int = 0,
    since_hours: int = 0,
) -> str:
    """List Claude Code sessions across all projects, sorted by most recent.

    Args:
        project: Filter by project path substring (e.g., "agenticore")
        limit: Maximum sessions to return (default: 20)
        offset: Skip first N results for pagination (default: 0)
        since_hours: Only sessions active in the last N hours (0 = all)

    Returns:
        JSON with sessions list
    """
    try:
        _get_collector()  # ensure collector is running
        store = _get_store()

        sessions = store.list_sessions(
            project=project if project else None,
            limit=limit,
            offset=offset,
            since_hours=since_hours if since_hours > 0 else 0,
        )

        return json.dumps({
            "success": True,
            "count": len(sessions),
            "offset": offset,
            "sessions": [
                {
                    "session_id": s.session_id,
                    "project_path": s.project_path,
                    "git_branch": s.git_branch,
                    "start_time": s.start_time,
                    "last_update": s.last_update,
                    "num_user_turns": s.num_user_turns,
                    "num_assistant_turns": s.num_assistant_turns,
                    "num_tool_calls": s.num_tool_calls,
                    "summary": s.summary[:200],
                    "has_subagents": s.has_subagents,
                }
                for s in sessions
            ],
        })

    except Exception as e:
        log("MCP list_sessions failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_session(
    session_id: str,
    last_n: int = 50,
    include_meta: bool = True,
) -> str:
    """Get full session details: metadata + conversation transcript.

    Args:
        session_id: Session UUID
        last_n: Number of most recent entries to include (default: 50, 0 = all)
        include_meta: Include session metadata in response (default: True)

    Returns:
        JSON with meta and entries
    """
    try:
        _get_collector()
        store = _get_store()

        result = {"success": True}

        if include_meta:
            meta = store.get_session_meta(session_id)
            if meta:
                result["meta"] = meta.to_dict()
            else:
                return json.dumps({"success": False, "error": f"Session not found: {session_id}"})

        if last_n == 0:
            entries = store.get_session_entries(session_id, offset=0, limit=10000)
        else:
            # Get total count to calculate offset for "last N"
            all_entries = store.get_session_entries(session_id, offset=0, limit=10000)
            start = max(0, len(all_entries) - last_n)
            entries = all_entries[start:]

        result["entries"] = [e.to_dict() for e in entries]
        result["entry_count"] = len(entries)

        return json.dumps(result)

    except Exception as e:
        log("MCP get_session failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_session_segment(
    session_id: str,
    offset: int = 0,
    limit: int = 20,
    since: str = "",
    until: str = "",
) -> str:
    """Get a segment of session transcript by offset/limit or time range.

    Args:
        session_id: Session UUID
        offset: Start from entry N (0-indexed)
        limit: Number of entries to return (default: 20)
        since: ISO timestamp — only entries after this time
        until: ISO timestamp — only entries before this time

    Returns:
        JSON with entries and total_count
    """
    try:
        _get_collector()
        store = _get_store()

        if since or until:
            # Time-based: get all entries and filter
            all_entries = store.get_session_entries(session_id, offset=0, limit=10000)
            filtered = []
            for e in all_entries:
                if since and e.timestamp < since:
                    continue
                if until and e.timestamp > until:
                    continue
                filtered.append(e)
            entries = filtered[:limit]
            total_count = len(filtered)
        else:
            entries = store.get_session_entries(session_id, offset=offset, limit=limit)
            all_entries = store.get_session_entries(session_id, offset=0, limit=10000)
            total_count = len(all_entries)

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "total_count": total_count,
            "offset": offset,
            "count": len(entries),
            "entries": [e.to_dict() for e in entries],
        })

    except Exception as e:
        log("MCP get_session_segment failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_session_actions(
    session_id: str,
    action_types: str = "tool_use",
) -> str:
    """Extract tool calls and file changes from a session.

    Args:
        session_id: Session UUID
        action_types: Types to extract (default: "tool_use")

    Returns:
        JSON with tool call counts and summary
    """
    try:
        _get_collector()
        store = _get_store()

        entries = store.get_session_entries(session_id, offset=0, limit=10000)

        # Count tool usage
        tool_counts: dict[str, int] = {}
        for entry in entries:
            for tool_name in entry.tool_names:
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Sort by count descending
        sorted_tools = sorted(tool_counts.items(), key=lambda x: -x[1])

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "total_tool_calls": sum(tool_counts.values()),
            "unique_tools": len(tool_counts),
            "tools": [
                {"name": name, "count": count}
                for name, count in sorted_tools
            ],
        })

    except Exception as e:
        log("MCP get_session_actions failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def search_sessions(
    query: str,
    project: str = "",
    limit: int = 10,
) -> str:
    """Keyword search across all session transcripts.

    Args:
        query: Search keyword or phrase
        project: Filter to a specific project (substring match)
        limit: Maximum results (default: 10)

    Returns:
        JSON with matching entries from sessions
    """
    try:
        _get_collector()
        store = _get_store()

        results = store.search_sessions(
            query=query,
            project=project if project else None,
            limit=limit,
        )

        return json.dumps({
            "success": True,
            "query": query,
            "count": len(results),
            "matches": results,
        })

    except Exception as e:
        log("MCP search_sessions failed", {"query": query, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def collect_now() -> str:
    """Trigger immediate transcript collection.

    Forces the collector to scan all transcript files now instead of
    waiting for the next polling cycle.

    Returns:
        JSON with collection stats
    """
    try:
        collector = _get_collector()
        stats = collector.collect_once()

        return json.dumps({
            "success": True,
            **stats,
        })

    except Exception as e:
        log("MCP collect_now failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# PHASE 2 — SEMANTIC SEARCH
# =============================================================================


@mcp.tool()
def search_semantic(
    query: str,
    project: str = "",
    limit: int = 10,
) -> str:
    """Semantic search across session transcripts using embeddings.

    Requires embedding backend (EMBEDDING_BACKEND) and Redis to be configured.
    Sessions must be embedded first via embed_session or auto-embedding.

    Args:
        query: Natural language search query
        project: Filter to a specific project (substring match)
        limit: Maximum results (default: 10)

    Returns:
        JSON with semantically similar session matches ranked by score
    """
    try:
        embedder = _get_embedder()
        if not embedder.is_available():
            return json.dumps({
                "success": False,
                "error": "Embedding backend not available. Configure EMBEDDING_BACKEND.",
            })

        results = embedder.search_semantic(
            query=query,
            project=project if project else None,
            limit=limit,
        )

        return json.dumps({
            "success": True,
            "query": query,
            "count": len(results),
            "matches": results,
        })

    except Exception as e:
        log("MCP search_semantic failed", {"query": query, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def generate_summary(
    session_id: str,
) -> str:
    """Generate an AI summary for a session using Claude.

    Reads the session transcript and produces a 2-3 sentence summary
    of what was accomplished, key decisions, and outcomes.

    Args:
        session_id: Session UUID to summarize

    Returns:
        JSON with the generated summary text
    """
    try:
        embedder = _get_embedder()
        summary = embedder.generate_summary(session_id)

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "summary": summary,
        })

    except Exception as e:
        log("MCP generate_summary failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# PHASE 4 — WRITE-BACK & DISPATCH
# =============================================================================


@mcp.tool()
def restore_session(
    session_id: str,
    last_n: int = 20,
) -> str:
    """Load session context blob for injection into a new conversation.

    Extracts the most relevant context from a past session, formatted
    for use as context in a new agent call or conversation.

    Args:
        session_id: Session UUID to restore context from
        last_n: Number of recent turns to include (default: 20)

    Returns:
        JSON with formatted context string ready for injection
    """
    try:
        from agentic_bridge.dispatch import restore_session_context
        context = restore_session_context(session_id, last_n=last_n)

        return json.dumps({
            "success": True,
            "session_id": session_id,
            "context": context,
            "char_count": len(context),
        })

    except Exception as e:
        log("MCP restore_session failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def dispatch_task(
    task_description: str,
    project: str = "",
    session_id: str = "",
    command: str = "default",
    context_turns: int = 10,
) -> str:
    """Dispatch a task to the agent, optionally with context from a past session.

    If session_id is provided, extracts relevant context from that session
    and injects it into the task prompt. Calls the agent via /completions API.

    Args:
        task_description: What the agent should do
        project: Project context hint (optional)
        session_id: Past session to pull context from (optional)
        command: Command preset — default/thinkhard/ultrathink
        context_turns: Number of turns to include from session context

    Returns:
        JSON with dispatch result
    """
    try:
        from agentic_bridge.dispatch import dispatch_task as _dispatch
        result = _dispatch(
            task_description=task_description,
            project=project,
            session_id=session_id,
            command=command,
            context_turns=context_turns,
        )

        return json.dumps({"success": True, **result})

    except Exception as e:
        log("MCP dispatch_task failed", {"task": task_description, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# MAIN
# =============================================================================


def main():
    """Run the session-bridge MCP server."""
    print("Starting session-bridge MCP server...", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    available_tools = mcp._tool_manager.list_tools()
    print(f"Available tools: {len(available_tools)}", file=sys.stderr)
    for tool in available_tools:
        print(f"  - {tool.name}", file=sys.stderr)

    print("=" * 60, file=sys.stderr)

    transport = os.getenv("SESSION_BRIDGE_TRANSPORT", "stdio")
    if transport == "sse":
        from agentic_bridge.transport import run_sse_server
        print(f"Starting SSE transport on {mcp.settings.host}:{mcp.settings.port}...", file=sys.stderr)
        run_sse_server(mcp)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
