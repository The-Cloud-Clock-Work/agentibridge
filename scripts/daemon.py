#!/usr/bin/env python3
"""Simple daemon skeleton for agentibridge.

Usage:
    python scripts/daemon.py start
    python scripts/daemon.py stop
    python scripts/daemon.py status
"""

import os
import signal
import sys
import time
from pathlib import Path

STATE_DIR = Path.home() / ".agentibridge"
PID_FILE = STATE_DIR / "daemon.pid"
LOG_FILE = STATE_DIR / "daemon.log"
POLL_INTERVAL = 60  # seconds


def _log(msg: str):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(line)


def _read_pid() -> int | None:
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            os.kill(pid, 0)  # check if alive
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            PID_FILE.unlink(missing_ok=True)
    return None


def _poll_cycle():
    """Override this with your daemon logic."""
    pass


def start():
    if _read_pid():
        print(f"Daemon already running (PID {PID_FILE.read_text().strip()})")
        return

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Fork and detach
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, __file__, "_run"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"Daemon started (PID {proc.pid})")


def _run():
    """Main daemon loop — called after fork."""
    PID_FILE.write_text(str(os.getpid()))
    _log("daemon started")

    def _shutdown(signum, frame):
        _log("daemon stopping")
        PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while True:
            _poll_cycle()
            time.sleep(POLL_INTERVAL)
    except Exception as e:
        _log(f"daemon error: {e}")
    finally:
        PID_FILE.unlink(missing_ok=True)


def stop():
    pid = _read_pid()
    if not pid:
        print("Daemon not running")
        return
    os.kill(pid, signal.SIGTERM)
    print(f"Stopped daemon (PID {pid})")


def status():
    pid = _read_pid()
    if pid:
        print(f"Running (PID {pid})")
    else:
        print("Not running")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "_run":
        _run()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
