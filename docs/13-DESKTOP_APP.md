# Desktop Application (Windows)

## Overview

RecallHub runs as a native Windows desktop application using a system tray app with WebView2 for the UI. The tray app manages Docker Desktop services, provides start/stop control, and renders the web frontend in an embedded browser.

All data stays local — no cloud, no tracking, no external dependencies.

## Architecture

### Components

1. **RecallHubTray.exe** — .NET 8 WinForms system tray application (self-contained, single-file publish)
2. **WebView2** — Embedded Chromium browser (Microsoft.Web.WebView2 v1.0.2792.45) rendering the React frontend
3. **ServiceManager** — Docker Compose orchestration (start/stop/health/restart)
4. **SplashScreen** — Startup progress UI shown while services initialize
5. **Start-Services.ps1** / **Stop-Services.ps1** — PowerShell service scripts (fallback and CLI use)

### Runtime Flow

1. User launches RecallHubTray.exe (or auto-starts via Task Scheduler)
2. Tray app loads `config.json` from app directory or `%LocalAppData%\RecallHub\`
3. Quick-checks if services are already running (GET `http://localhost:11080/`)
4. If already running: opens WebView2 main window immediately
5. If not running: shows SplashScreen and begins startup sequence:
   a. ServiceManager checks Docker daemon via `docker info`
   b. If Docker not responding: auto-starts Docker Desktop, polls every 2s (up to 60s)
   c. Runs `docker compose -f <composePath> up -d`
   d. Polls frontend endpoint every N seconds until healthy or timeout
   e. Closes splash, opens WebView2 main window
6. Status timer (every 5s) continuously monitors service health
7. Tray icon reflects current state: Running (green), Stopped (red), Unknown (orange)

### Tray Menu

| Menu Item | Action |
|-----------|--------|
| Status: Running/Stopped | Display-only status indicator |
| **Open RecallHub** | Show/focus WebView2 window |
| Open Admin Panel | Navigate WebView2 to `/admin` |
| Show/Hide Window | Toggle main window visibility |
| Start Services | Start Docker Compose stack |
| Stop Services | Stop Docker Compose stack |
| Restart Services | Stop then start with 2s delay |
| View Logs | Open logs folder in Explorer |
| Check for Updates | Scan for `.rhu` update files |
| About RecallHub | Version and description dialog |
| Exit | Optional stop services, then quit |

## Configuration (installer/config.json)

The config file is the single source of truth for all build and runtime parameters.

### Docker Section

| Key | Default | Description |
|-----|---------|-------------|
| `composeFile` | `docker-compose.yml` | Path to compose file (relative to installPath) |
| `installPath` | `C:\RecallHub` | Installation root directory |
| `startupTimeout` | `120` | Seconds to wait for services to become healthy |
| `healthCheckInterval` | `5` | Polling interval in seconds |
| `autoStart` | `true` | Auto-start Docker Desktop if not running |
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

## ServiceManager (C#)

Located at `installer/tray-app/ServiceManager.cs`. Handles all Docker interaction.

### Key Methods

| Method | Description |
|--------|-------------|
| `EnsureDockerRunningAsync()` | Checks `docker info`; if unresponsive and `autoStart` is true, launches Docker Desktop and polls every 2s for up to 60s |
| `StartServicesAsync()` | Ensures Docker running, runs `docker compose up -d`, polls health until frontend responds or timeout |
| `StopServicesAsync()` | Runs `docker compose down`; falls back to Stop-Services.ps1 if compose file missing |
| `RestartServicesAsync()` | Calls Stop then Start with a 2s gap |
| `CheckServicesRunningAsync()` | GET `http://localhost:{frontendPort}/` — returns true on 200 |
| `CheckBackendHealthyAsync()` | GET `http://localhost:{backendPort}/api/v1/status` — returns true on 200 |
| `IsDockerRunningAsync()` | Runs `docker info` with 5s timeout |
| `GetServiceStatusAsync()` | Returns composite `ServiceStatus` (DockerRunning, IsRunning, BackendHealthy) |

### Fallback Behavior

If the `docker-compose.yml` file is not found at the expected path, ServiceManager falls back to running the PowerShell scripts:
- Start: `Start-Services.ps1 -Wait`
- Stop: `Stop-Services.ps1 -Force`

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

**Steps:**
1. Imports `RecallHub-Logging.psm1` for structured logging
2. Loads `config.json` from parent directory
3. Checks Docker daemon via `docker info`
4. If not running: starts Docker Desktop, polls every 2s for up to 60s
5. Validates compose file exists at `<installPath>/<composeFile>`
6. Runs `docker compose up -d` from `installPath`
7. If `-Wait`: polls both frontend (port 11080) and backend (`/api/v1/status`) until both return 200

**Error codes:**
- `DOCKER-001` — Docker daemon not available
- `COMPOSE-001` — docker-compose.yml not found
- `COMPOSE-002` — `docker compose up` failed

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

## Installation Paths

| Purpose | Path |
|---------|------|
| Application root | `C:\RecallHub` (configurable via `docker.installPath`) |
| Tray executable | `<installPath>\RecallHubTray.exe` |
| Scripts | `<installPath>\installer\scripts\` |
| Config | `<installPath>\config.json` or `%LocalAppData%\RecallHub\config.json` |
| Logs | `%LocalAppData%\RecallHub\logs\` |
| Updates | `%LocalAppData%\RecallHub\updates\` (drop `.rhu` files here) |

## Building

```powershell
cd installer/tray-app
dotnet build -c Release
```

Output: `bin/Release/net8.0-windows/RecallHubTray.exe`

### Publish (self-contained single-file)

```powershell
cd installer/tray-app
dotnet publish -c Release
```

Output: `bin/Release/net8.0-windows/win-x64/publish/RecallHubTray.exe`

### Project Configuration (RecallHubTray.csproj)

- Target: `net8.0-windows` (WinForms)
- Output: `WinExe` (no console window)
- Self-contained: `true` (includes .NET runtime)
- RuntimeIdentifier: `win-x64`
- PublishSingleFile: `true`
- Namespace: `RecallHub.TrayApp`

### Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| Microsoft.Web.WebView2 | 1.0.2792.45 | Embedded Chromium browser |
| System.Text.Json | 8.0.0 | Config file parsing |

### Requirements

- .NET 8 SDK (build time)
- WebView2 Runtime (included with Windows 11; installable on Windows 10)
- Docker Desktop (runtime)

## Auto-Start

- Configured via **Windows Task Scheduler** (not a Windows Service)
- Task name: "RecallHub" — triggers at user logon
- Strategy chosen over Windows Service for:
  - Better user-session integration (tray icon, UI access)
  - No elevated permissions required for normal operation
  - Simpler installation/uninstallation

## Exit Behavior

When the user clicks "Exit" in the tray menu, a dialog offers three choices:

1. **Yes** — Stop services (docker compose down) then exit
2. **No** — Exit tray app but keep services running in background
3. **Cancel** — Do not exit
