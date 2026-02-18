# Reverse Proxy Configuration

Guide for exposing Agentic Bridge behind a reverse proxy with SSL termination.

## Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name bridge.example.com;

    ssl_certificate     /etc/letsencrypt/live/bridge.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bridge.example.com/privkey.pem;

    # SSE requires these settings
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 86400s;

    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE-specific headers
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
    }
}

# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name bridge.example.com;
    return 301 https://$host$request_uri;
}
```

## Caddy

```caddyfile
bridge.example.com {
    reverse_proxy localhost:8100 {
        # Disable buffering for SSE
        flush_interval -1
    }
}
```

Caddy automatically handles SSL via Let's Encrypt.

## Cloudflare Tunnel

```bash
# Install cloudflared
# https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/

# Create tunnel
cloudflared tunnel create agentic-bridge

# Configure
cat > ~/.cloudflared/config.yml << EOF
tunnel: <TUNNEL_ID>
credentials-file: /home/user/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: bridge.example.com
    service: http://localhost:8100
    originRequest:
      noTLSVerify: true
  - service: http_status:404
EOF

# Run
cloudflared tunnel run agentic-bridge
```

## Traefik (Docker Compose)

Add labels to your `docker-compose.yml`:

```yaml
services:
  session-bridge:
    # ... existing config ...
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.bridge.rule=Host(`bridge.example.com`)"
      - "traefik.http.routers.bridge.entrypoints=websecure"
      - "traefik.http.routers.bridge.tls.certresolver=letsencrypt"
      - "traefik.http.services.bridge.loadbalancer.server.port=8100"
      # Disable buffering for SSE
      - "traefik.http.middlewares.bridge-buffering.buffering.maxResponseBodyBytes=0"
      - "traefik.http.routers.bridge.middlewares=bridge-buffering"
```

## Important Notes

1. **SSE requires disabled buffering** — All proxy configurations must disable response buffering for Server-Sent Events to work correctly.

2. **Timeout settings** — SSE connections are long-lived. Set proxy timeouts high (24h+) or disable idle timeouts.

3. **API Key auth** — When using a reverse proxy, set `SESSION_BRIDGE_API_KEYS` to protect the endpoint. The key is passed in the `X-API-Key` header.

4. **Health checks** — The `/health` endpoint is unauthenticated and can be used for load balancer health checks.
