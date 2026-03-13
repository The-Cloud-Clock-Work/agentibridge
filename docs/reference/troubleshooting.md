---
title: Troubleshooting
nav_order: 4
---

# Troubleshooting

Common issues and solutions for AgentiBridge.

---

## Postgres password authentication failed

```
FATAL: password authentication failed for user "agentibridge"
```

**Cause:** The `POSTGRES_PASSWORD` env var only sets the password when the Postgres data volume is first initialized. If you change the password in `docker.env` after the volume already exists, the running Postgres still uses the old password.

**Fix (preserve data):** Update the password inside Postgres to match your `docker.env`:

```bash
docker exec agentibridge-postgres psql -U agentibridge \
  -c "ALTER USER agentibridge PASSWORD 'your-new-password';"
```

**Fix (fresh start):** Delete the volume and recreate (loses all stored data):

```bash
agentibridge stop
docker volume rm sb_postgres_data
agentibridge run
```

---

## Semantic search returns "not available"

```json
{"success": false, "error": "Embedding backend not available..."}
```

**Cause:** Semantic search is opt-in. Three things must all be configured:

1. **`AGENTIBRIDGE_EMBEDDING_ENABLED=true`** — the feature flag (defaults to `false`)
2. **LLM embedding config** — `LLM_API_BASE`, `LLM_API_KEY`, `LLM_EMBED_MODEL`
3. **Postgres with pgvector** — `POSTGRES_URL` pointing to a pgvector-enabled database

**Fix:** Add all three to your `docker.env` (or `.env` for native mode):

```bash
AGENTIBRIDGE_EMBEDDING_ENABLED=true
LLM_API_BASE=https://your-llm-endpoint/v1
LLM_API_KEY=your-key
LLM_EMBED_MODEL=text-embedding-3-small
```

Then recreate the container (see next section).

---

## Config changes not taking effect after editing docker.env

**Cause:** `agentibridge restart` restarts the same containers with the same environment. It does **not** reload `docker.env`.

**Fix:** Stop and recreate the containers:

```bash
agentibridge stop && agentibridge run
```

Or with Docker Compose directly:

```bash
docker compose -f ~/.agentibridge/docker-compose.yml up -d --force-recreate
```

---

## LLM endpoint unreachable from Docker

```
httpx.ConnectError: [Errno -2] Name or address not found
```

**Cause:** The container cannot reach your LLM endpoint. Common reasons:

- **DNS resolution** — Docker containers use their own DNS. `localhost` inside the container refers to the container itself, not the host.
- **Cloudflare Access** — If your LLM proxy is behind Cloudflare Access, the container needs service-token headers.

**Fix (host services):** Use `host.docker.internal` instead of `localhost`:

```bash
LLM_API_BASE=http://host.docker.internal:4000/v1
```

**Fix (Cloudflare Access):** Add service-token credentials to `docker.env`:

```bash
CF_ACCESS_CLIENT_ID=your-client-id.access
CF_ACCESS_CLIENT_SECRET=your-client-secret
```

The LLM client sends these as `CF-Access-Client-Id` / `CF-Access-Client-Secret` headers automatically when set.

---

## Connection refused / 401 Unauthorized

See [Connecting Clients — Troubleshooting](../getting-started/connecting-clients.md#troubleshooting) for connection and authentication issues.

---

## No semantic search results (0 chunks)

```json
{"success": true, "results": [], "total": 0}
```

**Cause:** Sessions haven't been embedded yet. Embedding happens automatically during each collector poll cycle when `AGENTIBRIDGE_EMBEDDING_ENABLED=true` is set.

**Diagnose with the CLI:**

```bash
agentibridge embeddings              # shows config, chunk counts, coverage
agentibridge embeddings --check-llm  # also tests LLM endpoint connectivity
```

**Common reasons for 0 chunks:**
- Collector hasn't completed its first cycle yet (wait ~60s after startup)
- `AGENTIBRIDGE_EMBEDDING_ENABLED` is not set to `true`
- LLM endpoint is unreachable from the container (see "LLM endpoint unreachable" above)
- Using `agentibridge run --test` but embedding config is only in `docker.env` (not the repo root `.env`) — see the [env file table](cli-commands.md#--test--local-dev-testing-mode)

**Fix:** Trigger an immediate collection:

```bash
# Via MCP tool
collect_now

# Or wait for the next poll cycle (default: 60 seconds)
```

Check the chunk count directly:

```bash
docker exec agentibridge-postgres psql -U agentibridge \
  -c "SELECT count(*) FROM transcript_chunks;"
```
