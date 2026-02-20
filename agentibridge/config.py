"""Configuration for agentibridge."""

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
        return "/app/logs/agentibridge.log"
    return str(Path.home() / ".cache" / "agentibridge" / "agentibridge.log")


LOG_FILE = os.getenv("AGENTIBRIDGE_LOG_FILE", _default_log_file())

# =============================================================================
# AGENTIBRIDGE CONFIGURATION
# =============================================================================

# Enable/disable agentibridge collector background polling
AGENTIBRIDGE_ENABLED = _env_bool("AGENTIBRIDGE_ENABLED", "true")

# Polling interval in seconds (minimum 5s)
AGENTIBRIDGE_POLL_INTERVAL = _env_int("AGENTIBRIDGE_POLL_INTERVAL", "60", min_val=5)

# Base directory for Claude transcript files
AGENTIBRIDGE_PROJECTS_DIR = os.getenv(
    "AGENTIBRIDGE_PROJECTS_DIR",
    str(Path.home() / ".claude" / "projects"),
)

# Maximum entries to store per session in Redis (0 = unlimited)
AGENTIBRIDGE_MAX_ENTRIES = _env_int("AGENTIBRIDGE_MAX_ENTRIES", "500", min_val=0)

# =============================================================================
# AGENTIBRIDGE — SEMANTIC SEARCH (Phase 2)
# =============================================================================

# Embedding enabled flag for semantic search
AGENTIBRIDGE_EMBEDDING_ENABLED = _env_bool("AGENTIBRIDGE_EMBEDDING_ENABLED", "false")

# =============================================================================
# AGENTIBRIDGE — REMOTE ACCESS (Phase 3)
# =============================================================================

# Transport mode: "stdio" (local MCP, default) or "sse" (HTTP remote)
AGENTIBRIDGE_TRANSPORT = os.getenv("AGENTIBRIDGE_TRANSPORT", "stdio")

# Port for SSE/HTTP transport (1-65535)
AGENTIBRIDGE_PORT = _env_int("AGENTIBRIDGE_PORT", "8100", min_val=1, max_val=65535)

# Host for SSE/HTTP transport
AGENTIBRIDGE_HOST = os.getenv("AGENTIBRIDGE_HOST", "127.0.0.1")

# Comma-separated API keys for remote access auth
AGENTIBRIDGE_API_KEYS = os.getenv("AGENTIBRIDGE_API_KEYS", "")

# =============================================================================
# AGENTIBRIDGE — OAUTH 2.1 (opt-in)
# =============================================================================

# OAuth issuer URL — enables OAuth 2.1 when set (e.g., https://homebridge.example.com)
OAUTH_ISSUER_URL = os.getenv("OAUTH_ISSUER_URL", "")

# OAuth resource server URL — defaults to {OAUTH_ISSUER_URL}/mcp
OAUTH_RESOURCE_URL = os.getenv("OAUTH_RESOURCE_URL", "")

# Pre-configured OAuth client credentials — disables dynamic registration when set
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")

# Comma-separated allowed OAuth redirect URIs (required for pre-configured clients)
OAUTH_ALLOWED_REDIRECT_URIS = os.getenv("OAUTH_ALLOWED_REDIRECT_URIS", "")

# Space-separated OAuth scopes the client is allowed to request (e.g. "claudeai")
OAUTH_ALLOWED_SCOPES = os.getenv("OAUTH_ALLOWED_SCOPES", "")
