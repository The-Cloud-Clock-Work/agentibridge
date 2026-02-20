"""CLI helper tool for agentibridge.

Provides commands for status, config, connection strings, and service management.

Usage:
    agentibridge status     — Check if running, Redis connectivity, session count
    agentibridge help       — Available MCP tools and configuration reference
    agentibridge connect    — Connection strings for Claude Code, ChatGPT, etc.
    agentibridge config     — Current config dump / generate .env template
    agentibridge tunnel     — Cloudflare Tunnel status and URL
    agentibridge locks      — Show Redis keys, file locks, and bridge resource state
    agentibridge install    — Install as systemd user service
    agentibridge uninstall  — Remove systemd service
    agentibridge version    — Print version
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def _version() -> str:
    from agentibridge import __version__

    return __version__


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_version(args: argparse.Namespace) -> None:
    print(f"agentibridge {_version()}")


def cmd_status(args: argparse.Namespace) -> None:
    print(f"AgentiBridge v{_version()}")
    print("=" * 50)

    # Check systemd service
    print("\n[Service]")
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "agentibridge"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = result.stdout.strip()
        print(f"  systemd: {status}")
    except Exception:
        print("  systemd: not checked (systemctl unavailable)")

    # Check Docker
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", "agentibridge"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            print(f"  docker:  {result.stdout.strip()}")
        else:
            print("  docker:  not running")
    except Exception:
        print("  docker:  not checked (docker unavailable)")

    # Check Redis
    print("\n[Redis]")
    try:
        from agentibridge.redis_client import get_redis

        r = get_redis()
        if r is not None:
            r.ping()
            print("  status: connected")
            # Count sessions
            from agentibridge.store import _rkey

            count = r.zcard(_rkey("idx:all"))
            print(f"  sessions indexed: {count}")
        else:
            url = os.getenv("REDIS_URL", "(not set)")
            print(f"  status: unavailable (REDIS_URL={url})")
    except Exception as e:
        print(f"  status: error ({e})")

    # Check Cloudflare Tunnel
    print("\n[Tunnel]")
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", "agentibridge-tunnel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            tunnel_status = result.stdout.strip()
            print(f"  cloudflared: {tunnel_status}")
            if tunnel_status == "running":
                # Try to extract quick tunnel URL from logs
                log_result = subprocess.run(
                    ["docker", "logs", "--tail", "50", "agentibridge-tunnel"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                log_output = log_result.stdout + log_result.stderr
                url = _extract_tunnel_url(log_output)
                if url:
                    print(f"  url: {url}")
                elif "Starting named tunnel" in log_output:
                    print("  mode: named tunnel")
                else:
                    print("  url: (detecting...)")
        else:
            print("  cloudflared: not running")
    except FileNotFoundError:
        print("  cloudflared: not checked (docker unavailable)")
    except Exception:
        print("  cloudflared: not checked")

    # Check projects directory
    print("\n[Transcripts]")
    projects_dir = Path(
        os.getenv(
            "AGENTIBRIDGE_PROJECTS_DIR",
            str(Path.home() / ".claude" / "projects"),
        )
    )
    if projects_dir.exists():
        jsonl_count = sum(1 for _ in projects_dir.rglob("*.jsonl"))
        print(f"  directory: {projects_dir}")
        print(f"  JSONL files: {jsonl_count}")
    else:
        print(f"  directory: {projects_dir} (not found)")

    print("\n[Config]")
    print(f"  transport: {os.getenv('AGENTIBRIDGE_TRANSPORT', 'stdio')}")
    print(f"  port: {os.getenv('AGENTIBRIDGE_PORT', '8100')}")
    print(f"  poll interval: {os.getenv('AGENTIBRIDGE_POLL_INTERVAL', '60')}s")


def cmd_help(args: argparse.Namespace) -> None:
    print(f"AgentiBridge v{_version()} — Claude CLI Transcript MCP Server")
    print("=" * 60)
    print()
    print("MCP TOOLS (10 total)")
    print("-" * 60)
    print()
    print("Phase 1 — Foundation:")
    print("  list_sessions        List sessions across all projects")
    print("  get_session          Get full session metadata + transcript")
    print("  get_session_segment  Paginated/time-range transcript retrieval")
    print("  get_session_actions  Extract tool calls with counts")
    print("  search_sessions      Keyword search across transcripts")
    print("  collect_now          Trigger immediate collection")
    print()
    print("Phase 2 — Semantic Search:")
    print("  search_semantic      Semantic search using embeddings")
    print("  generate_summary     AI-generated session summary")
    print()
    print("Phase 4 — Write-back & Dispatch:")
    print("  restore_session      Load session context for continuation")
    print("  dispatch_task        Dispatch task with session context")
    print()
    print("CONFIGURATION")
    print("-" * 60)
    print()
    print("  REDIS_URL                       Redis connection URL")
    print("  AGENTIBRIDGE_TRANSPORT          stdio or sse (default: stdio)")
    print("  AGENTIBRIDGE_HOST               Bind address (default: 127.0.0.1)")
    print("  AGENTIBRIDGE_PORT               HTTP port (default: 8100)")
    print("  AGENTIBRIDGE_API_KEYS           Comma-separated API keys")
    print("  AGENTIBRIDGE_POLL_INTERVAL      Poll interval in seconds (default: 60)")
    print("  AGENTIBRIDGE_MAX_ENTRIES        Max entries per session (default: 500)")
    print("  AGENTIBRIDGE_PROJECTS_DIR       Claude projects directory")
    print("  LLM_API_BASE                    OpenAI-compatible API base URL")
    print("  LLM_API_KEY                     API key for LLM endpoint")
    print("  LLM_EMBED_MODEL                Embedding model name")
    print("  LLM_CHAT_MODEL                 Chat model for summaries (fallback)")
    print("  ANTHROPIC_API_KEY               Anthropic key for summaries (preferred)")
    print("  CLAUDE_BINARY                   Path to Claude CLI (default: claude)")
    print("  CLAUDE_DISPATCH_MODEL           Dispatch model (default: sonnet)")
    print("  CLAUDE_DISPATCH_TIMEOUT         Dispatch timeout in seconds (default: 300)")
    print("  CLOUDFLARE_TUNNEL_TOKEN         Token for named Cloudflare Tunnel (optional)")
    print()
    print("USAGE")
    print("-" * 60)
    print()
    print("  Local (stdio):   python -m agentibridge")
    print("  Remote (SSE):    AGENTIBRIDGE_TRANSPORT=sse python -m agentibridge")
    print("  Docker:          docker compose up --build -d")
    print("  Tunnel:          docker compose --profile tunnel up -d")
    print(
        "  All-in-one:      docker run -d -p 8100:8100 -v ~/.claude/projects:/home/appuser/.claude/projects:ro agentibridge:allinone"
    )
    print()
    print("Run 'agentibridge connect' for client connection strings.")


def cmd_connect(args: argparse.Namespace) -> None:
    host = args.host or os.getenv("AGENTIBRIDGE_HOST", "localhost")
    port = args.port or os.getenv("AGENTIBRIDGE_PORT", "8100")
    api_key = args.api_key or "your-api-key"

    print(f"Connection strings for {host}:{port}")
    print("=" * 60)

    print()
    print("=== Claude Code CLI ===")
    print("Add to ~/.mcp.json:")
    config = {
        "mcpServers": {
            "agentibridge": {
                "url": f"http://{host}:{port}/sse",
                "headers": {"X-API-Key": api_key},
            }
        }
    }
    print(json.dumps(config, indent=2))

    print()
    print("=== ChatGPT Custom GPT / Actions ===")
    print(f"  Actions URL: http://{host}:{port}/sse")
    print("  Auth: API Key in X-API-Key header")
    print(f"  Key: {api_key}")

    print()
    print("=== Claude Web (MCP) ===")
    print(f"  URL: http://{host}:{port}/sse")
    print(f"  Header: X-API-Key: {api_key}")

    print()
    print("=== Generic API ===")
    print(f"  SSE endpoint:  http://{host}:{port}/sse")
    print(f"  Health check:  http://{host}:{port}/health")
    print(f"  Auth header:   X-API-Key: {api_key}")

    print()
    print("=== Cloudflare Tunnel ===")
    print("  Start a quick tunnel (no account needed):")
    print("    docker compose --profile tunnel up -d")
    print("  Then run 'agentibridge tunnel' to get the public URL.")

    print()
    print("=== curl test ===")
    print(f"  curl -s http://{host}:{port}/health")


def _extract_tunnel_url(log_output: str) -> str | None:
    """Extract *.trycloudflare.com URL from cloudflared log output."""
    match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", log_output)
    return match.group(0) if match else None


def cmd_tunnel(args: argparse.Namespace) -> None:
    # Check docker availability
    if not shutil.which("docker"):
        print("Docker is not installed or not in PATH.")
        print("Install Docker to use Cloudflare Tunnel integration.")
        return

    # Check if tunnel container is running
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", "agentibridge-tunnel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        print("Could not inspect tunnel container.")
        return

    if result.returncode != 0:
        print("Cloudflare Tunnel is not running.")
        print()
        print("Start a quick tunnel (no Cloudflare account needed):")
        print("  docker compose --profile tunnel up -d")
        print()
        print("Start a named tunnel (persistent hostname):")
        print("  CLOUDFLARE_TUNNEL_TOKEN=xxx docker compose --profile tunnel up -d")
        return

    status = result.stdout.strip()
    print(f"Cloudflare Tunnel: {status}")

    if status != "running":
        print("Container exists but is not running. Check logs:")
        print("  docker logs agentibridge-tunnel")
        return

    # Read logs to detect mode and URL
    try:
        log_result = subprocess.run(
            ["docker", "logs", "--tail", "50", "agentibridge-tunnel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        print("Could not read tunnel container logs.")
        return

    log_output = log_result.stdout + log_result.stderr

    # Quick tunnel — extract URL
    url = _extract_tunnel_url(log_output)
    if url:
        print("Mode: quick tunnel")
        print(f"URL:  {url}")
        print()
        print("Add to ~/.mcp.json:")
        config = {
            "mcpServers": {
                "agentibridge": {
                    "url": f"{url}/sse",
                }
            }
        }
        api_keys = os.getenv("AGENTIBRIDGE_API_KEYS", "")
        if api_keys:
            first_key = api_keys.split(",")[0].strip()
            config["mcpServers"]["agentibridge"]["headers"] = {"X-API-Key": first_key}
        print(json.dumps(config, indent=2))
        print()
        print("Test:")
        print(f"  curl -s {url}/health")
        return

    # Named tunnel
    if "Starting named tunnel" in log_output:
        print("Mode: named tunnel")
        print("The tunnel is connected via your Cloudflare configuration.")
        print("Check your Cloudflare Zero Trust dashboard for the hostname.")
        print()
        print("Logs:")
        print("  docker logs agentibridge-tunnel")
        return

    # Unknown state
    print("Tunnel is running but could not determine mode.")
    print("Check logs: docker logs agentibridge-tunnel")


def cmd_config(args: argparse.Namespace) -> None:
    if args.generate_env:
        _generate_env_template()
        return

    print("Current Configuration")
    print("=" * 50)

    env_vars = [
        ("REDIS_URL", ""),
        ("REDIS_KEY_PREFIX", "agentibridge"),
        ("AGENTIBRIDGE_TRANSPORT", "stdio"),
        ("AGENTIBRIDGE_HOST", "127.0.0.1"),
        ("AGENTIBRIDGE_PORT", "8100"),
        ("AGENTIBRIDGE_API_KEYS", ""),
        ("AGENTIBRIDGE_POLL_INTERVAL", "60"),
        ("AGENTIBRIDGE_MAX_ENTRIES", "500"),
        ("AGENTIBRIDGE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")),
        ("AGENTIBRIDGE_ENABLED", "true"),
        ("LLM_API_BASE", ""),
        ("LLM_API_KEY", ""),
        ("LLM_EMBED_MODEL", ""),
        ("LLM_CHAT_MODEL", ""),
        ("ANTHROPIC_API_KEY", ""),
        ("CLAUDE_BINARY", "claude"),
        ("CLAUDE_DISPATCH_MODEL", "sonnet"),
        ("CLAUDE_DISPATCH_TIMEOUT", "300"),
        ("CLAUDE_HOOK_LOG_ENABLED", "true"),
        ("AGENTIBRIDGE_LOG_FILE", ""),
    ]

    for key, default in env_vars:
        val = os.getenv(key, "")
        source = "env" if val else "default"
        display = val if val else default if default else "(not set)"
        print(f"  {key}={display}  [{source}]")


def _generate_env_template() -> None:
    template = """# AgentiBridge Configuration
