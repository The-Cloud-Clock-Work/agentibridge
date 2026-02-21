"""Run Claude CLI directly via subprocess, or proxy via HTTP bridge.

Replaces the old completions.py module that called an external agenticore
/completions API.  Now AgentiBridge is fully standalone — it shells out to
the ``claude`` CLI binary which must be on PATH (or set via CLAUDE_BINARY).

When ``CLAUDE_DISPATCH_URL`` is set, requests are proxied to a host-side
dispatch bridge (see :mod:`agentibridge.dispatch_bridge`) instead of
spawning ``claude`` locally. This is used when running inside Docker.

Usage:
    from agentibridge.claude_runner import run_claude_sync

    result = run_claude_sync("Summarize this code")
    if result["success"]:
        print(result["result"])

Env vars:
    CLAUDE_BINARY          — path to claude CLI (default: "claude")
    CLAUDE_DISPATCH_MODEL  — model for dispatch (default: "sonnet")
    CLAUDE_DISPATCH_TIMEOUT — timeout in seconds (default: 300)
    CLAUDE_DISPATCH_URL    — bridge URL (empty = local mode)
    DISPATCH_SECRET        — shared secret for bridge auth
"""

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from agentibridge.logging import log


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _claude_binary() -> str:
    return os.environ.get("CLAUDE_BINARY", "claude")


def _default_model() -> str:
    return os.environ.get("CLAUDE_DISPATCH_MODEL", "sonnet")


def _default_timeout() -> int:
    return int(os.environ.get("CLAUDE_DISPATCH_TIMEOUT", "300"))


def _dispatch_url() -> str:
    return os.environ.get("CLAUDE_DISPATCH_URL", "")


def _dispatch_secret() -> str:
    return os.environ.get("DISPATCH_SECRET", "")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ClaudeResult:
    """Result from a Claude CLI invocation."""

    success: bool
    result: Optional[str] = None
    session_id: Optional[str] = None
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    timed_out: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------


