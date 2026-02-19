"""CLI helper tool for agentic-bridge.

Provides commands for status, config, connection strings, and service management.

Usage:
    agentic-bridge status     — Check if running, Redis connectivity, session count
    agentic-bridge help       — Available MCP tools and configuration reference
    agentic-bridge connect    — Connection strings for Claude Code, ChatGPT, etc.
    agentic-bridge config     — Current config dump / generate .env template
    agentic-bridge tunnel     — Cloudflare Tunnel status and URL
    agentic-bridge install    — Install as systemd user service
    agentic-bridge uninstall  — Remove systemd service
    agentic-bridge version    — Print version
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
    from agentic_bridge import __version__

    return __version__


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_version(args: argparse.Namespace) -> None:
    print(f"agentic-bridge {_version()}")


def cmd_status(args: argparse.Namespace) -> None:
    print(f"Agentic Bridge v{_version()}")
    print("=" * 50)

    # Check systemd service
    print("\n[Service]")
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "agentic-bridge"],
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
            ["docker", "inspect", "-f", "{{.State.Status}}", "session-bridge"],
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
        from agentic_bridge.redis_client import get_redis

        r = get_redis()
        if r is not None:
            r.ping()
            print("  status: connected")
            # Count sessions
            from agentic_bridge.store import _rkey

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
            ["docker", "inspect", "-f", "{{.State.Status}}", "session-bridge-tunnel"],
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
                    ["docker", "logs", "--tail", "50", "session-bridge-tunnel"],
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
            "SESSION_BRIDGE_PROJECTS_DIR",
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
    print(f"  transport: {os.getenv('SESSION_BRIDGE_TRANSPORT', 'stdio')}")
    print(f"  port: {os.getenv('SESSION_BRIDGE_PORT', '8100')}")
    print(f"  poll interval: {os.getenv('SESSION_BRIDGE_POLL_INTERVAL', '60')}s")


def cmd_help(args: argparse.Namespace) -> None:
    print(f"Agentic Bridge v{_version()} — Claude CLI Transcript MCP Server")
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
    print("  SESSION_BRIDGE_TRANSPORT        stdio or sse (default: stdio)")
    print("  SESSION_BRIDGE_HOST             Bind address (default: 127.0.0.1)")
    print("  SESSION_BRIDGE_PORT             HTTP port (default: 8100)")
    print("  SESSION_BRIDGE_API_KEYS         Comma-separated API keys")
    print("  SESSION_BRIDGE_POLL_INTERVAL    Poll interval in seconds (default: 60)")
    print("  SESSION_BRIDGE_MAX_ENTRIES      Max entries per session (default: 500)")
    print("  SESSION_BRIDGE_PROJECTS_DIR     Claude projects directory")
    print("  EMBEDDING_BACKEND              ollama or bedrock (optional)")
    print("  AGENTIC_BRIDGE_SUMMARY_MODEL   Model for summaries (default: claude-sonnet-4-5-20250929)")
    print("  CLOUDFLARE_TUNNEL_TOKEN        Token for named Cloudflare Tunnel (optional)")
    print()
    print("USAGE")
    print("-" * 60)
    print()
    print("  Local (stdio):   python -m agentic_bridge")
    print("  Remote (SSE):    SESSION_BRIDGE_TRANSPORT=sse python -m agentic_bridge")
    print("  Docker:          docker compose up --build -d")
    print("  Tunnel:          docker compose --profile tunnel up -d")
    print(
        "  All-in-one:      docker run -d -p 8100:8100 -v ~/.claude/projects:/home/appuser/.claude/projects:ro agentic-bridge:allinone"
    )
    print()
    print("Run 'agentic-bridge connect' for client connection strings.")


def cmd_connect(args: argparse.Namespace) -> None:
    host = args.host or os.getenv("SESSION_BRIDGE_HOST", "localhost")
    port = args.port or os.getenv("SESSION_BRIDGE_PORT", "8100")
    api_key = args.api_key or "your-api-key"

    print(f"Connection strings for {host}:{port}")
    print("=" * 60)

    print()
    print("=== Claude Code CLI ===")
    print("Add to ~/.mcp.json:")
    config = {
        "mcpServers": {
            "session-bridge": {
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
    print("  Then run 'agentic-bridge tunnel' to get the public URL.")

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
            ["docker", "inspect", "-f", "{{.State.Status}}", "session-bridge-tunnel"],
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
        print("  docker logs session-bridge-tunnel")
        return

    # Read logs to detect mode and URL
    try:
        log_result = subprocess.run(
            ["docker", "logs", "--tail", "50", "session-bridge-tunnel"],
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
                "session-bridge": {
                    "url": f"{url}/sse",
                }
            }
        }
        api_keys = os.getenv("SESSION_BRIDGE_API_KEYS", "")
        if api_keys:
            first_key = api_keys.split(",")[0].strip()
            config["mcpServers"]["session-bridge"]["headers"] = {"X-API-Key": first_key}
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
        print("  docker logs session-bridge-tunnel")
        return

    # Unknown state
    print("Tunnel is running but could not determine mode.")
    print("Check logs: docker logs session-bridge-tunnel")


def cmd_config(args: argparse.Namespace) -> None:
    if args.generate_env:
        _generate_env_template()
        return

    print("Current Configuration")
    print("=" * 50)

    env_vars = [
        ("REDIS_URL", ""),
        ("REDIS_KEY_PREFIX", "agenticore"),
        ("SESSION_BRIDGE_TRANSPORT", "stdio"),
        ("SESSION_BRIDGE_HOST", "127.0.0.1"),
        ("SESSION_BRIDGE_PORT", "8100"),
        ("SESSION_BRIDGE_API_KEYS", ""),
        ("SESSION_BRIDGE_POLL_INTERVAL", "60"),
        ("SESSION_BRIDGE_MAX_ENTRIES", "500"),
        ("SESSION_BRIDGE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects")),
        ("SESSION_BRIDGE_ENABLED", "true"),
        ("EMBEDDING_BACKEND", ""),
        ("AGENTIC_BRIDGE_SUMMARY_MODEL", "claude-sonnet-4-5-20250929"),
        ("CLAUDE_HOOK_LOG_ENABLED", "true"),
        ("AGENTIC_BRIDGE_LOG_FILE", ""),
    ]

    for key, default in env_vars:
        val = os.getenv(key, "")
        source = "env" if val else "default"
        display = val if val else default if default else "(not set)"
        print(f"  {key}={display}  [{source}]")


def _generate_env_template() -> None:
    template = """# Agentic Bridge Configuration
