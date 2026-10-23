# Desktop Application (Windows)

## Overview

RecallHub runs as a native Windows desktop application using a system tray app with WebView2 for the UI. The tray app manages Docker Desktop services, provides start/stop control, and renders the web frontend in an embedded browser.

All data stays local — no cloud, no tracking, no external dependencies.

## Architecture

### Components

1. **RecallHubTray.exe** — .NET 10 WinForms system tray application (self-contained, single-file publish)
2. **WebView2** — Embedded Chromium browser (Microsoft.Web.WebView2 v1.0.2792.45) rendering the React frontend
3. **ServiceManager** — Docker Compose orchestration (start/stop/health/restart)
4. **SplashScreen** — Startup progress UI shown while services initialize
5. **Branding** (`Branding.cs`) — window title, tray text, splash title, dialogs and icon from `config.json` (`app.name` / `app.tenant`): a Quellex install shows "Quellex" and `quellex.ico` everywhere, RecallHub stays the default
6. **LanNameAdvertiser** (`LanNameAdvertiser.cs`) — mDNS/LLMNR responder that makes `https://quellex.local` resolvable on the office network (see `docs/21-LAN_ACCESS.md`)
7. **Start-Services.ps1** / **Stop-Services.ps1** — PowerShell service scripts (fallback and CLI use)
8. **RecallHub-Docker.psm1** — Docker engine readiness shared by the scripts: wait for a booting Docker Desktop, start it quietly when it is not running, never relaunch it (tests in `installer/tests/`)

### Runtime Flow

1. User launches RecallHubTray.exe (or auto-starts via Task Scheduler)
2. Tray app loads `config.json` from app directory or `%LocalAppData%\RecallHub\`, applies the tenant branding and, when `network.advertise` is true, starts answering `quellex.local` / `quellex` name queries
3. Quick-checks if services are already running (GET `http://localhost:11080/`)
4. If already running: opens WebView2 main window immediately
5. If not running: shows SplashScreen and begins startup sequence:
   a. ServiceManager checks the Docker engine (engine named pipe present, then `docker info`)
   b. If the engine does not answer but a Docker Desktop process already exists (it autostarts at login and needs 20–60 s): waits for it, polling every 3s up to `startupTimeout`. It never relaunches the exe — see "Why Docker Desktop is never relaunched" below
   c. If no Docker Desktop process exists and `autoStart` is true: starts it quietly (`docker desktop start --detach`, exe minimized as fallback) and waits as in b
   d. Runs `docker compose -f <composePath> up -d`
   e. Polls frontend endpoint every N seconds until healthy or timeout
   f. Closes splash, opens WebView2 main window
6. Status timer (every 5s) continuously monitors service health
7. Tray icon reflects current state: Running (green), Stopped (red), Unknown (orange)

### Tray Menu

| Menu Item | Action |
|-----------|--------|
| Status: Running/Stopped | Display-only status indicator |
| **Open RecallHub** / **Open Quellex** | Show/focus WebView2 window (label follows `app.name`) |
| Open Admin Panel | Navigate WebView2 to `/admin` |
| Network: https://quellex.local | Submenu: open the network address in the browser, copy it, *Create trust package for other computers...* (folder picker, then runs `Export-LanCertificate.ps1 -PackagePath` in a PowerShell window), and the announcer state (which addresses `quellex.local` resolves to). Hidden when `config.json` has no `network.hostname` |
| Show/Hide Window | Toggle main window visibility |
| Start Services | Start Docker Compose stack |
| Stop Services | Stop Docker Compose stack |
| Restart Services | Stop then start with 2s delay |
| View Logs | Open logs folder in Explorer |
| Check for Updates | Scan for `.rhu` update files |
| About RecallHub / About Quellex | Version, tagline and network address |
| Exit | Optional stop services, then quit (also sends the mDNS goodbye) |

The main window's title bar, the tray tooltip ("Quellex - Running") and every
dialog use the same name, so a Quellex install never shows "RecallHub" to the
user. Internal paths (`%LocalAppData%\RecallHub`, the log file, the
`recallhub://` protocol handler, the single-instance mutex) keep their names.

## Configuration (installer/config.json)

The config file is the single source of truth for all build and runtime parameters.

### App Section

| Key | Default | Description |
|-----|---------|-------------|
| `name` | `RecallHub` / `Quellex` | Display name (window title, tray, splash, dialogs). Written per tenant by `Write-TrayConfig.ps1`; an older config that still says `RecallHub` for the quellex tenant is shown as "Quellex" anyway (`Branding.ResolveAppName`) |
| `tenant` | _(tenant)_ | `recallhub` or `quellex`; selects `quellex.ico` and the splash tagline |
| `version` | from `pyproject.toml` | Shown in About |

