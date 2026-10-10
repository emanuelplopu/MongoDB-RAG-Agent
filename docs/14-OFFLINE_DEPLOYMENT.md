# RecallHub Offline Deployment Guide

## Overview

RecallHub deploys as a fully self-contained offline package. Backups are **per-tenant** — each backup targets a single tenant (Quellex or RecallHub) and contains only the Docker images, database collections, and configuration for that tenant. A single backup folder contains everything needed to stand up the tenant on a fresh Windows machine with zero internet connectivity:

- Docker Desktop (Hyper-V backend) runtime
- Ollama running natively on the Windows host with pre-loaded AI models
- Tenant-specific Docker images (backend + frontend)
- Shared service images (ingestion worker, MongoDB)
- Tenant-scoped MongoDB database dump
- Complete source code and configuration

The installer is idempotent — safe to re-run after interruptions or restarts. It reads the tenant from `manifest.json` and adjusts paths, ports, and profiles automatically.

### How the pieces fit together (read this first)

- **One install folder per machine.** `docker-compose.yml` pins the Compose project name to `recallhub` and gives every service a fixed image name (`recallhub-backend`, `recallhub-ingestion-worker`, `recallhub-frontend`, `recallhub-frontend-quellex`). Container, network and image names therefore no longer depend on the folder the stack runs from, and a package exported on the dev machine is loaded by exactly the names the stack expects. The flip side: two install folders (e.g. `C:\RecallHub` and `C:\Quellex`) cannot both own the containers. Both tenants live in one folder as Compose profiles; `INSTALL.ps1` refuses to take over a stack owned by another folder unless `-TakeOverStack` is given.
- **The desktop app is configured, not guessed.** `installer\scripts\Write-TrayConfig.ps1` writes `%LocalAppData%\RecallHub\config.json` with the install path, the tenant's Compose profile and the tenant's ports (RecallHub 11080/11000, Quellex 11081/11001), and deploys `Start-Services.ps1` / `Stop-Services.ps1` next to the app. Without this file the tray app falls back to `%LocalAppData%\RecallHub` as the compose directory and to the RecallHub ports — which is exactly why earlier test installs looked "installed" but never came up through the tray app.
- **Install is only complete when the tenant answers.** `INSTALL.ps1` polls `http://localhost:<frontend>/` and `http://localhost:<backend>/api/v1/system/health` for up to 3 minutes before it writes `install_complete: true` into the manifest. A failed restore stops the installer instead of being papered over.
- **Administrator rights are only needed for Phase 1** (Docker Desktop, Ollama, Windows features). With `-SkipPrerequisites` the installer runs as the logged-in user, which keeps the tray app, Ollama models and logs on the right profile.

## Backup Package Structure

Each backup is named `<Tenant>_v<version>_<timestamp>` and contains only tenant-relevant resources:

```
Quellex_v0.8.8_YYYYMMDD_HHMMSS/
├── INSTALL.ps1              (reads tenant from manifest)
├── manifest.json            (includes "tenant": "quellex")
├── apps/
│   ├── DockerDesktopInstaller.exe
│   └── OllamaSetup.exe
├── images/
│   ├── backend-quellex.tar      (tenant backend)
│   ├── frontend-quellex.tar     (tenant frontend)
│   ├── ingestion-worker.tar     (shared)
│   └── mongodb-atlas-local.tar  (shared)
├── models/                      (Ollama AI models)
├── mongodb/dump/
│   ├── admin/                   (shared)
│   ├── recallhub/               (shared auth DB)
│   └── rag_parhelion/           (tenant data)
├── config/
│   ├── .env                     (tenant-specific)
│   ├── docker-compose.yml
│   └── profiles.yaml            (tenant profiles)
├── source/                      (complete source code)
└── documents/
```

A RecallHub backup follows the same structure with `backend.tar`, `frontend.tar`, and RecallHub-specific databases (`rag_db`, `rag_test_law`, etc.).

## Tenant-Resource Mapping

