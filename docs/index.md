# AgentiBridge Documentation

AgentiBridge is a standalone MCP server that indexes Claude Code CLI transcripts and exposes them via 16 MCP tools.

## Getting Started

- [Connecting Clients](getting-started/connecting-clients.md) — Connect Claude Code, claude.ai, ChatGPT, and other MCP clients

## Architecture

- [Semantic Search](architecture/semantic-search.md) — Natural language search across transcripts using embeddings
- [Remote Access](architecture/remote-access.md) — SSE/HTTP transport with API key authentication
- [Session Dispatch](architecture/session-dispatch.md) — Fire-and-forget background jobs, session resume, and context injection
- [Knowledge Catalog](architecture/knowledge-catalog.md) — Memory files, plans, and prompt history

## Deployment

- [Releases](deployment/releases.md) — Release automation, versioning, PyPI, Docker Hub, and GHCR publish workflows
- [Reverse Proxy](deployment/reverse-proxy.md) — Nginx, Caddy, and Traefik configurations with SSL
- [Cloudflare Tunnel](deployment/cloudflare-tunnel.md) — Secure internet exposure without port forwarding

## Reference

- [CLI Commands](reference/cli-commands.md) — All `agentibridge` commands with options and examples
- [Configuration](reference/configuration.md) — All environment variables with defaults
