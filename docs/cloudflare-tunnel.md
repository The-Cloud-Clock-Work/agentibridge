# Cloudflare Tunnel Setup

Expose AgentiBridge to the internet securely using Cloudflare Tunnel. No port forwarding, firewall changes, or public IP required.

```
┌──────────┐     ┌─────────────────┐     ┌────────────┐     ┌────────────────┐
│  Remote  │────▶│  Cloudflare     │────▶│ cloudflared │────▶│ agentibridge │
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
agentibridge tunnel
# or
docker logs agentibridge-tunnel
```

The URL looks like `https://random-words.trycloudflare.com`. It changes each time the container restarts.

### Connect a remote client

```json
{
  "mcpServers": {
    "agentibridge": {
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
4. Name it (e.g., `agentibridge`)
5. Copy the tunnel token

### 2. Configure the route

In the tunnel configuration, add a **Public Hostname**:

| Field | Value |
|-------|-------|
| Subdomain | `bridge` (or your choice) |
| Domain | `example.com` (your domain) |
| Service Type | `HTTP` |
| URL | `agentibridge:8100` |

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
# {"status": "ok", "service": "agentibridge"}
```

## Named Tunnel via CLI (Non-Docker)

If you run agentibridge directly on the host (not in Docker), you can create a named tunnel using the `cloudflared` CLI. This is ideal for exposing a stable URL to GitHub Actions CI.

### 1. Install cloudflared

```bash
# Debian/Ubuntu
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# macOS
brew install cloudflared
```

### 2. Authenticate

```bash
cloudflared tunnel login
```

This opens your browser to authorize cloudflared with your Cloudflare account.

### 3. Create the tunnel

```bash
cloudflared tunnel create agentibridge
```

Note the tunnel UUID printed in the output. Credentials are saved to `~/.cloudflared/<tunnel-id>.json`.

### 4. Add a DNS route

Pick a subdomain on any domain already in your Cloudflare account:

```bash
cloudflared tunnel route dns agentibridge mcp.yourdomain.com
```

This creates a CNAME record `mcp.yourdomain.com` -> `<tunnel-id>.cfargotunnel.com` automatically. No manual DNS editing needed.

### 5. Configure the tunnel

Create `~/.cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /home/<user>/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: mcp.yourdomain.com
    service: http://localhost:8100
  - service: http_status:404
```

The `ingress` list must end with a catch-all rule. Port `8100` is the default `AGENTIBRIDGE_PORT`.

### 6. Run the tunnel

```bash
# Foreground (for testing)
cloudflared tunnel run agentibridge

# As a systemd service (persistent across reboots)
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

### 7. Verify

```bash
curl -s https://mcp.yourdomain.com/health
# {"status": "ok", "service": "agentibridge"}
```

### 8. Use in GitHub Actions

Set these in your repo settings (Settings > Secrets and variables > Actions):

| Type | Name | Value |
|------|------|-------|
| Variable | `MCP_SERVER_URL` | `https://mcp.yourdomain.com/mcp` |
| Secret | `MCP_API_KEY` | Your `AGENTIBRIDGE_API_KEYS` value |

The `e2e-smoke.yml` workflow uses these to generate `.mcp.json` and run smoke tests against your tunnel.

## Security Checklist

1. **Set API keys** — Always set `AGENTIBRIDGE_API_KEYS` when exposing to the internet:
   ```bash
   AGENTIBRIDGE_API_KEYS=my-secret-key-1,my-secret-key-2
   ```

2. **Use Cloudflare Access (optional)** — Add an Access policy in the Zero Trust dashboard for additional authentication (SSO, email OTP, etc.)

3. **TLS is automatic** — Cloudflare handles TLS termination at the edge. The connection between cloudflared and agentibridge stays internal to the Docker network.

4. **No ports exposed** — The tunnel does not require any inbound ports. All connections are outbound from cloudflared to Cloudflare's edge.

## Troubleshooting

### Tunnel URL not appearing

Quick tunnel URLs take a few seconds to register. Wait and retry:

```bash
sleep 10 && agentibridge tunnel
```

Or check raw logs:

```bash
docker logs agentibridge-tunnel 2>&1 | grep trycloudflare
```

### SSE connection drops

Cloudflare Tunnel handles long-lived connections well by default. If you experience drops:

- Ensure you're using `https://` (not `http://`) for the tunnel URL
- For named tunnels, set `noTLSVerify: true` in the tunnel config if the origin uses self-signed certs (not needed with the default Docker setup)

### Container won't start

The cloudflared container waits for agentibridge to be healthy:

```bash
# Check agentibridge health
docker inspect -f '{{.State.Health.Status}}' agentibridge

# Check cloudflared logs
docker logs agentibridge-tunnel
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