| Resource | Quellex | RecallHub |
|----------|---------|-----------|
| Docker profile | `--profile quellex` | `--profile recallhub` |
| Backend image | `backend-quellex` | `backend` |
| Frontend image | `frontend-quellex` | `frontend` |
| ACTIVE_PROFILE | `parhelion` | `default` |
| MONGODB_DATABASE | `rag_parhelion` | `rag_db` |
| Databases | admin, recallhub, rag_parhelion | admin, recallhub, rag_db, rag_test_law, user_rag_* |
| Install path | `C:\Quellex` | `C:\RecallHub` |
| Ports (backend/frontend) | 11001 / 11081 | 11000 / 11080 |

The `recallhub` database is always included — it holds the shared authentication data used by both tenants.

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Windows 10 64-bit | Windows 11 64-bit |
| RAM | 16 GB | 32 GB |
| Disk space | 100 GB free | 200 GB free |
| Internet | Not required | Not required |
| Permissions | Administrator | Administrator |
| CPU | 4 cores | 8+ cores |

Hyper-V capable hardware required (most modern PCs). Docker Desktop uses the Hyper-V backend; WSL2 is not required.

## Quick Start (New Machine)

```powershell
# 1. Set execution policy for this session
Set-ExecutionPolicy Bypass -Scope Process -Force

# 2. Run the installer — tenant is auto-detected from manifest.json
& "D:\Quellex_v0.8.8_...\INSTALL.ps1"
# Installs to C:\Quellex (or C:\RecallHub based on tenant)

# 3. After restart (if Docker was freshly installed):
& "D:\Quellex_v0.8.8_...\INSTALL.ps1" -SkipPrerequisites
```

The installer reads the tenant from `manifest.json` and configures everything accordingly. After install, the system is accessible at the tenant's ports (e.g., `http://localhost:11081` for Quellex, `http://localhost:11080` for RecallHub).

## What INSTALL.ps1 Does

The installer runs in 3 phases:

### Phase 1: Prerequisites

- Enables Hyper-V and Containers Windows features
- Installs Docker Desktop silently from `apps/DockerDesktopInstaller.exe` (configured for Hyper-V backend)
- Installs Ollama silently from `apps/OllamaSetup.exe` (runs natively on the host)
- Sets `OLLAMA_MODELS` environment variable
- **Triggers restart** if Hyper-V was freshly enabled

### Phase 2: Model Restore

- Copies Ollama model blobs and manifests from `models/` to the configured `OLLAMA_MODELS` path
- Verifies models are accessible via `ollama list`

### Phase 3: Full System Restore

1. Reads tenant from `manifest.json` (e.g., `quellex` or `recallhub`)
2. Copies source code from `source/` to `InstallPath` (default: `C:\<Tenant>`)
3. Writes `.env` and `.env.docker` with tenant-specific configuration
4. Loads tenant Docker images from `images/*.tar` via `docker load`
5. Starts MongoDB container
6. Restores tenant databases from `mongodb/dump/` via `mongorestore`
7. Copies documents to configured path
8. Starts tenant services via `docker compose --profile <tenant> up -d`
9. Runs health checks on tenant endpoints
10. Writes `%LocalAppData%\RecallHub\config.json` + service scripts for the desktop app (`Write-TrayConfig.ps1`)
11. Writes install manifest for future uninstall/upgrade

### Phase 4: Desktop App + verification

- Copies the newest `RecallHubTray.exe` from `installer\tray-app\bin\publish` or `bin\Release\net8.0-windows\win-x64\publish`
- Creates desktop / Start Menu shortcuts, the `recallhub://` protocol handler and the HKCU Run entry
- Polls the tenant frontend and backend; only then marks `install_complete: true` (exit code 2 otherwise)

### Installer Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-InstallPath` | `C:\<Tenant>` | Where source code is deployed (auto-set from tenant) |
| `-TestDataPath` | `C:\Test_Data` | Where document corpus is stored |
| `-SkipPrerequisites` | false | Skip Docker/Ollama install (use after restart) |
| `-Repair` | false | Re-run restore steps without reinstalling prerequisites |
| `-PurgeTestData` | false | In `-Repair` mode also delete `Test_Data` (kept by default: it is user data) |
| `-TakeOverStack` | false | Allow taking over a stack that runs from a different install folder |
| `-NoLaunch` | false | Do not start the desktop app at the end (automation) |
| `-NonInteractive` | false | Never wait for a key press or a restart confirmation (automation) |

The tenant is always read from `manifest.json` — there is no `-Tenant` parameter on `INSTALL.ps1`.

## Creating a Backup (Source Machine)

