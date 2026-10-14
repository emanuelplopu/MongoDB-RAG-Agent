# LAN gateway (Caddy)

`docker-compose.yml` runs one Caddy container per tenant (`gateway-quellex`,
`gateway`) as the only service other computers on the office network are meant
to reach. Everything else (frontend nginx, backend API, MongoDB, embedder) binds
to `BIND_ADDRESS` (default `127.0.0.1`).

| Tenant   | Address                          | Host ports (override in `.env`)                         |
|----------|----------------------------------|---------------------------------------------------------|
| Quellex  | `https://quellex.local`          | `QUELLEX_GATEWAY_HTTPS_PORT=443`, `QUELLEX_GATEWAY_HTTP_PORT=80` |
| RecallHub| `https://recallhub.local:11443`  | `RECALLHUB_GATEWAY_HTTPS_PORT=11443`, `RECALLHUB_GATEWAY_HTTP_PORT=11480` |

`Caddyfile.template` is rendered at container start: `__HOSTS__` comes from
`GATEWAY_HOSTS` (`QUELLEX_GATEWAY_HOSTS` / `RECALLHUB_GATEWAY_HOSTS` in `.env`,
comma-separated site addresses, add `https://<LAN-IP>` to allow the raw IP) and
`__UPSTREAM__` from the tenant frontend service.

What the gateway does:

- TLS with a certificate from Caddy's own local CA (`tls internal`). Export the
  root certificate with `installer\scripts\Export-LanCertificate.ps1` and trust
  it on the client computers so browsers show no warning.
- Redirects `http://` to `https://`, sends HSTS, strips the `Server` header.
- Streams Server-Sent Events (chat answers, ingestion logs) without buffering.

What it deliberately does not do:

- Authentication. The Quellex backend runs with `REQUIRE_AUTH=true`
  (`backend/core/auth_middleware.py`): every API call needs a valid session or
  API key. The frontend mirrors that rule and starts on the login page.
- Name resolution. `quellex.local` / `quellex` are announced on the LAN by the
  desktop tray app (`installer/tray-app/LanNameAdvertiser.cs`, mDNS + LLMNR).

Full guide: `docs/21-LAN_ACCESS.md`.
