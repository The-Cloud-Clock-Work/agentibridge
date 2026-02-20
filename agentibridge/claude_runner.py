"""Run Claude CLI directly via subprocess.

Replaces the old completions.py module that called an external agenticore
/completions API.  Now AgentiBridge is fully standalone — it shells out to
the ``claude`` CLI binary which must be on PATH (or set via CLAUDE_BINARY).

Usage:
    from agentibridge.claude_runner import run_claude_sync

    result = run_claude_sync("Summarize this code")
    if result["success"]:
        print(result["result"])

Env vars:
    CLAUDE_BINARY          — path to claude CLI (default: "claude")
    CLAUDE_DISPATCH_MODEL  — model for dispatch (default: "sonnet")
    CLAUDE_DISPATCH_TIMEOUT — timeout in seconds (default: 300)
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
# Async runner
# ---------------------------------------------------------------------------

async def run_claude(
    prompt: str,
    model: Optional[str] = None,
    timeout: Optional[int] = None,
    cwd: Optional[str] = None,
    output_format: str = "json",
) -> ClaudeResult:
    """Run the ``claude`` CLI and return the parsed result.

    Args:
        prompt: The prompt/task text.
        model: Model name (default: CLAUDE_DISPATCH_MODEL or "sonnet").
        timeout: Timeout in seconds (default: CLAUDE_DISPATCH_TIMEOUT or 300).
        cwd: Working directory for the subprocess.
        output_format: CLI output format (default: "json").

    Returns:
        ClaudeResult with parsed output.
    """
    binary = _claude_binary()
    model = model or _default_model()
    timeout = timeout or _default_timeout()

    cmd = [binary, "--model", model, "--output-format", output_format, "-p", prompt]

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

    Creates a new event loop so it can be called from non-async MCP tool
    functions without worrying about an already-running loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(run_claude(prompt, **kwargs))
    finally:
        loop.close()