The `-Tenant` parameter is **mandatory**. Each backup targets a single tenant:

```powershell
# Create a Quellex backup
.\scripts\full-backup.ps1 -OutputPath "E:\Backups" -Tenant quellex

# Create a RecallHub backup
.\scripts\full-backup.ps1 -OutputPath "E:\Backups" -Tenant recallhub -TestDataPath "D:\Test_Data"
```

The backup folder is created at `E:\Backups\Quellex_v0.8.8_YYYYMMDD_HHMMSS\` (or `RecallHub_...`).

### Export Docker images for a specific tenant

```powershell
.\scripts\export-docker-images.ps1 -OutputPath "E:\Backups\Quellex_v0.8.8_...\images" -Tenant quellex
```

### Export Ollama models

```powershell
.\scripts\backup-ollama-models.ps1 -OutputPath "E:\Backups\Quellex_v0.8.8_...\models"
```

### Bundle prerequisite installers

```powershell
Copy-Item "path\to\DockerDesktopInstaller.exe" "E:\...\apps\"
Copy-Item "path\to\OllamaSetup.exe" "E:\...\apps\"
```

### Verify backup completeness

A valid per-tenant backup must contain:
- `INSTALL.ps1` — copied from `scripts/INSTALL.ps1`
- `manifest.json` — valid JSON with `tenant`, `version`, and backup metadata
- `apps/` — both installers present
- `images/` — tenant backend + frontend tars, plus shared images
- `models/blobs/` — non-empty
- `source/` — contains `docker-compose.yml`
- `mongodb/dump/` — tenant databases + shared auth DB
- `config/` — tenant `.env`, `docker-compose.yml`, `profiles.yaml`

## Uninstallation

The uninstaller reads the tenant from the install manifest and removes only that tenant's components:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
# Tenant is auto-detected from the install manifest
& "C:\Quellex\scripts\UNINSTALL.ps1"
# Or for RecallHub:
& "C:\RecallHub\scripts\UNINSTALL.ps1"
```

### Options

```powershell
# Preview what would be removed (no changes made)
& "C:\Quellex\scripts\UNINSTALL.ps1" -DryRun

# Keep Docker Desktop installed
& "C:\Quellex\scripts\UNINSTALL.ps1" -KeepDocker

# Keep Ollama installed
& "C:\Quellex\scripts\UNINSTALL.ps1" -KeepOllama

# Keep data (useful before reinstall)
& "C:\Quellex\scripts\UNINSTALL.ps1" -KeepData

# Skip all prompts
& "C:\Quellex\scripts\UNINSTALL.ps1" -Force
```

The uninstaller removes (in order):
1. Tenant Docker containers, volumes, and networks
2. Tenant Docker images (backend, frontend)
3. Source code directory (`C:\<Tenant>`)
4. Documents directory
5. Ollama models (tenant-specific)
6. Ollama application (if installed by us)
7. Docker Desktop (if installed by us)
8. Hyper-V/Containers features (if enabled by us)
9. Install manifest and metadata

## Configuration

### Default Ports

| Service | Port |
|---------|------|
| RecallHub Backend | 11000 |
| RecallHub Frontend | 11080 |
| Quellex Backend | 11001 |
| Quellex Frontend | 11081 |
| MongoDB Atlas Local | 11017 |

### Path Configuration

Override at install time:

```powershell
# Tenant is read from manifest.json; override install path if needed
& ".\INSTALL.ps1" -InstallPath "D:\MyQuellex" -TestDataPath "D:\MyData"
```

### Docker Resource Allocation

Automatically configured via Docker Desktop settings:
- Memory: 75% of system RAM
- CPU: Total cores minus 1
- Swap: 8 GB

### Environment Variables Set

| Variable | Purpose |
|----------|---------|
| `OLLAMA_MODELS` | Points to Ollama model storage directory |

## Verification checklist (run on every test machine)

