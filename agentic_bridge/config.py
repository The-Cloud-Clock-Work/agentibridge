"""Configuration for agentic-bridge."""

import os
from pathlib import Path
from typing import Optional


def _env_bool(key: str, default: str = "false") -> bool:
    """Parse env var as boolean. Accepts: true/false, 1/0, yes/no."""
    val = os.getenv(key, default).lower()
    return val in ("true", "1", "yes")


def _env_int(key: str, default: str, *, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Parse env var as int with optional bounds validation."""
    val = int(os.getenv(key, default))
    if min_val is not None and val < min_val:
        val = min_val
    if max_val is not None and val > max_val:
        val = max_val
    return val


# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

LOG_ENABLED = _env_bool("CLAUDE_HOOK_LOG_ENABLED", "true")


def _default_log_file() -> str:
    if Path("/.dockerenv").exists():
        return "/app/logs/agentic-bridge.log"
    return str(Path.home() / ".cache" / "agentic-bridge" / "agentic-bridge.log")


LOG_FILE = os.getenv("AGENTIC_BRIDGE_LOG_FILE", _default_log_file())

# =============================================================================
# SESSION BRIDGE CONFIGURATION
# =============================================================================

# Enable/disable session bridge collector background polling
SESSION_BRIDGE_ENABLED = _env_bool("SESSION_BRIDGE_ENABLED", "true")

# Polling interval in seconds (minimum 5s)
SESSION_BRIDGE_POLL_INTERVAL = _env_int("SESSION_BRIDGE_POLL_INTERVAL", "60", min_val=5)

# Base directory for Claude transcript files
SESSION_BRIDGE_PROJECTS_DIR = os.getenv(
    "SESSION_BRIDGE_PROJECTS_DIR",
    str(Path.home() / ".claude" / "projects"),
)

# Maximum entries to store per session in Redis (0 = unlimited)
SESSION_BRIDGE_MAX_ENTRIES = _env_int("SESSION_BRIDGE_MAX_ENTRIES", "500", min_val=0)

# =============================================================================
# SESSION BRIDGE — SEMANTIC SEARCH (Phase 2)
# =============================================================================

# Embedding backend for semantic search (bedrock or ollama)
SESSION_BRIDGE_EMBEDDING_ENABLED = _env_bool("SESSION_BRIDGE_EMBEDDING_ENABLED", "false")

# =============================================================================
# SESSION BRIDGE — REMOTE ACCESS (Phase 3)
# =============================================================================

# Transport mode: "stdio" (local MCP, default) or "sse" (HTTP remote)
SESSION_BRIDGE_TRANSPORT = os.getenv("SESSION_BRIDGE_TRANSPORT", "stdio")

# Port for SSE/HTTP transport (1-65535)
SESSION_BRIDGE_PORT = _env_int("SESSION_BRIDGE_PORT", "8100", min_val=1, max_val=65535)

# Host for SSE/HTTP transport
SESSION_BRIDGE_HOST = os.getenv("SESSION_BRIDGE_HOST", "127.0.0.1")

# Comma-separated API keys for remote access auth
SESSION_BRIDGE_API_KEYS = os.getenv("SESSION_BRIDGE_API_KEYS", "")
