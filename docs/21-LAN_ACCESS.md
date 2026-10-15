# 21 - Network Access (https://quellex.local)

Last reviewed: 2026-10-14

How other computers in the office reach a RecallHub / Quellex installation, and
why the install behaves the same whether it is opened in the desktop app or in
a browser on another machine.

## What you get

| Tenant    | Address other computers open      | Also works on Windows clients |
|-----------|-----------------------------------|-------------------------------|
| Quellex   | `https://quellex.local`           | `https://quellex`             |
| RecallHub | `https://recallhub.local:11443`   | `https://recallhub:11443`     |

- **TLS everywhere on the network.** Only the Caddy gateway container
  (`gateway-quellex` / `gateway` in `docker-compose.yml`) is published for other
  machines. The frontend nginx, the API, MongoDB and the embedder bind to
  `127.0.0.1` (`BIND_ADDRESS` in `.env`), so nothing crosses the LAN in clear.
- **Login required.** The Quellex backend runs with `REQUIRE_AUTH=true`
  (`backend/core/auth_middleware.py`): every API request needs a valid session
  token or API key, except `/health`, `/api/v1/system/health`,
  `/api/v1/tenant/config`, `/api/v1/auth/login` and `/api/v1/auth/register`
  (which itself obeys `REGISTRATION_MODE`). The frontend mirrors the rule
  (`features.requireAuth`, also switched on by the backend's tenant config): the
  root URL opens the login page, a signed-in user lands on the dashboard, every
  other route redirects guests to the login page, and the "continue as guest"
  options are gone.
- **Same start screen in the desktop app and in the browser.** Both open the
  frontend's root URL, and `frontend/src/components/StartPage.tsx` decides from
  the tenant config what the root shows (`startPage: 'login' | 'landing'`).
- **A name instead of an IP address.** The desktop tray app answers mDNS
  (`quellex.local`) and LLMNR (`quellex`) queries with this machine's LAN
  address (`installer/tray-app/LanNameAdvertiser.cs`). No DNS server, no
  hosts-file edits on the clients.

## Pieces

```
other PC  --https://quellex.local-->  Windows host
   |                                    |-- RecallHubTray.exe  : mDNS/LLMNR answer "quellex.local -> 192.168.x.y"
   |                                    |-- Windows Firewall   : TCP 443/80, UDP 5353/5355 (Configure-LanAccess.ps1)
   +--TCP 443------------------------>  Docker Desktop
                                        |-- rag-gateway-quellex (caddy:2.10-alpine, tls internal, HTTP->HTTPS)
                                        |-- rag-frontend-quellex (nginx, 127.0.0.1:11081)  -> /api proxied to
                                        |-- rag-backend-quellex  (FastAPI, REQUIRE_AUTH=true, 127.0.0.1:11001)
                                        +-- rag-mongodb          (127.0.0.1:11017)
```

| Piece | Where | Notes |
|-------|-------|-------|
| Gateway | `docker-compose.yml` services `gateway-quellex` / `gateway`, `gateway/Caddyfile.template` | Rendered at container start from `GATEWAY_HOSTS` / `GATEWAY_UPSTREAM`; local CA in the `gateway_quellex_data` volume |
| Port binding | `docker-compose.yml`, `BIND_ADDRESS` (default `127.0.0.1`) | `BIND_ADDRESS=0.0.0.0` restores raw-port access for dev boxes reached over Tailscale |
| Login enforcement | `backend/core/auth_middleware.py`, `REQUIRE_AUTH` (`QUELLEX_REQUIRE_AUTH` / `RECALLHUB_REQUIRE_AUTH` in `.env`) | Advertised as `require_auth` by `GET /api/v1/tenant/config` |
| Frontend guard | `frontend/src/components/RequireAuth.tsx`, `StartPage.tsx`, `pages/LoginPage.tsx`, `tenants/configs/*.ts` | `requireAuth` and `startPage` per tenant |
| Name announcer | `installer/tray-app/LanNameAdvertiser.cs`, `network` section of `%LocalAppData%\RecallHub\config.json` | Written by `installer/scripts/Write-TrayConfig.ps1` |
| Firewall | `installer/scripts/Configure-LanAccess.ps1` | Run once as Administrator (INSTALL.ps1 does it when elevated) |
| Certificate | `installer/scripts/Export-LanCertificate.ps1` (tray menu *Network → Create trust package...*) | Builds the trust package (certificate, fingerprint-pinned Windows installer, Apple profile, README) for a share |

## Setting it up

1. Start the stack: `docker compose --profile quellex up -d` (the gateway is part
   of the profile). Check `docker compose --profile quellex ps`: `rag-gateway-quellex`
   must be up and publishing `443` and `80`.
2. Make sure the tray app config announces the name (INSTALL.ps1 does this):

   ```powershell
   installer\scripts\Write-TrayConfig.ps1 -InstallPath C:\Quellex -Tenant quellex
   ```

   This writes `network: { hostname: "quellex", advertise: true, httpsPort: 443, httpPort: 80 }`
   and `app.name: "Quellex"`. Restart the tray app afterwards; its menu now has a
   **Network** entry showing `https://quellex.local`, the announced addresses and
   the responder state. `%LocalAppData%\RecallHub\logs\tray-app.log` logs
   `LAN name advertiser: answering quellex.local (mDNS) and quellex (LLMNR) with ...`.
3. Open the firewall once, as Administrator:

   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\Quellex\installer\scripts\Configure-LanAccess.ps1
   ```

   It allows TCP 443/80 (gateway) and UDP 5353/5355 to `RecallHubTray.exe`
   (announcer) from the local subnet on every firewall profile. `-AllowTailscale`
   adds `100.64.0.0/10`, `-AnyRemote` removes the subnet restriction, `-Remove`
   deletes the rules.
4. Create the **trust package** and put it on a read-only network share. In the
   tray menu choose *Network → Create trust package for other computers...* and
   pick the share, or run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\Quellex\installer\scripts\Export-LanCertificate.ps1 -PackagePath "\\NAS\IT\Quellex-Trust"
   ```

   The package (default location: `Desktop\Quellex-Trust`) contains:

   | File | Purpose |
   |------|---------|
   | `Quellex-LAN-Root-CA.crt` | The gateway's root certificate (DER, public key only) |
   | `Install-Quellex-Trust.cmd` / `.ps1` | Windows one-click installer: verifies the certificate's SHA-1 fingerprint against the value embedded at creation time, refuses a swapped file, imports machine-wide after one UAC prompt (falls back to the current user), and sets the Firefox `ImportEnterpriseRoots` policy |
   | `Uninstall-Quellex-Trust.cmd` | Removes the certificate and the Firefox policy again |
   | `Quellex-LAN-Trust.mobileconfig` | Apple configuration profile: one tap on iPhone/iPad/Mac (iOS additionally needs *Certificate Trust Settings*) |
   | `README.txt` | German and English instructions plus the fingerprint to compare |

   On every Windows PC: double-click `Install-Quellex-Trust.cmd`, confirm the
   administrator prompt, restart the browser. Phones and Macs open the
   `.mobileconfig`. `-TrustOnThisComputer` also imports the certificate on the
   server itself. Without the package the site still works, the browser just
   warns ("not private" / `ERR_CERT_AUTHORITY_INVALID`).

   Why a package and not a public certificate: no public CA issues certificates
   for `.local` names. A publicly trusted certificate would need a real host
   name under `quellex.at` (DNS at Cloudflare) with a Let's Encrypt DNS
   challenge and a Caddy build with the Cloudflare DNS module; that removes all
   client-side steps but requires internet for renewals and a DNS API token on
   the server. It is the recommended upgrade for offices with internet access.
5. On another computer open `https://quellex.local` (Windows: `https://quellex`
   also works via LLMNR). You see the Quellex login page; after signing in you
   are on the dashboard.

## Rule for frontend code under REQUIRE_AUTH

Every request to `/api/...` other than the public allowlist must carry the
session token. The axios client (`frontend/src/api/client.ts`) adds it through
an interceptor, so anything that bypasses axios needs explicit handling:

- raw `fetch` calls: add the `Authorization: Bearer <token>` header (the
  client's auth-header helper);
- `EventSource` (ingestion log/event streams): append `?token=<jwt>`; the
  middleware accepts the query token only for paths ending in `/stream`;
- files opened by the browser itself (`window.open`, `<iframe>`, downloads):
  fetch with the header and hand the browser a blob URL; do not put the token
  in a URL, it would land in the Caddy and nginx access logs;
- status probes must target a public path (`/api/v1/system/health`).

Symptom when this is missed: the page shows "HTTP error 401" although
`GET /api/v1/auth/me` works. The 2026-10-14 sweep covered the chat stream,
document preview/download, the failed-documents, analytics and job-history
pages, the OpenAPI load and the sidebar status probe.

## How name resolution works

- Windows 10/11, macOS, iOS, Android and Linux with Avahi/systemd-resolved
  resolve `*.local` names via multicast DNS. The tray app joins the mDNS group
  (224.0.0.251:5353) next to Windows' own DNS client (shared port) and answers
  `A` queries for `quellex.local` with the IPv4 address of the interface the
  query arrived on. It also answers with an NSEC record saying no AAAA exists,
  so clients do not wait for an IPv6 answer, announces itself on start and on
  network changes, and sends a goodbye (TTL 0) on exit.
- Windows clients additionally resolve single-label names via LLMNR
  (224.0.0.252:5355); the tray app answers `quellex` there. Networks that
  disable LLMNR (common hardening) still have `quellex.local`.
- Queries that do not come from a directly attached subnet are ignored, and the
  same multicast answer is never sent more than once per second.
- The responder lives in the tray app because Docker Desktop containers cannot
  receive LAN multicast. It only runs while the tray app runs, which is the same
  condition as Docker Desktop itself on a Windows workstation.
- Alternatives when mDNS/LLMNR are blocked (VLANs, managed switches, strict
  firewalls): add a DNS record `quellex.local -> <IP>` on the office router or DNS
  server, or an entry in the clients' hosts file. Tailscale peers cannot use
  multicast at all: give them the Tailscale IP or MagicDNS name, add it to
  `QUELLEX_GATEWAY_HOSTS` in `.env` (`https://quellex.local, https://quellex, https://100.x.y.z`)
  and run `Configure-LanAccess.ps1 -AllowTailscale`.

## Configuration reference

`.env` (all optional):

| Variable | Default | Meaning |
|----------|---------|---------|
| `BIND_ADDRESS` | `127.0.0.1` | Host address for the raw ports 11000/11001/11080/11081/11017/11090 |
| `QUELLEX_GATEWAY_HTTPS_PORT` / `QUELLEX_GATEWAY_HTTP_PORT` | `443` / `80` | Host ports of the Quellex gateway. With a non-443 port the URL becomes `https://quellex.local:<port>` and the HTTP redirect does not add the port |
| `QUELLEX_GATEWAY_HOSTS` | `https://quellex.local, https://quellex` | Site addresses Caddy serves (comma separated). Add `https://<IP>` to allow the raw address |
| `RECALLHUB_GATEWAY_*` | `11443` / `11480` / `https://recallhub.local, https://recallhub` | Same for the RecallHub tenant (non-default ports so both tenants can run on one dev box) |
| `QUELLEX_REQUIRE_AUTH` | `true` | Login enforcement for backend-quellex |
| `RECALLHUB_REQUIRE_AUTH` | `false` | RecallHub keeps guest access unless set |

`%LocalAppData%\RecallHub\config.json`, section `network` (written by `Write-TrayConfig.ps1`):

| Key | Meaning |
|-----|---------|
| `hostname` | Single DNS label (`quellex`); `-LanHostname` on `Write-TrayConfig.ps1` / `INSTALL.ps1` |
| `advertise` | `false` disables the responder (`-NoLanAdvertise` / `INSTALL.ps1 -NoLanAccess`) |
| `httpsPort`, `httpPort` | Used for the URL shown in the tray menu; keep in sync with `.env` |

## Verification

```powershell
# Gateway up, only the gateway published for the LAN
docker compose --profile quellex ps
# TLS answer with the quellex.local certificate (from the host itself)
curl.exe -k --resolve quellex.local:443:127.0.0.1 -I https://quellex.local/
# API refuses guests, accepts a session
curl.exe -k --resolve quellex.local:443:127.0.0.1 https://quellex.local/api/v1/ingestion/documents   # 401
# Name answer from the tray app (run on another computer, or here after the firewall rules exist)
Resolve-DnsName quellex.local
ping quellex.local
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| `ERR_NAME_NOT_RESOLVED` / `ping quellex.local` fails, even on the server itself | The name only exists while `RecallHubTray.exe` runs (the responder lives in the tray app). Check the tray icon, `Get-Process RecallHubTray`, and the Application event log for an "Application Hang" / crash entry; start the app again from the Start Menu or `%LocalAppData%\RecallHub\RecallHubTray.exe` |
| `quellex.local` does not resolve on another computer | Tray app running? `tray-app.log` shows "answering quellex.local"? Firewall rules present (`Get-NetFirewallRule -DisplayName "Quellex LAN*"`)? Same subnet / multicast allowed on the Wi-Fi (guest networks often block it)? |
| Resolves to a Tailscale / vEthernet address instead of the LAN address | Fixed in the 2026-10-14 build: only physical adapters with a default gateway are announced (`LanInterface.IsLanUplink`); the log line "LAN name advertiser: answering ... preferred: <addresses>" shows what is handed out |
| Resolves, but the browser times out | TCP 443 rule missing, or the gateway is down (`docker logs rag-gateway-quellex`) |
| Certificate warning (`ERR_CERT_AUTHORITY_INVALID`) | Trust package not installed on that client, or the browser was not restarted after installing it. If the gateway volume was recreated there is a new CA: build a new package and re-install (the old installer refuses the new certificate by fingerprint) |
| `ERR_SSL_UNRECOGNIZED_NAME` / handshake fails for an IP or other name | The name is not in `QUELLEX_GATEWAY_HOSTS` |
| Everything answers 401 | Expected for API calls without a session; sign in. Health and tenant config stay public |
| "Cannot login" / every `/api` call answers 502 while the backend is healthy | The frontend nginx had cached the backend container's IP at startup and the backend was recreated. Fixed on 2026-10-14 in `frontend/nginx.conf` (`resolver 127.0.0.11 valid=10s` + `$backend_upstream` variable, so the upstream is re-resolved at request time); with an older frontend image, `docker restart rag-frontend-quellex` clears it |
| Port 443 or 80 already in use on the host | Set `QUELLEX_GATEWAY_HTTPS_PORT` / `QUELLEX_GATEWAY_HTTP_PORT` in `.env` and re-run `Write-TrayConfig.ps1 -HttpsPort <n> -HttpPort <m>` plus `Configure-LanAccess.ps1` |
| Raw port access from another machine stopped working | Intended: `BIND_ADDRESS=127.0.0.1`. Use the gateway, or set `BIND_ADDRESS=0.0.0.0` on a dev box |
