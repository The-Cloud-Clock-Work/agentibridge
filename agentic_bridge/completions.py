"""Completions API client for calling the remote agent.

Call the /completions endpoint to invoke Claude agents remotely.

Usage:
    from agentic_bridge.completions import CompletionsClient, call_completions

    # Simple call
    result = call_completions("Create a diagram for S3 pipeline")

    # With options
    result = call_completions(
        prompt="Create diagram",
        command="thinkhard",  # default/thinkhard/ultrathink
        wait=True,
        meta={"user": "john", "platform": "slack"},
    )

    # Stateless mode (fresh session, no history)
    result = call_completions(
        prompt="Analyze this code",
        stateless=True,  # No conversation history
    )

    # With template variables for prompt rendering
    result = call_completions(
        prompt="Generate report",
        template_vars={"USER_NAME": "John", "CONTEXT": "Q4 metrics"},
    )

    # Check result
    if result.success:
        print(f"Done in {result.duration_ms}ms")
        print(result.parsed_output)
    else:
        print(f"Error: {result.error}")
"""

import os
import uuid as uuid_lib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from agentic_bridge.logging import log


# =============================================================================
# CONFIGURATION (override via env vars)
# =============================================================================

# AGENT_API_ENDPOINT - Base URL for completions API (default: http://localhost:8000)
# AGENT_API_KEY - API key for authentication
# AGENT_API_TIMEOUT - Request timeout in seconds (default: 300)

DEFAULT_BASE_URL = os.environ.get("AGENT_API_ENDPOINT", "http://localhost:8000")
DEFAULT_API_KEY = os.environ.get("AGENT_API_KEY", "")
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("AGENT_API_TIMEOUT", "300"))


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class CompletionResult:
    """Result from a /completions API call."""

    success: bool
    exit_code: Optional[int] = None
    duration_ms: Optional[int] = None
    timed_out: bool = False
    parsed_output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    is_async: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# =============================================================================
# COMPLETIONS CLIENT
# =============================================================================


class CompletionsClient:
    """Client for calling the /completions endpoint."""

    _instance: Optional["CompletionsClient"] = None

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        """Initialize completions client.

        Args:
            base_url: Base URL for the API (default: AGENT_API_ENDPOINT env or localhost:8000)
            api_key: API key for authentication (default: AGENT_API_KEY env)
            timeout: Request timeout in seconds (default: AGENT_API_TIMEOUT env or 300s)
        """
        self.base_url = base_url or DEFAULT_BASE_URL
        self.api_key = api_key or DEFAULT_API_KEY
        self.timeout = timeout or DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def get_client(cls, **kwargs) -> "CompletionsClient":
        """Get singleton client instance.

        Args:
            **kwargs: Arguments to pass to __init__ (only used on first call)

        Returns:
            CompletionsClient singleton instance
        """
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton instance (useful for testing)."""
        cls._instance = None

    def call(
        self,
        prompt: str,
        command: str = "default",
        wait: bool = True,
        context: Optional[Dict[str, Any]] = None,
        uuid: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
        stateless: bool = False,
        template_vars: Optional[Dict[str, Any]] = None,
    ) -> CompletionResult:
        """Call the /completions endpoint.

        Args:
            prompt: The prompt/task for the agent
            command: Command preset - controls model selection:
                     - "default": Fast (haiku)
                     - "thinkhard": Balanced (sonnet)
                     - "ultrathink": Best quality (opus)
            wait: If True, waits for completion. If False, returns immediately (fire-and-forget)
            context: Optional context data passed to agent
            uuid: Session UUID for state tracking (auto-generated if not provided)
            meta: Platform-specific metadata (conversation_id, platform, user, etc.)
            stateless: If True, generates fresh Claude session (no history accumulation)
            template_vars: Variables for prompt template rendering (e.g., {'USER_NAME': 'John'})

        Returns:
            CompletionResult with success status and response data
        """
        # Generate UUID if not provided
        if uuid is None:
            uuid = str(uuid_lib.uuid4())

        # Generate meta if not provided
        if meta is None:
            meta = {
                "conversation_id": uuid,
                "platform": "agentic-bridge",
                "user": os.environ.get("USER", "system"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key,
        }

        payload = {
            "command": command,
            "parameters": [prompt],
            "context": context,
            "uuid": uuid,
            "wait": wait,
            "meta": meta,
            "stateless": stateless,
            "template_vars": template_vars,
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/completions",
                    json=payload,
                    headers=headers,
                )

            # Fire-and-forget mode returns 202 Accepted
            if response.status_code == 202:
                return CompletionResult(
                    success=True,
                    is_async=True,
                )

            response.raise_for_status()
            data = response.json()

            return CompletionResult(
                success=True,
                exit_code=data.get("exit_code"),
                duration_ms=data.get("duration_ms"),
                timed_out=data.get("timed_out", False),
                parsed_output=data.get("parsed_output"),
            )

        except httpx.HTTPStatusError as e:
            error_msg = f"HTTP {e.response.status_code}: {str(e)}"
            log("CompletionsClient HTTP error", {
                "status": e.response.status_code,
                "error": str(e),
            })
            return CompletionResult(success=False, error=error_msg)

        except httpx.TimeoutException as e:
            log("CompletionsClient timeout", {"error": str(e)})
            return CompletionResult(
                success=False,
                timed_out=True,
                error=f"Request timed out after {self.timeout}s",
            )

        except Exception as e:
            log("CompletionsClient error", {"error": str(e)})
            return CompletionResult(success=False, error=str(e))


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def call_completions(
    prompt: str,
    command: str = "default",
    wait: bool = True,
    context: Optional[Dict[str, Any]] = None,
    uuid: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    stateless: bool = False,
    template_vars: Optional[Dict[str, Any]] = None,
) -> CompletionResult:
    """Call /completions endpoint using singleton client.

    Args:
        prompt: The prompt/task for the agent
        command: Command preset (default/thinkhard/ultrathink)
        wait: If True, waits for completion
        context: Optional context data passed to agent
        uuid: Session UUID for state tracking (auto-generated if not provided)
        meta: Platform-specific metadata (conversation_id, platform, user, etc.)
        stateless: If True, generates fresh Claude session (no history accumulation)
        template_vars: Variables for prompt template rendering (e.g., {'USER_NAME': 'John'})

    Returns:
        CompletionResult with success status and response data
    """
    return CompletionsClient.get_client().call(
        prompt=prompt,
        command=command,
        wait=wait,
        context=context,
        uuid=uuid,
        meta=meta,
        stateless=stateless,
        template_vars=template_vars,
    )
