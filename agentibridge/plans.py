"""Dispatch planning for AgentiBridge.

Two-phase plan-then-execute workflow:
1. ``submit_plan()`` — runs Claude CLI in read-only mode (Read, Glob, Grep)
   to produce a structured markdown plan.
2. ``execute_plan()`` — runs Claude CLI in normal mode with the plan
   content injected as context.

Plans are persistent user artifacts stored in Redis + file fallback.
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
# Plan store
# ---------------------------------------------------------------------------

_PLANS_DIR = Path("/tmp/agentibridge_plans")
_KEY_PREFIX: str = "agentibridge"

# Keep references to running background tasks to prevent GC
_background_tasks: set = set()

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PLAN_PROMPT = (
    "You are a software architect. Analyze the codebase and create a "
    "detailed implementation plan.\n\n"
    "Task: {task}\n"
    "{repo_context}\n"
    "Create a structured markdown plan with:\n"
    "1. Summary of changes needed\n"
    "2. Files to create or modify\n"
    "3. Step-by-step implementation details\n"
    "4. Dependencies and sequencing\n"
    "5. Testing considerations\n\n"
    "Output ONLY the markdown plan."
)

_EXECUTE_PROMPT = (
    "Execute the following implementation plan:\n\n"
    "---\n{content}\n---\n\n"
    "Implement every step in the plan above. Work top to bottom. Be thorough."
)

# ---------------------------------------------------------------------------
# Storage helpers (mirror dispatch.py patterns)
# ---------------------------------------------------------------------------


def _rkey(suffix: str) -> str:
    """Build a namespaced Redis key for plan storage."""
    return f"{_KEY_PREFIX}:sb:{suffix}"


def _plan_path(plan_id: str) -> Path:
    return _PLANS_DIR / f"{plan_id}.json"


def _write_file(plan_id: str, data: dict) -> None:
    """Write plan state to file (always, as fallback)."""
    _PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _plan_path(plan_id).write_text(json.dumps(data))


def _write_plan(plan_id: str, data: dict) -> None:
    """Write plan state to Redis (primary) and file (fallback).

    Unlike jobs, plans have NO TTL — they are persistent user artifacts.
    """
    _write_file(plan_id, data)

    r = get_redis()
    if r is not None:
        try:
            hash_key = _rkey(f"plan:{plan_id}")
            flat = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in data.items()}
            r.hset(hash_key, mapping=flat)
            # No r.expire() — plans persist indefinitely
            created = data.get("created_at", "")
            try:
                score = datetime.fromisoformat(created).timestamp()
            except (ValueError, TypeError):
                score = datetime.now(timezone.utc).timestamp()
            r.zadd(_rkey("idx:plans"), {plan_id: score})
        except Exception as e:
            log("plans: Redis write failed, file fallback used", {"plan_id": plan_id, "error": str(e)})


def _read_plan_redis(plan_id: str) -> Optional[Dict[str, Any]]:
    """Read plan state from Redis hash."""
    r = get_redis()
    if r is None:
        return None
    try:
        data = r.hgetall(_rkey(f"plan:{plan_id}"))
        if not data:
            return None
        result = {}
        for k, v in data.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result
    except Exception:
        return None


def _plan_summary(data: Dict[str, Any]) -> Dict[str, Any]:
    """Return a plan dict with the content field excluded for listing."""
    return {k: v for k, v in data.items() if k != "content"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def get_plan_status(plan_id: str) -> Optional[Dict[str, Any]]:
    """Read the current state of a dispatch plan.

    Tries Redis first, falls back to file.

    Args:
        plan_id: Plan UUID returned by submit_plan.

    Returns:
        Dict with status, content, error, etc. or None if not found.
    """
    data = _read_plan_redis(plan_id)
    if data is not None:
        return data

    path = _plan_path(plan_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _list_plans_redis(status: str, limit: int) -> Optional[List[Dict[str, Any]]]:
    """List plans from Redis, newest first. Returns None if Redis unavailable."""
    r = get_redis()
    if r is None:
        return None
    try:
        plans: List[Dict[str, Any]] = []
        plan_ids = r.zrevrange(_rkey("idx:plans"), 0, -1)
        for pid in plan_ids:
            data = _read_plan_redis(pid)
            if data is None:
                continue
            if status and data.get("status") != status:
                continue
            plans.append(_plan_summary(data))
            if len(plans) >= limit:
                break
        return plans
    except Exception as e:
        log("plans: Redis list failed, trying file fallback", {"error": str(e)})
        return None


def _list_plans_files(status: str, limit: int) -> List[Dict[str, Any]]:
    """List plans from file fallback, newest first."""
    if not _PLANS_DIR.exists():
        return []
    plans: List[Dict[str, Any]] = []
    files = sorted(_PLANS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        if status and data.get("status") != status:
            continue
        plans.append(_plan_summary(data))
        if len(plans) >= limit:
            break
    return plans


def list_plans(status: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    """List dispatch plans, newest first.

    Args:
        status: Optional filter (planning, ready, failed, executing, completed).
        limit: Max plans to return.

    Returns:
        List of plan dicts (content field excluded).
    """
    redis_result = _list_plans_redis(status, limit)
    if redis_result is not None:
        return redis_result
    return _list_plans_files(status, limit)


# ---------------------------------------------------------------------------
# Background runners
# ---------------------------------------------------------------------------


async def _run_plan_background(plan_id: str, task: str, repo_url: str) -> None:
    """Background task: run Claude CLI in read-only mode, update plan on completion."""
    from agentibridge.claude_runner import run_claude

    repo_context = f"\nRepository: {repo_url}" if repo_url else ""
    prompt = _PLAN_PROMPT.format(task=task, repo_context=repo_context)

    result = await run_claude(
        prompt=prompt,
        model="sonnet",
        output_format="text",
        allowed_tools="Read,Glob,Grep",
        max_turns=15,
        permission_mode="bypassPermissions",
    )

    current = get_plan_status(plan_id)
    if current is None:
        log("plans: plan disappeared during background run", {"plan_id": plan_id})
        return

    now = _now_iso()
    if result.success and result.result:
        current.update(
            {
                "status": "ready",
                "content": result.result,
                "completed_at": now,
                "error": None,
            }
        )
        log("plans: planning completed", {"plan_id": plan_id})
    else:
        current.update(
            {
                "status": "failed",
                "completed_at": now,
                "error": result.error or "Planning failed with no output",
            }
        )
        log("plans: planning failed", {"plan_id": plan_id, "error": result.error})

    _write_plan(plan_id, current)


async def _run_execution_background(plan_id: str, content: str, repo_url: str) -> None:
    """Background task: run Claude CLI with plan content, update plan on completion."""
    from agentibridge.claude_runner import run_claude

    prompt = _EXECUTE_PROMPT.format(content=content)

    result = await run_claude(
        prompt=prompt,
        model="sonnet",
        output_format="json",
    )

    current = get_plan_status(plan_id)
    if current is None:
        log("plans: plan disappeared during execution", {"plan_id": plan_id})
        return

    now = _now_iso()
    if result.success:
        current.update(
            {
                "status": "completed",
                "completed_at": now,
                "error": None,
            }
        )
        log("plans: execution completed", {"plan_id": plan_id})
    else:
        current.update(
            {
                "status": "failed",
                "completed_at": now,
                "error": result.error or "Execution failed",
            }
        )
        log("plans: execution failed", {"plan_id": plan_id, "error": result.error})

    _write_plan(plan_id, current)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def submit_plan(
    task: str,
    repo_url: str = "",
    wait: bool = False,
) -> Dict[str, Any]:
    """Create a plan by running Claude in read-only mode.

    Spawns a background Claude CLI job with restricted tools
    (Read, Glob, Grep only) and returns immediately.

    Args:
        task: What to plan (same format as dispatch_task).
        repo_url: Optional repo to analyze.
        wait: If True, block until plan is ready (default: False).

    Returns:
        Dict with plan_id, status, and optionally content (if wait=True).
    """
    plan_id = str(uuid.uuid4())
    now = _now_iso()

    plan_data = {
        "plan_id": plan_id,
        "status": "planning",
        "task": task,
        "repo_url": repo_url or "",
        "content": "",
        "created_at": now,
        "completed_at": None,
        "planning_job_id": plan_id,
        "execution_job_id": "",
        "error": None,
    }
    _write_plan(plan_id, plan_data)

    log("plans: submitting plan", {"plan_id": plan_id, "task_len": len(task), "wait": wait})

    if wait:
        await _run_plan_background(plan_id, task, repo_url)
        final = get_plan_status(plan_id) or plan_data
        return {
            "plan_id": plan_id,
            "status": final.get("status", "failed"),
            "content": final.get("content"),
            "error": final.get("error"),
        }

    task_obj = asyncio.create_task(_run_plan_background(plan_id, task, repo_url))
    _background_tasks.add(task_obj)
    task_obj.add_done_callback(_background_tasks.discard)

    return {
        "plan_id": plan_id,
        "status": "planning",
    }


async def execute_plan(
    plan_id: str,
    repo_url: str = "",
    wait: bool = False,
) -> Dict[str, Any]:
    """Execute a ready plan.

    Validates the plan is in "ready" status, then spawns a normal
    Claude CLI job with the plan content injected as the task prompt.

    Args:
        plan_id: Plan UUID returned by submit_plan.
        repo_url: Override repo URL (defaults to the one used when planning).
        wait: If True, block until execution completes (default: False).

    Returns:
        Dict with plan_id, execution_job_id, status.

    Raises:
        ValueError: If plan not found or not in "ready" status.
    """
    plan = get_plan_status(plan_id)
    if plan is None:
        raise ValueError(f"Plan not found: {plan_id}")
    if plan.get("status") != "ready":
        raise ValueError(f"Plan {plan_id} not ready (status: {plan.get('status')})")

    content = plan.get("content", "")
    if not content:
        raise ValueError(f"Plan {plan_id} has no content")

    execution_id = str(uuid.uuid4())
    effective_repo = repo_url or plan.get("repo_url", "")

    plan.update(
        {
            "status": "executing",
            "execution_job_id": execution_id,
        }
    )
    _write_plan(plan_id, plan)

    log("plans: executing plan", {"plan_id": plan_id, "execution_id": execution_id})

    if wait:
        await _run_execution_background(plan_id, content, effective_repo)
        final = get_plan_status(plan_id) or plan
        return {
            "plan_id": plan_id,
            "execution_job_id": execution_id,
            "status": final.get("status", "failed"),
            "error": final.get("error"),
        }

    task_obj = asyncio.create_task(_run_execution_background(plan_id, content, effective_repo))
    _background_tasks.add(task_obj)
    task_obj.add_done_callback(_background_tasks.discard)

    return {
        "plan_id": plan_id,
        "execution_job_id": execution_id,
        "status": "executing",
    }
