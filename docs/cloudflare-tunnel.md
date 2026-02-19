# Cloudflare Tunnel Setup

Expose Agentic Bridge to the internet securely using Cloudflare Tunnel. No port forwarding, firewall changes, or public IP required.

```
┌──────────┐     ┌─────────────────┐     ┌────────────┐     ┌────────────────┐
│  Remote  │────▶│  Cloudflare     │────▶│ cloudflared │────▶│ session-bridge │
│  Client  │ TLS │  Edge Network   │     │ (container) │     │ :8100          │
└──────────┘     └─────────────────┘     └────────────┘     └────────────────┘
```

## Quick Tunnel (Zero Config)

No Cloudflare account needed. Generates a temporary `*.trycloudflare.com` URL.

```bash
docker compose --profile tunnel up -d
```

Get the tunnel URL:

```bash
agentic-bridge tunnel
# or
docker logs session-bridge-tunnel
```

The URL looks like `https://random-words.trycloudflare.com`. It changes each time the container restarts.

### Connect a remote client

```json
{
  "mcpServers": {
    "session-bridge": {
      "url": "https://random-words.trycloudflare.com/sse"
    }
  }
}
```

## Named Tunnel (Persistent Hostname)

For a stable hostname that survives restarts.

### 1. Create a Cloudflare Tunnel

1. Go to [Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/)
2. Navigate to **Networks > Tunnels**
3. Click **Create a tunnel** > **Cloudflared**
4. Name it (e.g., `agentic-bridge`)
5. Copy the tunnel token

### 2. Configure the route

In the tunnel configuration, add a **Public Hostname**:

| Field | Value |
|-------|-------|
| Subdomain | `bridge` (or your choice) |
| Domain | `example.com` (your domain) |
| Service Type | `HTTP` |
| URL | `session-bridge:8100` |

### 3. Start with token

```bash
CLOUDFLARE_TUNNEL_TOKEN=eyJh... docker compose --profile tunnel up -d
```

Or add to `.env`:

```bash
CLOUDFLARE_TUNNEL_TOKEN=eyJh...
```

Then:

```bash
docker compose --profile tunnel up -d
```

### 4. Verify

```bash
curl https://bridge.example.com/health
# {"status": "ok", "service": "session-bridge"}
```

## Security Checklist

1. **Set API keys** — Always set `SESSION_BRIDGE_API_KEYS` when exposing to the internet:
   ```bash
   SESSION_BRIDGE_API_KEYS=my-secret-key-1,my-secret-key-2
   ```

2. **Use Cloudflare Access (optional)** — Add an Access policy in the Zero Trust dashboard for additional authentication (SSO, email OTP, etc.)

3. **TLS is automatic** — Cloudflare handles TLS termination at the edge. The connection between cloudflared and session-bridge stays internal to the Docker network.

4. **No ports exposed** — The tunnel does not require any inbound ports. All connections are outbound from cloudflared to Cloudflare's edge.

## Troubleshooting

### Tunnel URL not appearing

Quick tunnel URLs take a few seconds to register. Wait and retry:

```bash
sleep 10 && agentic-bridge tunnel
```

Or check raw logs:

```bash
docker logs session-bridge-tunnel 2>&1 | grep trycloudflare
```

### SSE connection drops

Cloudflare Tunnel handles long-lived connections well by default. If you experience drops:

- Ensure you're using `https://` (not `http://`) for the tunnel URL
- For named tunnels, set `noTLSVerify: true` in the tunnel config if the origin uses self-signed certs (not needed with the default Docker setup)

### Container won't start

The cloudflared container waits for session-bridge to be healthy:

```bash
# Check session-bridge health
docker inspect -f '{{.State.Health.Status}}' session-bridge

# Check cloudflared logs
docker logs session-bridge-tunnel
```

### Stopping the tunnel

```bash
# Stop just the tunnel
docker compose --profile tunnel stop cloudflared

# Stop everything
docker compose --profile tunnel down
```

## How It Works

The `docker-compose.yml` includes a `cloudflared` service behind the `tunnel` profile. This means `docker compose up` works normally without it — you only activate the tunnel when you explicitly use `--profile tunnel`.

The container detects its mode automatically:
- **`CLOUDFLARE_TUNNEL_TOKEN` set**: Runs as a named tunnel with your persistent hostname
- **`CLOUDFLARE_TUNNEL_TOKEN` unset**: Runs as a quick tunnel with a temporary URL