def parse_claude_output(raw: str) -> Dict[str, Any]:
    """Parse JSON output from ``claude --output-format json``.

    The CLI emits a JSON object with fields like:
      - result (str)          — the final text answer
      - session_id (str)      — Claude session UUID
      - cost_usd (float)      — cost in USD
      - duration_ms (int)     — wall-clock time
      - duration_api_ms (int) — API time
      - is_error (bool)

    Returns a flat dict; callers pick the keys they need.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"result": raw, "parse_error": True}

    return data


# ---------------------------------------------------------------------------
# HTTP transport (container → host bridge)
# ---------------------------------------------------------------------------


async def _run_claude_http(
    dispatch_url: str,
    prompt: str,
    model: str,
    timeout: int,
    output_format: str,
    resume_session_id: Optional[str] = None,
) -> ClaudeResult:
    """Proxy a dispatch request to the host-side bridge via HTTP.

    Args:
        dispatch_url: Base URL of the dispatch bridge (e.g. http://host.docker.internal:8101).
        prompt: The prompt/task text.
        model: Model name.
        timeout: Timeout in seconds for the Claude CLI execution.
        output_format: CLI output format.

    Returns:
        ClaudeResult with parsed output.
    """
    import httpx

    secret = _dispatch_secret()
    url = f"{dispatch_url.rstrip('/')}/dispatch"
    # Give the bridge time to timeout first, then add buffer for HTTP overhead
    http_timeout = timeout + 30

    log("claude_runner: HTTP dispatch", {"url": url, "model": model, "prompt_len": len(prompt)})

    try:
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resp = await client.post(
                url,
                json={
                    "prompt": prompt,
                    "model": model,
                    "timeout": timeout,
                    "output_format": output_format,
                    "resume_session_id": resume_session_id or "",
                },
                headers={"X-Dispatch-Secret": secret},
            )

        if resp.status_code == 401:
            return ClaudeResult(success=False, error="Dispatch bridge auth failed (401)")

        if resp.status_code != 200:
            return ClaudeResult(
                success=False,
                error=f"Dispatch bridge returned HTTP {resp.status_code}: {resp.text[:500]}",
            )

        data = resp.json()
        return ClaudeResult(
            success=data.get("success", False),
            result=data.get("result"),
            session_id=data.get("session_id"),
            exit_code=data.get("exit_code"),
            duration_ms=data.get("duration_ms"),
            timed_out=data.get("timed_out", False),
            error=data.get("error"),
        )

    except httpx.ConnectError as e:
        msg = f"Cannot connect to dispatch bridge at {dispatch_url}: {e}"
        log("claude_runner: bridge connect error", {"url": dispatch_url, "error": str(e)})
        return ClaudeResult(success=False, error=msg)

    except httpx.TimeoutException:
        log("claude_runner: bridge timeout", {"url": dispatch_url, "timeout": http_timeout})
        return ClaudeResult(success=False, timed_out=True, error=f"Dispatch bridge timed out after {http_timeout}s")

    except Exception as e:
        log("claude_runner: bridge unexpected error", {"error": str(e)})
        return ClaudeResult(success=False, error=f"Dispatch bridge error: {e}")


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------


async def run_claude(
    prompt: str,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    output_format: str = "json",
    resume_session_id: Optional[str] = None,
) -> ClaudeResult:
    """Run the ``claude`` CLI and return the parsed result.

    If ``CLAUDE_DISPATCH_URL`` is set, proxies the request to the host-side
    dispatch bridge via HTTP. Otherwise, runs the CLI as a local subprocess.

    Args:
        prompt: The prompt/task text.
        model: Model name (default: CLAUDE_DISPATCH_MODEL or "sonnet").
        timeout: Timeout in seconds (default: CLAUDE_DISPATCH_TIMEOUT or 300).
        cwd: Working directory for the subprocess.
        output_format: CLI output format (default: "json").

    Returns:
        ClaudeResult with parsed output.
    """
    model = model or _default_model()
    timeout = timeout or _default_timeout()

    # Route to HTTP bridge if configured
    dispatch_url = _dispatch_url()
    if dispatch_url:
        return await _run_claude_http(dispatch_url, prompt, model, timeout, output_format, resume_session_id)

    # Local subprocess mode
    binary = _claude_binary()
    if resume_session_id:
        cmd = [
            binary,
            "--dangerously-skip-permissions",
            "--model",
            model,
            "--output-format",
            output_format,
            "--resume",
            resume_session_id,
            "--print",
            prompt,
        ]
    else:
        cmd = [
            binary,
            "--dangerously-skip-permissions",
            "--model",
            model,
            "--output-format",
            output_format,
            "-p",
            prompt,
        ]

    log("claude_runner: starting", {"model": model, "prompt_len": len(prompt)})

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

        if proc.returncode != 0:
            log("claude_runner: non-zero exit", {"exit_code": proc.returncode, "stderr": stderr_text[:500]})
            return ClaudeResult(
                success=False,
                exit_code=proc.returncode,
                error=stderr_text[:2000] or f"Exit code {proc.returncode}",
            )

        parsed = parse_claude_output(stdout_text)

        return ClaudeResult(
            success=not parsed.get("is_error", False),
            result=parsed.get("result", stdout_text),
            session_id=parsed.get("session_id"),
            duration_ms=parsed.get("duration_ms"),
            exit_code=proc.returncode,
            error=parsed.get("result") if parsed.get("is_error") else None,
        )

    except asyncio.TimeoutError:
        log("claude_runner: timeout", {"timeout": timeout})
        return ClaudeResult(success=False, timed_out=True, error=f"Timed out after {timeout}s")

    except FileNotFoundError:
        msg = f"Claude CLI binary not found: {binary}"
        log("claude_runner: binary not found", {"binary": binary})
        return ClaudeResult(success=False, error=msg)

    except Exception as e:
        log("claude_runner: unexpected error", {"error": str(e)})
        return ClaudeResult(success=False, error=str(e))


# ---------------------------------------------------------------------------
# Sync wrapper
# ---------------------------------------------------------------------------


def run_claude_sync(prompt: str, **kwargs) -> ClaudeResult:
    """Synchronous wrapper around :func:`run_claude`.

    If called from within a running event loop (e.g. MCP server context),
    runs the coroutine in a separate thread to avoid the
    "Cannot run the event loop while another loop is running" error.
    Prefer calling :func:`run_claude` directly with ``await`` when possible.
    """
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        # Already inside an event loop — run in a thread with its own loop
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, run_claude(prompt, **kwargs))
            return future.result()
    except RuntimeError:
        # No running loop — safe to create one
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(run_claude(prompt, **kwargs))
        finally:
            loop.close()
