# Lightweight standalone image for the Agentic Bridge MCP server.
# Indexes Claude CLI transcripts from ~/.claude/projects/ and serves
# them via SSE/HTTP for remote MCP clients.
#
# Build:
#   docker build -t agentic-bridge .
#
# ~200MB vs ~1.5GB for the full agent image (no Claude CLI, Node, AWS CLI, gh).

FROM python:3.12-slim

# Minimal runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 -s /bin/bash appuser

WORKDIR /app

# Install Python dependencies (pinned to what agentic-bridge actually needs)
RUN pip install --no-cache-dir \
    "fastmcp>=2.0" \
    "redis>=7.0" \
    "uvicorn[standard]>=0.30" \
    "httpx>=0.25" \
    "anthropic>=0.40"

# Copy agentic_bridge package
COPY --chown=appuser:appuser agentic_bridge/ ./agentic_bridge/

# Create log directory
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

# Create .claude/projects mount target
RUN mkdir -p /home/appuser/.claude/projects && \
    chown -R appuser:appuser /home/appuser/.claude

USER appuser

# Expose SSE port
EXPOSE 8100

# Environment defaults
ENV PYTHONPATH=/app \
    SESSION_BRIDGE_TRANSPORT=sse \
    SESSION_BRIDGE_HOST=0.0.0.0 \
    SESSION_BRIDGE_PORT=8100 \
    SESSION_BRIDGE_POLL_INTERVAL=60

# Health check against /health (unauthenticated)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8100/health || exit 1

CMD ["python3", "-m", "agentic_bridge"]