# Copy to ~/.config/agentibridge/env or .env

# Redis (optional — falls back to filesystem)
# REDIS_URL=redis://localhost:6379/0
# REDIS_KEY_PREFIX=agentibridge

# Transport: stdio (local MCP) or sse (HTTP remote)
AGENTIBRIDGE_TRANSPORT=stdio
AGENTIBRIDGE_HOST=127.0.0.1
AGENTIBRIDGE_PORT=8100

# API key auth for SSE transport (comma-separated, empty = no auth)
# AGENTIBRIDGE_API_KEYS=key1,key2

# Collector
AGENTIBRIDGE_POLL_INTERVAL=60
AGENTIBRIDGE_MAX_ENTRIES=500
# AGENTIBRIDGE_PROJECTS_DIR=~/.claude/projects

# Semantic search + LLM (OpenAI-compatible API)
# LLM_API_BASE=http://localhost:11434/v1
# LLM_API_KEY=
# LLM_EMBED_MODEL=text-embedding-3-small
# LLM_CHAT_MODEL=gpt-4o-mini

# Summary generation (Anthropic SDK preferred, falls back to LLM_CHAT_MODEL)
# ANTHROPIC_API_KEY=

# Dispatch (Claude CLI)
# CLAUDE_BINARY=claude
# CLAUDE_DISPATCH_MODEL=sonnet
# CLAUDE_DISPATCH_TIMEOUT=300

