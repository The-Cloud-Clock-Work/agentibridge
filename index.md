---
layout: home
title: Home
nav_order: 1
description: "AgentiBridge — Persistent session controller for your Claude Code agents. Search, resume, and dispatch AI sessions from anywhere."
permalink: /
---

# Your AI agents don't sleep. Now neither does their memory.
{: .fs-9 .fw-700 .text-center .mb-4 }

Step away from your desk and productivity drops to zero. AgentiBridge makes your Claude Code sessions persistent, searchable, and remotely controllable — from any MCP client.
{: .fs-5 .text-center .text-grey-dk-100 .mb-6 }

![AgentiBridge - Persistent session controller for your AI Agents](docs/media/agentibridge-readme-banner.jpg){: .d-block .mx-auto .mb-6 }

<div class="hero-actions text-center mb-8" markdown="0">
  <a href="#quick-start" class="btn btn-primary fs-5 mr-2">Get Started</a>
  <a href="https://github.com/The-Cloud-Clock-Work/agentibridge" class="btn fs-5" target="_blank">View on GitHub</a>
</div>

[![PyPI](https://img.shields.io/pypi/v/agentibridge)](https://pypi.org/project/agentibridge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/The-Cloud-Clock-Work/agentibridge/blob/main/LICENSE)
[![Tests](https://github.com/The-Cloud-Clock-Work/agentibridge/actions/workflows/test.yml/badge.svg)](https://github.com/The-Cloud-Clock-Work/agentibridge/actions/workflows/test.yml)
[![Docker](https://img.shields.io/docker/v/tccw/agentibridge?label=Docker%20Hub)](https://hub.docker.com/r/tccw/agentibridge)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://python.org)
{: .text-center .mb-8 }

---

## The Problem

Your Claude Code sessions disappear when the terminal closes. Your IDE history is locked to one machine. When you leave your desk, your agent productivity drops to zero.

**AgentiBridge fixes this.**

It indexes every Claude Code transcript automatically, makes them searchable with AI-powered semantic search, and lets you dispatch and monitor tasks from any device — your phone, another laptop, or claude.ai.

---

## AgentiBridge vs Anthropic Remote Control

Anthropic's [Remote Control](https://code.claude.com/docs/en/remote-control) (Feb 2026) continues **one live session** from your phone. AgentiBridge is the **other 90%** of remote agent control:

| | Remote Control | AgentiBridge |
|---|---|---|
| **What it does** | Continue one live session from phone/browser | Index all past sessions, search, dispatch new ones |
| **Session scope** | 1 active session | All sessions across all projects |
| **Search** | None | Keyword + semantic search |
| **Memory/Plans** | None | Catalog memory files, plans, history |
| **Dispatch** | None | Fire-and-forget background task dispatch |
| **Context restore** | Only the live session | Restore any past session's context |
| **Multi-client** | claude.ai + Claude app only | Claude Code, claude.ai, ChatGPT, Grok, any MCP client |
| **Runs where** | Local terminal (must stay open) | MCP server (Docker/stdio), works offline |
| **Auth** | Pro/Max subscription | Self-hosted, no subscription needed |

**Bottom line:** Remote Control is a "remote desktop for one session." AgentiBridge is a **knowledge base and orchestration layer** across all sessions. They're complementary, not competitive.

---

## See It In Action

![claude.ai using AgentiBridge list_sessions tool](docs/media/examples/claude-ai-list-sessions.jpg){: .d-block .mx-auto .mb-4 }

AgentiBridge works from Claude Code CLI, claude.ai, ChatGPT, and any MCP client. [See more screenshots &rarr;](docs/examples)
{: .fs-5 .text-center .text-grey-dk-100 .mb-6 }

---

## Features

<div class="feature-grid">
  <div class="feature-card">
    <img src="{{ '/assets/images/feature-search.svg' | relative_url }}" alt="Semantic Search" class="feature-icon">
    <h3>Semantic Search</h3>
    <p>Ask natural language questions across all your past sessions. Powered by pgvector embeddings — no keyword matching needed.</p>
  </div>
  <div class="feature-card">
    <img src="{{ '/assets/images/feature-dispatch.svg' | relative_url }}" alt="Background Dispatch" class="feature-icon">
    <h3>Background Dispatch</h3>
    <p>Fire-and-forget task dispatch with session restore. Resume work where you left off, from any device.</p>
  </div>
  <div class="feature-card">
    <img src="{{ '/assets/images/feature-security.svg' | relative_url }}" alt="Security First" class="feature-icon">
    <h3>Security First</h3>
    <p>OAuth 2.1 with PKCE, API key auth, Cloudflare Tunnel. Fully self-hosted — your data never leaves your infrastructure.</p>
  </div>
  <div class="feature-card">
    <img src="{{ '/assets/images/feature-multiclient.svg' | relative_url }}" alt="Multi-Client Fleet" class="feature-icon">
    <h3>Multi-Client Fleet</h3>
    <p>Works with Claude Code CLI, claude.ai, ChatGPT, Grok, and any MCP-compatible client. One server, all your clients.</p>
  </div>
</div>

---

## Quick Start
{: #quick-start }

```bash
pip install agentibridge
```

AgentiBridge supports two connection modes. Pick one or use both.

### Local (stdio) — Zero config
{: .fs-5 .fw-500 }

Runs as a subprocess alongside Claude Code. No server, no auth. Add to `.mcp.json`:

```json
{
  "mcpServers": {
    "agentibridge": {
      "command": "python",
      "args": ["-m", "agentibridge"]
    }
  }
}
```

### Remote (HTTP + API key) — Access from anywhere
{: .fs-5 .fw-500 }

Runs as a persistent server. Access your sessions from your phone, another laptop, or claude.ai:

```json
{
  "mcpServers": {
    "agentibridge": {
      "type": "http",
      "url": "https://bridge.yourdomain.com/mcp",
      "headers": {
        "X-API-Key": "sk-ab-your-api-key-here"
      }
    }
  }
}
```

### Both at once

Local for speed, remote for mobility — run them side by side:

```json
{
  "mcpServers": {
    "agentibridge": {
      "command": "python",
      "args": ["-m", "agentibridge"]
    },
    "agentibridge-remote": {
      "type": "http",
      "url": "https://bridge.yourdomain.com/mcp",
      "headers": {
        "X-API-Key": "sk-ab-your-api-key-here"
      }
    }
  }
}
```

That's it. Your Claude Code sessions are now searchable from any MCP-compatible client.

{: .note }
> Zero dependencies to start — filesystem-only storage out of the box. Add Redis for caching and Postgres for semantic search when you need them.

---

## 16 MCP Tools

### Foundation

| Tool | What it does |
|:-----|:-------------|
| `list_sessions` | List sessions across all projects |
| `get_session` | Full session metadata + transcript |
| `get_session_segment` | Paginated/time-range transcript retrieval |
| `get_session_actions` | Extract tool calls with counts |
| `search_sessions` | Keyword search across transcripts |
| `collect_now` | Trigger immediate collection |

### AI-Powered

| Tool | What it does |
|:-----|:-------------|
| `search_semantic` | Semantic search using embeddings |
| `generate_summary` | Auto-generate session summary via LLM |

### Dispatch

| Tool | What it does |
|:-----|:-------------|
| `restore_session` | Load session context for continuation |
| `dispatch_task` | Fire-and-forget background job dispatch |
| `get_dispatch_job` | Poll a background job for status and output |

### Knowledge Catalog

| Tool | What it does |
|:-----|:-------------|
| `list_memory_files` | List memory files across projects |
| `get_memory_file` | Read a specific memory file |
| `list_plans` | List plans sorted by recency |
| `get_plan` | Read a plan by codename |
| `search_history` | Search the global prompt history |

---

## Architecture

```mermaid
flowchart LR
    E([Any AI Client]) -->|query| D{{MCP Tools}}
    D -->|read| C[(SessionStore)]
    C -->|indexed by| B[Collector]
    B -->|watches| A([Claude Code sessions])

    classDef sessions fill:#6366f1,stroke:#4338ca,color:#fff
    classDef collector fill:#f59e0b,stroke:#d97706,color:#fff
    classDef store fill:#10b981,stroke:#059669,color:#fff
    classDef tools fill:#8b5cf6,stroke:#7c3aed,color:#fff
    classDef client fill:#06b6d4,stroke:#0284c7,color:#fff

    class A sessions
    class B collector
    class C store
    class D tools
    class E client
```

---

## Connect to Claude.ai
{: #claude-ai }

Claude.ai requires OAuth 2.1 to connect to remote MCP servers. AgentiBridge includes a built-in OAuth 2.1 authorization server — enable it with one env var.

**1. Add to your `.env`:**

```bash
OAUTH_ISSUER_URL=https://bridge.yourdomain.com
```

**2. Expose over HTTPS** (Cloudflare Tunnel or reverse proxy):

```bash
agentibridge tunnel setup
```

**3. Add to claude.ai** at [claude.ai/settings/connectors](https://claude.ai/settings/connectors):

```
https://bridge.yourdomain.com/mcp
```

Claude.ai automatically discovers OAuth metadata, registers as a client, and completes the PKCE flow. No manual JSON config needed.

{: .note }
> For production, set `OAUTH_CLIENT_ID` and `OAUTH_CLIENT_SECRET` to lock down registration to a single pre-configured client. API key auth (`X-API-Key`) continues to work alongside OAuth.

---

## Deployment Options

| | Minimal | Standard | Production |
|:--|:--------|:---------|:-----------|
| **Install** | `pip install agentibridge` | `docker compose up -d` | Docker + Cloudflare Tunnel |
| **Storage** | Filesystem only | Redis + filesystem | Redis + Postgres (pgvector) |
| **Search** | Keyword only | Keyword only | Keyword + semantic |
| **Access** | Local only | Local network | Internet (HTTPS) |
| **Auth** | None | API key | OAuth 2.1 + API key |
| **Dispatch** | N/A | Local bridge | Remote bridge |

---

## FAQ

<details markdown="block">
<summary><strong>Isn't this just session history?</strong></summary>

History is the data layer. The product is remote fleet control — dispatch tasks from your phone, search sessions from any MCP client, monitor jobs from claude.ai. You go from 0% productivity away from your desk to controlling your agents from anywhere.
</details>

<details markdown="block">
<summary><strong>VS Code / Cursor already has conversation history.</strong></summary>

IDE conversation history is excellent for local replay within that IDE. AgentiBridge serves CLI-first developers and adds capabilities no IDE provides: remote multi-client access, background dispatch from any device, and semantic search across your full session history.
</details>

<details markdown="block">
<summary><strong>Won't Anthropic build this natively?</strong></summary>

AgentiBridge is self-hosted, vendor-neutral infrastructure. Native features optimize for one vendor's client. AgentiBridge works with Claude Code, claude.ai, ChatGPT, Grok, and any MCP client. Your data stays on your machine. MIT licensed — no lock-in.
</details>

<details markdown="block">
<summary><strong>Do I need Redis and Postgres?</strong></summary>

No. `pip install agentibridge && agentibridge run` works with zero dependencies — filesystem-only storage out of the box. Add Redis for caching and Postgres for semantic search when you need them.
</details>

<details markdown="block">
<summary><strong>Is my data sent anywhere?</strong></summary>

No. No telemetry, no SaaS dependencies. Cloudflare Tunnel is opt-in, and even then only MCP tool responses traverse the tunnel — your transcripts stay local.
</details>

<details markdown="block">
<summary><strong>How is this different from Anthropic's Remote Control?</strong></summary>

Remote Control continues one active Claude Code session from your phone — great for live interaction. AgentiBridge indexes all past sessions, adds semantic search, background dispatch, context restore, and multi-client support. They solve different problems and work great together.
</details>

<details markdown="block">
<summary><strong>Which clients are supported?</strong></summary>

Claude Code CLI, claude.ai, ChatGPT, Grok, and any MCP-compatible client. Run `agentibridge connect` for ready-to-paste configs.
</details>

---

## Code Quality

Continuous static analysis via [SonarQube](https://sonar.homeofanton.com/dashboard?id=agentibridge) ensures code quality, security, and maintainability.

---

<div class="text-center mb-4" markdown="0">
  <p class="fs-5 fw-500">Ready to make your AI agents persistent?</p>
  <code class="fs-4">pip install agentibridge</code>
</div>

[Get Started](docs/getting-started/connecting-clients){: .btn .btn-primary .fs-5 .mr-2 }
[Documentation](docs/){: .btn .fs-5 .mr-2 }
[View on GitHub](https://github.com/The-Cloud-Clock-Work/agentibridge){: .btn .fs-5 }
{: .text-center }
