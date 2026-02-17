"""Simplified logging utility for agentic-bridge.

Writes structured JSON log entries to a file.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _env_bool(key: str, default: str = "false") -> bool:
    """Parse env var as boolean. Accepts: true/false, 1/0, yes/no."""
    val = os.getenv(key, default).lower()
    return val in ("true", "1", "yes")


LOG_ENABLED = _env_bool("CLAUDE_HOOK_LOG_ENABLED", "true")
LOG_FILE = os.getenv("AGENTIC_BRIDGE_LOG_FILE", "/app/logs/agentic-bridge.log")


def log(message: str, payload: dict | None = None) -> None:
    """Write log entry to file (JSON format)."""
    if not LOG_ENABLED:
        return

    try:
        log_path = Path(LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "@timestamp": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        if payload:
            entry["payload"] = payload

        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Silent failure - never break the server