```powershell
# 1. Manifest says complete and names the tenant
Get-Content C:\ProgramData\RecallHub\install-manifest.json   # or %LocalAppData%\RecallHub\install-manifest.json
# 2. Exactly one Compose project, owned by the install folder
docker compose ls
# 3. Tenant endpoints answer (Quellex: 11081 / 11001)
Invoke-WebRequest http://localhost:11081/ -UseBasicParsing | Select-Object StatusCode
Invoke-WebRequest http://localhost:11001/api/v1/system/health -UseBasicParsing | Select-Object StatusCode
# 4. Tray app config points at the install folder and the tenant
Get-Content $env:LOCALAPPDATA\RecallHub\config.json
# 5. Tray app start path works end to end
& $env:LOCALAPPDATA\RecallHub\scripts\Start-Services.ps1 -Wait; $LASTEXITCODE   # must be 0
Get-Content $env:LOCALAPPDATA\RecallHub\logs\tray-app.log -Tail 20
```

## Troubleshooting

### Tray app says "Failed to start services"

Read `%LocalAppData%\RecallHub\logs\tray-app.log` and the newest `start-services-*.log` in the same folder. The start script exits 1 (Docker not running), 2 (compose file not at `docker.installPath`), 3 (`docker compose up` failed, output is in the log) or 4 (endpoints not healthy). If `config.json` is missing or points at the wrong folder, re-run:

```powershell
& C:\<Tenant>\installer\scripts\Write-TrayConfig.ps1 -InstallPath C:\<Tenant> -Tenant quellex
```

### "A ... stack already runs from: <other folder>"

The containers belong to another install folder. Either install into that folder (`-InstallPath`) or pass `-TakeOverStack` to move the stack; data folders of the old install are left untouched.

### Package says "PACKAGE INCOMPLETE"

`full-backup.ps1` now verifies the package (source tree, config/.env, MongoDB dump, images, models) and refuses to call it done otherwise. A folder with only `config/` in it is a backup that died early — never ship it.

### LLM provider after restore

The restored `config/.env` decides which LLM and embedding provider the tenant uses. The default dev configuration uses OpenAI (`gpt-5.2`, `text-embedding-3-large`, 3072 dimensions), so the bundled Ollama models are only used if `.env` is switched to Ollama — and the embedding model cannot be switched without re-ingesting, because the stored vectors have 3072 dimensions. Decide this on the source machine before creating the package.

### "Docker not ready after restart"

Docker Desktop takes 30–60 seconds to fully initialize after login. Re-run with `-SkipPrerequisites`:

```powershell
& ".\INSTALL.ps1" -SkipPrerequisites
```

### "MongoDB health check timeout"

Check container logs:

```powershell
cd C:\RecallHub
docker compose logs mongodb
```

Common cause: insufficient RAM allocated to Docker Desktop.

### "Hyper-V not enabled"

A restart is required after first-time Hyper-V enablement. The installer will prompt for this automatically.

### "Script execution disabled"

Always start with:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
```

This applies only to the current PowerShell session and requires no permanent system changes.

### "Ollama models not found"

Verify the `OLLAMA_MODELS` environment variable points to the `models` directory (not the Ollama root):

```powershell
echo $env:OLLAMA_MODELS
# Should be something like: C:\Users\<user>\.ollama\models
```

### "Docker image load fails"

Ensure the tar files weren't corrupted during transfer. Re-export from source machine:

```powershell
.\scripts\export-docker-images.ps1 -OutputPath "path\to\images"
```

### "Antivirus blocking scripts"

Some antivirus solutions flag PowerShell automation. Temporarily disable real-time protection or add an exclusion for the backup folder path.

## Script Reference

| Script | Purpose |
|--------|--------|
| `scripts/INSTALL.ps1` | Single entry point — reads tenant from backup manifest |
| `scripts/UNINSTALL.ps1` | Tenant-aware clean removal |
| `scripts/install-prerequisites.ps1` | Docker Desktop + Ollama silent install |
| `scripts/restore-ollama-models.ps1` | Restore model blobs and manifests |
| `scripts/full-restore.ps1` | Tenant-aware restore (auto-detects from manifest) |
| `scripts/full-backup.ps1` | Create per-tenant backup (`-Tenant quellex\|recallhub`) |
| `scripts/export-docker-images.ps1` | Export tenant-specific Docker images (`-Tenant`) |
| `scripts/backup-ollama-models.ps1` | Export Ollama model files |

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v0.8.8 | 2026-05-13 | Initial offline deployment package |
| v0.8.8 | 2026-10-10 | Pinned Compose project/image names, tenant-aware tray config, start scripts no longer abort on Docker stderr, install verified by health check, package completeness check |
