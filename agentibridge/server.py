#!/usr/bin/env python3
"""AgentiBridge MCP Server.

Indexes and exposes ALL Claude Code CLI transcripts from
~/.claude/projects/ via MCP tools. Background collector polls
for new data; all tools work with Redis or filesystem fallback.

Usage:
    python -m agentibridge

Available tools (17):
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
    - get_dispatch_job    — Poll background job status
    - list_dispatch_jobs  — List dispatch jobs with optional status filter
    Phase 5 — Knowledge Catalog:
    - list_memory_files   — List memory files across projects
    - get_memory_file     — Read a specific memory file
    - list_plans          — List plans sorted by recency
    - get_plan            — Read a plan by codename
    - search_history      — Search global prompt history
"""

import json
import os
import sys
from typing import Dict

from mcp.server.fastmcp import FastMCP

from agentibridge.logging import log

_SUMMARY_TRUNCATE_LENGTH = 200


# =============================================================================
# OAUTH SETUP
# =============================================================================


def _build_oauth_config():
    """Build OAuth provider + settings if OAUTH_ISSUER_URL is set."""
    issuer = os.getenv("OAUTH_ISSUER_URL")
    if not issuer:
        return None, None

    from agentibridge.oauth_provider import BridgeOAuthProvider

    try:
        from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
    except ImportError:
        print("WARNING: mcp package does not support OAuth (upgrade to >=1.26)", file=sys.stderr)
        return None, None

    client_id = os.getenv("OAUTH_CLIENT_ID", "")
    client_secret = os.getenv("OAUTH_CLIENT_SECRET", "")

    provider = BridgeOAuthProvider(
        issuer_url=issuer,
        client_id=client_id,
        client_secret=client_secret,
    )

    # Always enable registration — claude.ai requires it to work.
    # The provider returns pre-configured creds when locked.
    allowed_scopes_raw = os.getenv("OAUTH_ALLOWED_SCOPES", "").strip()
    allowed_scopes_list = [s.strip() for s in allowed_scopes_raw.split() if s.strip()] if allowed_scopes_raw else None

    resource_url = os.getenv("OAUTH_RESOURCE_URL") or (issuer.rstrip("/") + "/mcp")
    settings = AuthSettings(
        issuer_url=issuer,
        resource_server_url=resource_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=allowed_scopes_list,
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
    return provider, settings


# =============================================================================
# MCP SERVER
# =============================================================================

_oauth_provider, _oauth_settings = _build_oauth_config()

mcp = FastMCP(
    "agentibridge",
    host=os.getenv("AGENTIBRIDGE_HOST", "127.0.0.1"),
    port=int(os.getenv("AGENTIBRIDGE_PORT", "8100")),
    json_response=True,
    auth_server_provider=_oauth_provider,
    auth=_oauth_settings,
)

# Lazy singletons
_store = None
_collector = None
_embedder = None


def _get_store():
    global _store
    if _store is None:
        from agentibridge.store import SessionStore

        _store = SessionStore()
    return _store


def _get_collector():
    global _collector
    if _collector is None:
        from agentibridge.config import AGENTIBRIDGE_ENABLED, AGENTIBRIDGE_EMBEDDING_ENABLED
        from agentibridge.collector import SessionCollector

        embedder = _get_embedder() if AGENTIBRIDGE_EMBEDDING_ENABLED else None
        _collector = SessionCollector(_get_store(), embedder=embedder)
        if AGENTIBRIDGE_ENABLED:
            _collector.start()
    return _collector


def _get_embedder():
    global _embedder
    if _embedder is None:
        from agentibridge.embeddings import TranscriptEmbedder

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

        return json.dumps(
            {
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
                        "summary": s.summary[:_SUMMARY_TRUNCATE_LENGTH],
                        "has_subagents": s.has_subagents,
                    }
                    for s in sessions
                ],
            }
        )

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
            # Use count_entries to avoid loading all entries just for the count
            total = store.count_entries(session_id)
            start = max(0, total - last_n)
            entries = store.get_session_entries(session_id, offset=start, limit=last_n)

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
            total_count = store.count_entries(session_id)

        return json.dumps(
            {
                "success": True,
                "session_id": session_id,
                "total_count": total_count,
                "offset": offset,
                "count": len(entries),
                "entries": [e.to_dict() for e in entries],
            }
        )

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
        tool_counts: Dict[str, int] = {}
        for entry in entries:
            for tool_name in entry.tool_names:
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1

        # Sort by count descending
        sorted_tools = sorted(tool_counts.items(), key=lambda x: -x[1])

        return json.dumps(
            {
                "success": True,
                "session_id": session_id,
                "total_tool_calls": sum(tool_counts.values()),
                "unique_tools": len(tool_counts),
                "tools": [{"name": name, "count": count} for name, count in sorted_tools],
            }
        )

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

        return json.dumps(
            {
                "success": True,
                "query": query,
                "count": len(results),
                "matches": results,
            }
        )

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

        return json.dumps(
            {
                "success": True,
                **stats,
            }
        )

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

    Requires LLM API configured (LLM_API_BASE + LLM_API_KEY + LLM_EMBED_MODEL) and Postgres (pgvector).
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
            return json.dumps(
                {
                    "success": False,
                    "error": "Embedding backend not available. Configure LLM_API_BASE + LLM_API_KEY + LLM_EMBED_MODEL and POSTGRES_URL.",
                }
            )

        results = embedder.search_semantic(
            query=query,
            project=project if project else None,
            limit=limit,
        )

        return json.dumps(
            {
                "success": True,
                "query": query,
                "count": len(results),
                "matches": results,
            }
        )

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

        return json.dumps(
            {
                "success": True,
                "session_id": session_id,
                "summary": summary,
            }
        )

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
        from agentibridge.dispatch import restore_session_context

        context = restore_session_context(session_id, last_n=last_n)

        return json.dumps(
            {
                "success": True,
                "session_id": session_id,
                "context": context,
                "char_count": len(context),
            }
        )

    except Exception as e:
        log("MCP restore_session failed", {"session_id": session_id, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def dispatch_task(
    task_description: str,
    project: str = "",
    session_id: str = "",
    resume_session_id: str = "",
    command: str = "default",
    context_turns: int = 10,
) -> str:
    """Dispatch a task to the agent as a background job (fire-and-forget).

    Returns immediately with a job_id. Use get_dispatch_job(job_id) to
    check status and retrieve output when the task completes.

    Two ways to use a past session:
    - session_id: load context from the session and inject it into a new prompt
    - resume_session_id: actually resume the session thread via ``--resume``
      (continues the existing conversation with full memory, no injection needed)

    Args:
        task_description: What the agent should do
        project: Project context hint (optional)
        session_id: Past session to pull context from (optional)
        resume_session_id: Session to resume via --resume flag (optional)
        command: Command preset — default/thinkhard/ultrathink
        context_turns: Number of turns to include from session context

    Returns:
        JSON with job_id and status "running"
    """
    try:
        from agentibridge.dispatch import dispatch_task as _dispatch

        result = await _dispatch(
            task_description=task_description,
            project=project,
            session_id=session_id,
            resume_session_id=resume_session_id,
            command=command,
            context_turns=context_turns,
        )

        return json.dumps({"success": True, **result})

    except Exception as e:
        log("MCP dispatch_task failed", {"task": task_description, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def get_dispatch_job(job_id: str) -> str:
    """Get the status and output of a background dispatch job.

    Args:
        job_id: Job UUID returned by dispatch_task

    Returns:
        JSON with status ("running", "completed", "failed"), output, error,
        duration_ms, claude_session_id, and other metadata.
    """
    from agentibridge.dispatch import get_job_status

    data = get_job_status(job_id)
    if data is None:
        return json.dumps({"success": False, "error": f"Job not found: {job_id}"})
    return json.dumps({"success": True, **data})


@mcp.tool()
async def list_dispatch_jobs(status: str = "", limit: int = 20) -> str:
    """List dispatch jobs with optional status filter.

    Returns job summaries (newest first) without the full output field,
    so the response stays compact even with many jobs.

    Args:
        status: Filter by status ("running", "completed", "failed"). Empty = all.
        limit: Maximum number of jobs to return (default: 20)

    Returns:
        JSON with jobs list and count
    """
    try:
        from agentibridge.dispatch import list_jobs

        jobs = list_jobs(status=status, limit=limit)
        return json.dumps({"success": True, "count": len(jobs), "jobs": jobs})

    except Exception as e:
        log("MCP list_dispatch_jobs failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# PHASE 4b — DISPATCH PLANS (plan-then-execute workflow)
# =============================================================================


@mcp.tool()
async def plan_task(
    task: str,
    repo_url: str = "",
    wait: bool = False,
    timeout: int = 0,
) -> str:
    """Create an implementation plan without executing it.

    Runs Claude in read-only mode (Read, Glob, Grep only) to analyse the
    codebase and produce a markdown plan. The plan can later be executed
    with execute_plan.

    Args:
        task: What to plan (same format as dispatch_task)
        repo_url: Repo to analyse (optional)
        wait: Block until plan is ready (default: false)
        timeout: Timeout in seconds (0 = use default from env)

    Returns:
        JSON with plan_id, job_id, status, and (if wait=true) the plan content
    """
    try:
        from agentibridge.plans import submit_plan

        result = await submit_plan(task=task, repo_url=repo_url, wait=wait, timeout=timeout)
        return json.dumps({"success": True, **result})

    except Exception as e:
        log("MCP plan_task failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def get_dispatch_plan(plan_id: str) -> str:
    """Get a plan by ID, including its markdown content once ready.

    Args:
        plan_id: Plan UUID returned by plan_task

    Returns:
        JSON with plan details including status and content
    """
    try:
        from agentibridge.plans import get_plan_status

        data = get_plan_status(plan_id)
        if data is None:
            return json.dumps({"success": False, "error": f"Plan not found: {plan_id}"})
        return json.dumps({"success": True, **data})

    except Exception as e:
        log("MCP get_dispatch_plan failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def list_dispatch_plans(status: str = "", limit: int = 20) -> str:
    """List dispatch plans with optional status filter.

    Returns plan summaries (newest first) without the content field,
    so the response stays compact even with many plans.

    Args:
        status: Filter by status (planning/ready/failed/executing/completed). Empty = all.
        limit: Maximum number of plans to return (default: 20)

    Returns:
        JSON with plans list and count
    """
    try:
        from agentibridge.plans import list_plans as list_dispatch_plans_fn

        plans = list_dispatch_plans_fn(status=status, limit=limit)
        return json.dumps({"success": True, "count": len(plans), "plans": plans})

    except Exception as e:
        log("MCP list_dispatch_plans failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
async def execute_plan(
    plan_id: str,
    repo_url: str = "",
    wait: bool = False,
    timeout: int = 0,
) -> str:
    """Execute a ready plan by ID.

    Submits a normal coding job with the plan injected as context.

    Args:
        plan_id: Plan ID returned by plan_task
        repo_url: Override repo URL (defaults to the one used when planning)
        wait: Block until execution completes
        timeout: Timeout in seconds (0 = use default from env)

    Returns:
        JSON with job_id and status
    """
    try:
        from agentibridge.plans import execute_plan as execute_plan_fn

        result = await execute_plan_fn(plan_id=plan_id, repo_url=repo_url, wait=wait, timeout=timeout)
        return json.dumps({"success": True, **result})

    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    except Exception as e:
        log("MCP execute_plan failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# PHASE 5 — KNOWLEDGE CATALOG (Memory, Plans, History)
# =============================================================================


@mcp.tool()
def list_memory_files(project: str = "") -> str:
    """List all memory files across projects.

    Memory files (~/.claude/projects/{project}/memory/*.md) contain curated
    project knowledge — the highest-signal content per project.

    Args:
        project: Filter by project path substring (e.g., "agentibridge")

    Returns:
        JSON with files list
    """
    try:
        _get_collector()
        store = _get_store()

        files = store.list_memory_files(project=project if project else None)

        return json.dumps(
            {
                "success": True,
                "count": len(files),
                "files": [
                    {
                        "project_path": f.project_path,
                        "project_encoded": f.project_encoded,
                        "filename": f.filename,
                        "file_size_bytes": f.file_size_bytes,
                        "last_modified": f.last_modified,
                    }
                    for f in files
                ],
            }
        )

    except Exception as e:
        log("MCP list_memory_files failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_memory_file(project: str, filename: str = "MEMORY.md") -> str:
    """Read a specific memory file's content.

    Args:
        project: Project encoded name (e.g., "-home-user-dev-myapp")
        filename: Memory filename (default: "MEMORY.md")

    Returns:
        JSON with project_path, filename, content, file_size_bytes, last_modified
    """
    try:
        _get_collector()
        store = _get_store()

        mem = store.get_memory_file(project, filename)
        if mem is None:
            return json.dumps({"success": False, "error": f"Memory file not found: {project}/{filename}"})

        return json.dumps(
            {
                "success": True,
                "project_path": mem.project_path,
                "project_encoded": mem.project_encoded,
                "filename": mem.filename,
                "content": mem.content,
                "file_size_bytes": mem.file_size_bytes,
                "last_modified": mem.last_modified,
            }
        )

    except Exception as e:
        log("MCP get_memory_file failed", {"project": project, "filename": filename, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def list_plans(
    project: str = "",
    codename: str = "",
    limit: int = 30,
    offset: int = 0,
    include_agent_plans: bool = False,
) -> str:
    """List plans sorted by recency.

    Plans (~/.claude/plans/*.md) are detailed implementation blueprints
    linked to sessions via the codename/slug field.

    Args:
        project: Filter by project path substring
        codename: Filter by codename substring
        limit: Maximum plans to return (default: 30)
        offset: Skip first N results for pagination
        include_agent_plans: Include agent subplans (default: False)

    Returns:
        JSON with plans list
    """
    try:
        _get_collector()
        store = _get_store()

        plans = store.list_plans(
            project=project if project else None,
            codename=codename if codename else None,
            limit=limit,
            offset=offset,
            include_agent_plans=include_agent_plans,
        )

        return json.dumps(
            {
                "success": True,
                "count": len(plans),
                "offset": offset,
                "plans": [
                    {
                        "codename": p.codename,
                        "file_size_bytes": p.file_size_bytes,
                        "last_modified": p.last_modified,
                        "is_agent_plan": p.is_agent_plan,
                        "parent_codename": p.parent_codename,
                        "session_ids": p.session_ids,
                        "project_path": p.project_path,
                    }
                    for p in plans
                ],
            }
        )

    except Exception as e:
        log("MCP list_plans failed", {"error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def get_plan(codename: str, include_agent_plans: bool = False) -> str:
    """Read a plan by codename.

    Args:
        codename: Plan codename (e.g., "cached-wondering-sloth")
        include_agent_plans: Include agent subplans (default: False)

    Returns:
        JSON with codename, content, session_ids, project_path, agent_plans
    """
    try:
        _get_collector()
        store = _get_store()

        result = store.get_plan(codename, include_agent_plans=include_agent_plans)
        if result is None:
            return json.dumps({"success": False, "error": f"Plan not found: {codename}"})

        plan = result["plan"]
        response = {
            "success": True,
            "codename": plan.codename,
            "content": plan.content,
            "file_size_bytes": plan.file_size_bytes,
            "last_modified": plan.last_modified,
            "session_ids": plan.session_ids,
            "project_path": plan.project_path,
        }

        if include_agent_plans:
            response["agent_plans"] = [
                {
                    "codename": ap.codename,
                    "content": ap.content,
                    "file_size_bytes": ap.file_size_bytes,
                    "last_modified": ap.last_modified,
                }
                for ap in result["agent_plans"]
            ]
        else:
            response["agent_plans"] = []

        return json.dumps(response)

    except Exception as e:
        log("MCP get_plan failed", {"codename": codename, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


@mcp.tool()
def search_history(
    query: str = "",
    project: str = "",
    session_id: str = "",
    limit: int = 20,
    offset: int = 0,
    since: str = "",
) -> str:
    """Search the global prompt history.

    History (~/.claude/history.jsonl) contains every user prompt across all
    sessions with timestamps, project paths, and session UUIDs.

    Args:
        query: Search keyword or phrase (empty = all)
        project: Filter by project path substring
        session_id: Filter by session UUID
        limit: Maximum results (default: 20)
        offset: Skip first N results for pagination
        since: ISO timestamp — only entries after this time

    Returns:
        JSON with entries list and total count
    """
    try:
        _get_collector()
        store = _get_store()

        entries, total = store.search_history(
            query=query,
            project=project if project else None,
            session_id=session_id if session_id else None,
            limit=limit,
            offset=offset,
            since=since,
        )

        return json.dumps(
            {
                "success": True,
                "total": total,
                "count": len(entries),
                "offset": offset,
                "entries": [e.to_dict() for e in entries],
            }
        )

    except Exception as e:
        log("MCP search_history failed", {"query": query, "error": str(e)})
        return json.dumps({"success": False, "error": str(e)})


# =============================================================================
# MAIN
# =============================================================================


def main():
    """Run the AgentiBridge MCP server."""
    from agentibridge.config import AGENTIBRIDGE_REMOVE_TOOLS

    print("Starting AgentiBridge MCP server...", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    for name in AGENTIBRIDGE_REMOVE_TOOLS:
        try:
            mcp._tool_manager.remove_tool(name)
            print(f"  Removed tool: {name}", file=sys.stderr)
        except Exception:
            print(f"  Warning: tool '{name}' not found, skipping", file=sys.stderr)

    available_tools = mcp._tool_manager.list_tools()
    print(f"Available tools: {len(available_tools)}", file=sys.stderr)
    for tool in available_tools:
        print(f"  - {tool.name}", file=sys.stderr)

    print("=" * 60, file=sys.stderr)

    # Start collector eagerly so indexing + embedding begin immediately
    _get_collector()

    transport = os.getenv("AGENTIBRIDGE_TRANSPORT", "stdio")
    if transport == "sse":
        from agentibridge.transport import run_sse_server

        print(f"Starting SSE transport on {mcp.settings.host}:{mcp.settings.port}...", file=sys.stderr)
        run_sse_server(mcp)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