### Network Section

| Key | Default | Description |
|-----|---------|-------------|
| `hostname` | _(tenant id)_ | Single label announced via mDNS (`<hostname>.local`) and LLMNR (`<hostname>`) |
| `advertise` | `true` (installs), `false` (missing section) | Run the responder. `Write-TrayConfig.ps1 -NoLanAdvertise` switches it off |
| `httpsPort` / `httpPort` | `443` / `80` (quellex), `11443` / `11480` (recallhub) | Gateway ports; only used to build the URL shown in the menu |

### Docker Section

| Key | Default | Description |
|-----|---------|-------------|
| `composeFile` | `docker-compose.yml` | Path to compose file (relative to installPath) |
| `composeProfile` | _(tenant)_ | Compose profile to enable: `recallhub` or `quellex`. Set by `Write-TrayConfig.ps1`. The scripts and the tray app merge it with `COMPOSE_PROFILES` from `<installPath>\.env` (e.g. `quellex,embedder-gpu` for the local embedding server): an explicit `--profile` flag would otherwise replace the `.env` list and the stack would start without the embedder. `Start-Services.ps1 -DryRun` / `Stop-Services.ps1 -DryRun` print the resolved command |
| `installPath` | `C:\RecallHub` | Installation root directory. `auto` (the hardened installer's `config.json`) means the parent folder of the `scripts` directory the service scripts run from. |
| `startupTimeout` | `120` | Seconds to wait for services to become healthy |
| `healthCheckInterval` | `5` | Polling interval in seconds |
| `autoStart` | `true` | Start Docker Desktop when no Docker Desktop process exists at all. A running or booting instance is always waited for, never relaunched |
| `desktopPath` | `C:\Program Files\Docker\Docker\Docker Desktop.exe` | Docker Desktop executable |

### Ports Section

| Key | Default | Description |
|-----|---------|-------------|
| `backend` | `11000` | Backend API port |
| `frontend` | `11080` | Frontend web UI port |
| `mongodb` | `27017` | MongoDB port |
| `ollama` | `11434` | Ollama LLM port |
| `backendQuellex` | `11001` | Quellex backend (multi-tenant) |
| `frontendQuellex` | `11081` | Quellex frontend (multi-tenant) |

### Config Resolution Order

The tray app searches for `config.json` in this order:
1. `<AppContext.BaseDirectory>/config.json` (next to the exe)
2. `%LocalAppData%\RecallHub\config.json`

`INSTALL.ps1` / `full-restore.ps1` write the second file through `installer\scripts\Write-TrayConfig.ps1 -InstallPath <dir> -Tenant <recallhub|quellex>`. It carries `docker.installPath`, `docker.composeProfile` and the tenant ports, so the app no longer assumes 11080/11000 or `%LocalAppData%\RecallHub` as compose directory. Re-run it whenever the install folder or tenant changes.

## ServiceManager (C#)

Located at `installer/tray-app/ServiceManager.cs`. Handles all Docker interaction.

### Key Methods

| Method | Description |
|--------|-------------|
| `EnsureDockerRunningAsync()` | Checks the engine; while a Docker Desktop process exists it only waits (3s polls, up to `startupTimeout`); only when no process exists and `autoStart` is true does it start Docker Desktop quietly (`docker desktop start --detach`, exe minimized as fallback) and then wait. Never relaunches a running instance |
| `IsDockerDesktopProcessRunning()` | True when a `Docker Desktop` process exists (running or still booting) |
| `StartServicesAsync()` | Ensures Docker running, runs `docker compose up -d`, polls health until frontend responds or timeout |
| `StopServicesAsync()` | Runs `docker compose down`; falls back to Stop-Services.ps1 if compose file missing |
| `RestartServicesAsync()` | Calls Stop then Start with a 2s gap |
| `CheckServicesRunningAsync()` | GET `http://localhost:{frontendPort}/` — returns true on 200 |
| `CheckBackendHealthyAsync()` | GET `http://localhost:{backendPort}/api/v1/system/health` — returns true on 200 (`/api/v1/status` does not exist) |
| `IsDockerRunningAsync()` | Fast-fails when no Docker engine named pipe exists, otherwise runs `docker info` with a 15s timeout (the earlier 5s made a busy but healthy engine look stopped) |
| `GetServiceStatusAsync()` | Returns composite `ServiceStatus` (DockerRunning, IsRunning, BackendHealthy) |

### Start/Stop strategy

ServiceManager prefers the PowerShell scripts (`%LocalAppData%\RecallHub\scripts`, then `<installPath>\installer\scripts`): they know the install path, the Compose profile and the health endpoints and leave a log. Only when no script is found does it run `docker compose -f <file> [-f override] [--profile <tenant>] up -d --remove-orphans` itself. Decisions are logged to `%LocalAppData%\RecallHub\logs\tray-app.log`.

### Why Docker Desktop is never relaunched

Docker Desktop is a single-instance application. Launching `Docker Desktop.exe` while it is already running — or still booting after sign-in — starts nothing; Windows hands the launch to the running instance, which responds by opening its Dashboard window. The tray app and Docker Desktop both autostart at sign-in, and the engine does not answer `docker info` for the first 20–60 s, so builds before October 2026 (which treated "no answer" as "not installed or not running" and launched the exe) put the Docker Dashboard in front of every user at every login, and again on every tray Start/Restart while the engine was slow. The `start-services-*.log` files of such installs show "Starting Docker Desktop..." followed by the engine answering a few seconds later — too fast for a cold start, so Docker was already up each time.

Both the tray app and `Start-Services.ps1` now check for a `Docker Desktop` process first and only wait while one exists. A launch happens only when there is no process at all, and then through the Docker Desktop CLI (`docker desktop start --detach`, bundled since Docker Desktop 4.37) or, without the CLI, the exe started minimized. Installs made before this change need the new `RecallHubTray.exe` and the scripts in `%LocalAppData%\RecallHub\scripts` (re-run `INSTALL.ps1 -SkipPrerequisites` from a current package). In addition, `install-prerequisites.ps1` writes Docker Desktop's "do not open the Dashboard at startup" and "start at sign-in" settings into `settings-store.json` (Docker Desktop 4.35+), not only into the legacy `settings.json` that current builds ignore.

### Health Polling

- Checks `http://localhost:{frontendPort}/` every `healthCheckInterval` seconds
- Timeout after `startupTimeout` seconds (default 120)
- On timeout: shows balloon notification to user with error guidance

## PowerShell Scripts

### Start-Services.ps1

Located at `installer/scripts/Start-Services.ps1`.

**Parameters:**
- `-Detached` (default: true) — run in background
- `-Wait` — block until services are healthy
- `-Timeout` (default: 180) — max seconds to wait
- `-DryRun` — print the resolved `docker compose` command (profiles from `config.json` plus `.env` `COMPOSE_PROFILES`) and exit; the Docker step is skipped, so nothing is started
- `-DockerOnly` — diagnostic: only run the Docker step (wait for or quietly start Docker Desktop), exit 0/1, do not touch the stack

**Steps:**
1. Imports `RecallHub-Logging.psm1` (structured logging) and `RecallHub-Docker.psm1` (engine readiness)
2. Loads `config.json` from parent directory
3. `Confirm-DockerEngine`: returns at once when `docker info` answers; waits (3s polls, up to `docker.startupTimeout`) while a Docker Desktop process exists; starts Docker Desktop quietly (`docker desktop start --detach`, exe minimized as fallback) only when no process exists. The log shows which of these happened (`ready`, `waited`, `started-cli`, `started-exe`)
4. With `-DockerOnly`: exits here
5. Validates compose file exists at `<installPath>/<composeFile>`
6. Runs `docker compose up -d` from `installPath` with the profiles from `config.json` plus `.env` `COMPOSE_PROFILES` (`-DryRun` prints this command and exits instead)
7. If `-Wait`: polls the frontend (`ports.frontend`) and backend (`ports.backend`, `/api/v1/system/health`) until both return 200

The script runs native Docker commands with `$ErrorActionPreference = Continue` and judges them by exit code: Docker prints progress to stderr, and Windows PowerShell 5.1 would otherwise turn the first stderr line into a terminating error (every pre-October-2026 run died silently right after "Running docker compose up").

**Error codes:**
- `DOCKER-001` — Docker engine not reachable; the diagnostic's context says what was tried (`timeout` = Docker Desktop runs but its engine never answered, usually a dialog in Docker Desktop; `not-installed` = neither the Docker Desktop CLI nor `docker.desktopPath` exists)
- `COMPOSE-001` — docker-compose.yml not found
- `COMPOSE-002` — `docker compose up` failed
- `HEALTH-001` — endpoints not healthy within the timeout

Exit codes: 0 ok, 1 Docker unavailable, 2 compose file missing, 3 compose up failed, 4 health timeout.

### Stop-Services.ps1

Located at `installer/scripts/Stop-Services.ps1`.

**Parameters:**
- `-Force` — use shorter 10s shutdown timeout
- `-Timeout` (default: 60) — max seconds for container stop

**Steps:**
1. Imports `RecallHub-Logging.psm1`
2. Loads `config.json`
3. Checks Docker is running (exits gracefully if not)
4. Runs `docker compose down --timeout <N>` from `installPath`
5. Does NOT terminate Docker Desktop itself

### Tests

`installer/tests/RecallHub-Docker.Tests.ps1` covers the Docker readiness module with mocked probes (engine already up, Docker Desktop booting, CLI start, exe fallback, timeout, `-NoLaunch`). Windows ships Pester 3.4 only, so install a current Pester once per user, then run from the repo root:

```powershell
Install-Module Pester -Scope CurrentUser -Force -SkipPublisherCheck
Invoke-Pester -Path installer/tests -Output Detailed
```

## Installation Paths

| Purpose | Path |
|---------|------|
| Application root | `C:\RecallHub` (configurable via `docker.installPath`) |
| Tray executable | `<installPath>\RecallHubTray.exe` |
| Icons | `%LocalAppData%\RecallHub\assets\icon.ico` and `quellex.ico` (INSTALL.ps1 copies both; `Branding.LoadIcon` also looks next to the exe) |
| Scripts | `%LocalAppData%\RecallHub\scripts\` (deployed copy of `<installPath>\installer\scripts\`) |
| Config | `<installPath>\config.json` or `%LocalAppData%\RecallHub\config.json` |
| Logs | `%LocalAppData%\RecallHub\logs\` |
| Updates | `%LocalAppData%\RecallHub\updates\` (drop `.rhu` files here) |

## Building

```powershell
cd installer/tray-app
dotnet build -c Release
```

Output: `bin/Release/net10.0-windows/RecallHubTray.exe`

### Publish (self-contained single-file)

```powershell
cd installer/tray-app
dotnet publish -c Release
```

Output: `bin/Release/net10.0-windows/win-x64/publish/RecallHubTray.exe`

The csproj sets `IncludeNativeLibrariesForSelfExtract=true`: `WebView2Loader.dll` and the WinForms native DLLs are embedded in the single exe. Without it they are written next to the exe, and because `INSTALL.ps1` deploys only `RecallHubTray.exe`, the app fails with "Browser Initialization Error" (`DllNotFoundException`). Check `%LocalAppData%\RecallHub\logs\tray-app.log` for the logged exception.

The tenant icon `installer/assets/quellex.ico` is generated from the frontend's
`quellex-q-icon.png` with `installer\scripts\New-IcoFromPng.ps1` (multi-size,
PNG-compressed ICO, no external tools); regenerate it when the logo changes.

The name announcer has no third-party dependency: `LanNameAdvertiser.cs` parses
and builds the DNS packets itself (A answers plus an NSEC "no AAAA" record for
mDNS, unicast answers for LLMNR). It binds UDP 5353/5355 with address reuse next
to Windows' own DNS client; the firewall rules for the process come from
`installer\scripts\Configure-LanAccess.ps1`.

### Project Configuration (RecallHubTray.csproj)

- Target: `net10.0-windows` (WinForms)
- Output: `WinExe` (no console window)
- Self-contained: `true` (bundles the .NET 10 runtime; nothing to install on the target machine)
- RuntimeIdentifier: `win-x64`
- PublishSingleFile: `true`
- Namespace: `RecallHub.TrayApp`

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| Microsoft.Web.WebView2 | 1.0.4258.31 | Embedded Chromium browser |
| System.ServiceProcess.ServiceController | 10.0.13 | Host agent service status in the tray menu |
| System.Text.Json | .NET 10 shared framework (no package reference) | Config file parsing |

### Requirements

- .NET 10 SDK (build time)
- WebView2 Runtime (included with Windows 11; installable on Windows 10)
- Docker Desktop (runtime)

## Auto-Start

- Backup/restore installs (`INSTALL.ps1`): HKCU `Run` entry "RecallHub" at user logon
- Hardened installer: **Windows Task Scheduler** task "RecallHub" at user logon
- Strategy chosen over Windows Service for:
  - Better user-session integration (tray icon, UI access)
  - No elevated permissions required for normal operation
  - Simpler installation/uninstallation

## Exit Behavior

When the user clicks "Exit" in the tray menu, a dialog offers three choices:

1. **Yes** — Stop services (docker compose down) then exit
2. **No** — Exit tray app but keep services running in background
3. **Cancel** — Do not exit
