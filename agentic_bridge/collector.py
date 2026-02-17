"""Background transcript collector.

Scans ~/.claude/projects/ for JSONL transcript files and incrementally
indexes new entries into the SessionStore. Runs as a daemon thread
inside the MCP server process.
"""

import os
import sys
import threading
from pathlib import Path
from time import time, sleep

from agentic_bridge.logging import log
from agentic_bridge.parser import (
    parse_transcript_entries,
    parse_transcript_meta,
    scan_projects_dir,
)
from agentic_bridge.store import SessionStore


class SessionCollector:
    """Incremental transcript collector with background polling."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store
        self._interval = int(os.getenv("SESSION_BRIDGE_POLL_INTERVAL", "60"))
        self._projects_dir = Path(os.getenv(
            "SESSION_BRIDGE_PROJECTS_DIR",
            str(Path.home() / ".claude" / "projects"),
        ))
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="session-collector",
            daemon=True,
        )
        self._thread.start()
        print(f"Collector started (interval={self._interval}s)", file=sys.stderr)

    def stop(self) -> None:
        """Signal the background thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def collect_once(self) -> dict:
        """Run a single collection cycle. Returns stats."""
        start = time()
        files_scanned = 0
        sessions_updated = 0
        entries_added = 0

        try:
            all_files = scan_projects_dir(self._projects_dir)
            files_scanned = len(all_files)

            for session_id, project_encoded, filepath in all_files:
                try:
                    result = self._scan_file(session_id, project_encoded, filepath)
                    if result["updated"]:
                        sessions_updated += 1
                        entries_added += result["entries_added"]
                except Exception as e:
                    log("Collector: file scan error", {
                        "file": str(filepath),
                        "error": str(e),
                    })
                    continue

        except Exception as e:
            log("Collector: scan error", {"error": str(e)})

        duration_ms = int((time() - start) * 1000)

        return {
            "files_scanned": files_scanned,
            "sessions_updated": sessions_updated,
            "entries_added": entries_added,
            "duration_ms": duration_ms,
        }

    def _poll_loop(self) -> None:
        """Background thread: collect on interval."""
        # Initial collection
        try:
            stats = self.collect_once()
            print(
                f"Initial collection: {stats['files_scanned']} files, "
                f"{stats['sessions_updated']} updated, "
                f"{stats['entries_added']} entries, "
                f"{stats['duration_ms']}ms",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"Initial collection failed: {e}", file=sys.stderr)

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            try:
                self.collect_once()
            except Exception as e:
                log("Collector: poll error", {"error": str(e)})

    def _scan_file(self, session_id: str, project_encoded: str, filepath: Path) -> dict:
        """Incrementally index one transcript file."""
        file_size = filepath.stat().st_size
        stored_offset = self._store.get_file_position(str(filepath))

        if file_size <= stored_offset:
            return {"updated": False, "entries_added": 0}

        # Parse new entries from offset
        entries, new_offset = parse_transcript_entries(filepath, offset=stored_offset)

        if not entries and new_offset <= stored_offset:
            return {"updated": False, "entries_added": 0}

        # Update session metadata
        meta = parse_transcript_meta(filepath, project_encoded, entries if stored_offset == 0 else None)
        if meta:
            self._store.upsert_session(meta)

        # Store new entries
        if entries:
            self._store.add_entries(session_id, entries)

        # Save position
        self._store.save_file_position(str(filepath), new_offset)

        return {"updated": True, "entries_added": len(entries)}