# Copy to ~/.config/agentic-bridge/env or .env

# Redis (optional — falls back to filesystem)
# REDIS_URL=redis://localhost:6379/0
# REDIS_KEY_PREFIX=agenticore

# Transport: stdio (local MCP) or sse (HTTP remote)
SESSION_BRIDGE_TRANSPORT=stdio
SESSION_BRIDGE_HOST=127.0.0.1
SESSION_BRIDGE_PORT=8100

# API key auth for SSE transport (comma-separated, empty = no auth)
# SESSION_BRIDGE_API_KEYS=key1,key2

# Collector
SESSION_BRIDGE_POLL_INTERVAL=60
SESSION_BRIDGE_MAX_ENTRIES=500
# SESSION_BRIDGE_PROJECTS_DIR=~/.claude/projects

# Semantic search (Phase 2)
# EMBEDDING_BACKEND=ollama
# OLLAMA_URL=http://localhost:11434
# OLLAMA_EMBED_MODEL=nomic-embed-text

# Summary generation model
# AGENTIC_BRIDGE_SUMMARY_MODEL=claude-sonnet-4-5-20250929

# Logging
CLAUDE_HOOK_LOG_ENABLED=true
# AGENTIC_BRIDGE_LOG_FILE=~/.cache/agentic-bridge/agentic-bridge.log

# Cloudflare Tunnel (optional — use docker compose --profile tunnel)
# CLOUDFLARE_TUNNEL_TOKEN=your-tunnel-token-here
"""
    print(template)


def cmd_install(args: argparse.Namespace) -> None:
    mode = args.mode or "docker"
    config_dir = Path.home() / ".config" / "agentic-bridge"
    systemd_dir = Path.home() / ".config" / "systemd" / "user"

    print(f"Installing agentic-bridge as systemd user service (mode: {mode})")

    # Create config directory
    config_dir.mkdir(parents=True, exist_ok=True)
    env_file = config_dir / "env"
    if not env_file.exists():
        env_file.write_text(
            "# Agentic Bridge environment\n"
            "SESSION_BRIDGE_TRANSPORT=sse\n"
            "SESSION_BRIDGE_HOST=0.0.0.0\n"
            "SESSION_BRIDGE_PORT=8100\n"
            "# SESSION_BRIDGE_API_KEYS=\n"
            "# REDIS_URL=redis://localhost:6379/0\n"
        )
        print(f"  Created {env_file}")

    # Determine service file
    pkg_dir = Path(__file__).parent.parent
    if mode == "docker":
        service_src = pkg_dir / "deploy" / "agentic-bridge.service"
    else:
        service_src = pkg_dir / "deploy" / "agentic-bridge-native.service"

    if not service_src.exists():
        print(f"  ERROR: Service file not found: {service_src}")
        print("  Make sure the deploy/ directory is present.")
        sys.exit(1)

    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_dest = systemd_dir / "agentic-bridge.service"
    shutil.copy2(service_src, service_dest)
    print(f"  Installed {service_dest}")

    # Enable and start
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "agentic-bridge"], check=True)
    subprocess.run(["systemctl", "--user", "start", "agentic-bridge"], check=True)
    print("  Service enabled and started")
    print()
    print("Check status with: agentic-bridge status")
    print("View logs with: journalctl --user -u agentic-bridge -f")


def cmd_uninstall(args: argparse.Namespace) -> None:
    print("Uninstalling agentic-bridge systemd service...")

    try:
        subprocess.run(["systemctl", "--user", "stop", "agentic-bridge"], check=False)
        subprocess.run(["systemctl", "--user", "disable", "agentic-bridge"], check=False)
    except Exception:
        pass

    service_file = Path.home() / ".config" / "systemd" / "user" / "agentic-bridge.service"
    if service_file.exists():
        service_file.unlink()
        print(f"  Removed {service_file}")

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    except Exception:
        pass

    print("  Service uninstalled")
    print()
    print("Note: Config files in ~/.config/agentic-bridge/ were preserved.")
    print("Remove manually if no longer needed.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentic-bridge",
        description="Agentic Bridge — Claude CLI Transcript MCP Server",
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
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
