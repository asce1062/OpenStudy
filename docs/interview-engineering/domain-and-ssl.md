# Domain And SSL

This page uses `learn.alexmbugua.me` as a concrete example. Replace it with
your own hostname.

## Target Shape

```mermaid
flowchart LR
    Browser["Browser / Claude client"] --> DNS["learn.alexmbugua.me"]
    DNS --> Proxy["TLS reverse proxy"]
    Proxy --> Frontend["frontend container :8080"]
    Frontend --> Backend["openstudy container :8000"]
    Backend --> Postgres["postgres container"]
```

The public domain should point at the frontend/reverse proxy entrypoint. The
frontend container serves the SPA and proxies API/MCP traffic to the backend on
the internal Docker network.

## DNS

Create one DNS record:

```text
Type: A
Name: learn
Value: <server IPv4 address>
Proxy: optional, depending on provider
```

If your server has IPv6:

```text
Type: AAAA
Name: learn
Value: <server IPv6 address>
```

## Coolify Domain

In Coolify, attach the domain to the frontend service or the exposed application
entrypoint:

```text
learn.alexmbugua.me
```

Enable HTTPS/TLS in Coolify. Coolify should provision and renew certificates if
DNS is pointed correctly.

## Docker Compose Reverse Proxy

The repository Compose file binds:

```text
frontend: 127.0.0.1:8080
openstudy: 127.0.0.1:8000
```

An outer proxy should send public HTTPS traffic to:

```text
127.0.0.1:8080
```

Example Caddy config:

```caddy
learn.alexmbugua.me {
    encode gzip
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1
    }
}
```

`flush_interval -1` is useful for streaming endpoints such as MCP.

## Frontend Public URL

Set public frontend metadata before building:

```text
PUBLIC_SITE_URL=https://learn.alexmbugua.me
PUBLIC_SITE_NAME=OpenStudy
PUBLIC_SHOW_LANDING=false
```

These values are build-time frontend settings used for canonical URLs,
OpenGraph tags, robots, sitemap, and manifest metadata.

## Backend Public URL

If OAuth or MCP clients need an externally visible URL, set the backend public
URL according to the app's existing environment conventions:

```text
PUBLIC_URL=https://learn.alexmbugua.me
```

Keep this consistent with the domain users and Claude clients will access.

## Health Checks

From the server:

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8080/
```

From outside:

```bash
curl -fsS https://learn.alexmbugua.me/
curl -fsS https://learn.alexmbugua.me/api/health
```

The API health response should include `ok: true`.

## Cloudflare Notes

Cloudflare can work well as DNS and proxy, but keep these points in mind:

- Use `Full` or `Full (strict)` SSL mode when the origin has a certificate.
- Do not expose Postgres.
- Make sure WebSocket/streaming behavior is not blocked for MCP.
- Disable aggressive caching for API and MCP paths.

Suggested cache bypass paths:

```text
/api/*
/mcp
/oauth/*
```

## Common Mistakes

- Pointing DNS to the backend port instead of the frontend entrypoint.
- Forgetting to rebuild after changing `PUBLIC_SITE_URL`.
- Using a Cloudflare mode that creates redirect loops.
- Exposing Postgres to the public internet.
- Testing only `/` and not `/api/health` or `/mcp` connectivity.