# Logging
CLAUDE_HOOK_LOG_ENABLED=true
# AGENTIBRIDGE_LOG_FILE=~/.cache/agentibridge/agentibridge.log

# Cloudflare Tunnel (optional — use docker compose --profile tunnel)
# CLOUDFLARE_TUNNEL_TOKEN=your-tunnel-token-here
"""
    print(template)


def cmd_install(args: argparse.Namespace) -> None:
    mode = args.mode or "docker"
    config_dir = Path.home() / ".config" / "agentibridge"
    systemd_dir = Path.home() / ".config" / "systemd" / "user"

    print(f"Installing agentibridge as systemd user service (mode: {mode})")

    # Create config directory
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / "env"
    if not env_file.exists():
        env_file.write_text(
            "# AgentiBridge environment\n"
            "AGENTIBRIDGE_TRANSPORT=sse\n"
            "AGENTIBRIDGE_HOST=0.0.0.0\n"
            "AGENTIBRIDGE_PORT=8100\n"
            "# AGENTIBRIDGE_API_KEYS=\n"
            "# REDIS_URL=redis://localhost:6379/0\n"
        )
        print(f"  Created {env_file}")

    # Determine service file
    pkg_dir = Path(__file__).parent.parent
    if mode == "docker":
        service_src = pkg_dir / "deploy" / "agentibridge.service"
    else:
        service_src = pkg_dir / "deploy" / "agentibridge-native.service"

    if not service_src.exists():
        print(f"  ERROR: Service file not found: {service_src}")
        print("  Make sure the deploy/ directory is present.")
        sys.exit(1)

    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_dest = systemd_dir / "agentibridge.service"
    shutil.copy2(service_src, service_dest)
    print(f"  Installed {service_dest}")

    # Enable and start
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "agentibridge"], check=True)
    subprocess.run(["systemctl", "--user", "start", "agentibridge"], check=True)
    print("  Service enabled and started")
    print()
    print("Check status with: agentibridge status")
    print("View logs with: journalctl --user -u agentibridge -f")


def cmd_uninstall(args: argparse.Namespace) -> None:
    print("Uninstalling agentibridge systemd service...")

    try:
        subprocess.run(["systemctl", "--user", "stop", "agentibridge"], check=False)
        subprocess.run(["systemctl", "--user", "disable", "agentibridge"], check=False)
    except Exception:
        pass

    service_file = Path.home() / ".config" / "systemd" / "user" / "agentibridge.service"
    if service_file.exists():
        service_file.unlink()
        print(f"  Removed {service_file}")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    except Exception:
        pass

    print("  Service uninstalled")
    print()
    print("Note: Config files in ~/.config/agentibridge/ were preserved.")
    print("Remove manually if no longer needed.")


def cmd_locks(args: argparse.Namespace) -> None:
    """Show Redis keys, file position locks, and bridge resource state."""
    print(f"AgentiBridge v{_version()} — Lock & Resource Inspector")
    print("=" * 60)

    # ── Redis locks / keys ────────────────────────────────────────────
    print("\n[Redis Keys]")
    try:
        from agentibridge.redis_client import get_redis

        r = get_redis()
        if r is None:
            print("  Redis: unavailable (REDIS_URL not set or connection failed)")
        else:
            r.ping()
            prefix = os.getenv("REDIS_KEY_PREFIX", "agentibridge")

            # Session index
            idx_all = f"{prefix}:sb:idx:all"
            session_count = r.zcard(idx_all)
            print(f"  Session index ({idx_all}): {session_count} sessions")

            # Project indexes
            cursor = 0
            project_indexes = []
            while True:
                cursor, keys = r.scan(cursor, match=f"{prefix}:sb:idx:project:*", count=100)
                project_indexes.extend(keys)
                if cursor == 0:
                    break
            print(f"  Project indexes: {len(project_indexes)}")
            for key in sorted(project_indexes):
                count = r.zcard(key)
                # Extract project name from key
                proj_name = key.replace(f"{prefix}:sb:idx:project:", "")
                print(f"    {proj_name}: {count} sessions")

            # Position keys (collector file offsets)
            cursor = 0
            pos_keys = []
            while True:
                cursor, keys = r.scan(cursor, match=f"{prefix}:sb:pos:*", count=100)
                pos_keys.extend(keys)
                if cursor == 0:
                    break
            print(f"  Position locks (file offsets): {len(pos_keys)}")
            for key in sorted(pos_keys):
                val = r.get(key)
                short_key = key.replace(f"{prefix}:sb:pos:", "")
                print(f"    {short_key}: offset {val}")

            # Session data keys (meta + entries)
            cursor = 0
            meta_keys = []
            entry_keys = []
            while True:
                cursor, keys = r.scan(cursor, match=f"{prefix}:sb:session:*", count=100)
                for k in keys:
                    if k.endswith(":meta"):
                        meta_keys.append(k)
                    elif k.endswith(":entries"):
                        entry_keys.append(k)
                if cursor == 0:
                    break
            print(f"  Session metadata keys: {len(meta_keys)}")
            print(f"  Session entry lists: {len(entry_keys)}")

            # Total memory usage estimate
            info = r.info("memory")
            used_mb = info.get("used_memory_human", "unknown")
            print(f"  Redis memory usage: {used_mb}")

    except Exception as e:
        print(f"  Redis error: {e}")

    # ── File-based position locks ─────────────────────────────────────
    print("\n[File Position Locks]")
    pos_dir = Path(
        os.getenv(
            "AGENTIBRIDGE_POSITIONS_DIR",
            str(Path.home() / ".cache" / "agentibridge" / "positions"),
        )
    )
    if pos_dir.exists():
        pos_files = list(pos_dir.glob("*.pos"))
        print(f"  Directory: {pos_dir}")
        print(f"  Position files: {len(pos_files)}")
        for pf in sorted(pos_files):
            try:
                offset = pf.read_text().strip()
                print(f"    {pf.name}: offset {offset}")
            except OSError:
                print(f"    {pf.name}: (unreadable)")
    else:
        print(f"  Directory: {pos_dir} (not found — no file locks)")

    # ── Bridge process locks ──────────────────────────────────────────
    print("\n[Bridge Processes]")

    # Check for running agentibridge processes
    try:
        result = subprocess.run(
            ["pgrep", "-af", "agentibridge"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"  PID {line}")
        else:
            print("  No agentibridge processes found")
    except Exception:
        print("  Process check unavailable (pgrep not found)")

    # Docker containers
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=agentibridge", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            print("\n  Docker containers:")
            for line in result.stdout.strip().splitlines():
                print(f"    {line}")
        else:
            print("  No agentibridge Docker containers running")
    except Exception:
        print("  Docker check unavailable")

    if args.clear:
        print("\n[Clearing locks]")
        # Clear file position locks
        if pos_dir.exists():
            cleared = 0
            for pf in pos_dir.glob("*.pos"):
                pf.unlink()
                cleared += 1
            print(f"  Cleared {cleared} file position lock(s)")

        # Clear Redis position keys
        try:
            from agentibridge.redis_client import get_redis

            r = get_redis()
            if r is not None:
                prefix = os.getenv("REDIS_KEY_PREFIX", "agentibridge")
                cursor = 0
                cleared = 0
                while True:
                    cursor, keys = r.scan(cursor, match=f"{prefix}:sb:pos:*", count=100)
                    if keys:
                        r.delete(*keys)
                        cleared += len(keys)
                    if cursor == 0:
                        break
                print(f"  Cleared {cleared} Redis position key(s)")
        except Exception as e:
            print(f"  Redis clear failed: {e}")

        print("  Done. Next collection cycle will re-index from scratch.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentibridge",
        description="AgentiBridge — Claude CLI Transcript MCP Server",
    )
    subparsers = parser.add_subparsers(dest="command")

    # version
    subparsers.add_parser("version", help="Print version")

    # status
    subparsers.add_parser("status", help="Check service status and connectivity")

    # help
    subparsers.add_parser("help", help="Show available tools and configuration")

    # connect
    connect_parser = subparsers.add_parser("connect", help="Show connection strings for MCP clients")
    connect_parser.add_argument("--host", default=None, help="Server host (default: localhost)")
    connect_parser.add_argument("--port", default=None, help="Server port (default: 8100)")
    connect_parser.add_argument("--api-key", default=None, help="API key to include in examples")

    # tunnel
    subparsers.add_parser("tunnel", help="Show Cloudflare Tunnel status and URL")

    # config
    config_parser = subparsers.add_parser("config", help="Show current config or generate .env template")
    config_parser.add_argument("--generate-env", action="store_true", help="Print .env template")

    # install
    install_parser = subparsers.add_parser("install", help="Install as systemd user service")
    install_parser.add_argument(
        "--docker", dest="mode", action="store_const", const="docker", help="Docker-based service (default)"
    )
    install_parser.add_argument(
        "--native", dest="mode", action="store_const", const="native", help="Native Python service"
    )

    # uninstall
    subparsers.add_parser("uninstall", help="Remove systemd service")

    # locks
    locks_parser = subparsers.add_parser("locks", help="Show Redis keys, file locks, and bridge resource state")
    locks_parser.add_argument("--clear", action="store_true", help="Clear all position locks (forces re-index)")

    args = parser.parse_args()

    commands = {
        "version": cmd_version,
        "status": cmd_status,
        "help": cmd_help,
        "connect": cmd_connect,
        "tunnel": cmd_tunnel,
        "config": cmd_config,
        "install": cmd_install,
        "uninstall": cmd_uninstall,
        "locks": cmd_locks,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
