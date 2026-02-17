"""Configuration for agentic-bridge."""

import os
from pathlib import Path


def _env_bool(key: str, default: str = "false") -> bool:
    """Parse env var as boolean. Accepts: true/false, 1/0, yes/no."""
    val = os.getenv(key, default).lower()
    return val in ("true", "1", "yes")


# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================

LOG_ENABLED = _env_bool("CLAUDE_HOOK_LOG_ENABLED", "true")
LOG_FILE = os.getenv("AGENTIC_BRIDGE_LOG_FILE", "/app/logs/agentic-bridge.log")

# =============================================================================
# SESSION BRIDGE CONFIGURATION
# =============================================================================

# Enable/disable session bridge collector background polling
SESSION_BRIDGE_ENABLED = _env_bool("SESSION_BRIDGE_ENABLED", "true")

# Polling interval in seconds
SESSION_BRIDGE_POLL_INTERVAL = int(os.getenv("SESSION_BRIDGE_POLL_INTERVAL", "60"))

# Base directory for Claude transcript files
SESSION_BRIDGE_PROJECTS_DIR = os.getenv(
    "SESSION_BRIDGE_PROJECTS_DIR",
    str(Path.home() / ".claude" / "projects"),
)

# Maximum entries to store per session in Redis (0 = unlimited)
SESSION_BRIDGE_MAX_ENTRIES = int(os.getenv("SESSION_BRIDGE_MAX_ENTRIES", "500"))

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

# Port for SSE/HTTP transport
SESSION_BRIDGE_PORT = int(os.getenv("SESSION_BRIDGE_PORT", "8100"))

# Comma-separated API keys for remote access auth
SESSION_BRIDGE_API_KEYS = os.getenv("SESSION_BRIDGE_API_KEYS", "")
