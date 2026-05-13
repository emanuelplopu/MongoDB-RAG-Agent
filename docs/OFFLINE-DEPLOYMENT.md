# RecallHub Offline Deployment Guide

## Overview

RecallHub deploys as a fully self-contained offline package. A single backup folder contains everything needed to stand up the complete system on a fresh Windows machine with zero internet connectivity:

- Docker Desktop + WSL2 runtime
- Ollama with pre-loaded AI models
- Pre-built Docker images for all services
- Full MongoDB database dump
- Document corpus (Test_Data)
- Complete source code and configuration

The installer is idempotent — safe to re-run after interruptions or restarts.

## Backup Package Structure

```
RecallHub_v0.8.8_YYYYMMDD_HHMMSS/
├── INSTALL.ps1          — Single entry point (run this)
├── apps/                — Bundled installers
│   ├── DockerDesktopInstaller.exe
│   └── OllamaSetup.exe
├── images/              — Pre-built Docker images
│   ├── backend.tar
│   ├── backend-quellex.tar
│   ├── ingestion-worker.tar
│   ├── frontend.tar
│   ├── frontend-quellex.tar
│   └── mongodb-atlas-local.tar
├── models/              — Ollama AI models
│   ├── blobs/
│   └── manifests/
├── mongodb/dump/        — Full database backup
├── Test_Data/           — Document corpus
├── source/              — Complete source code
├── config/              — Environment configs
└── manifest.json        — Backup metadata
```

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| OS | Windows 10 64-bit | Windows 11 64-bit |
| RAM | 16 GB | 32 GB |
| Disk space | 100 GB free | 200 GB free |
| Internet | Not required | Not required |
| Permissions | Administrator | Administrator |
| CPU | 4 cores | 8+ cores |

WSL2 and Hyper-V capable hardware required (most modern PCs).

## Quick Start (New Machine)

```powershell
# 1. Set execution policy for this session
Set-ExecutionPolicy Bypass -Scope Process -Force

# 2. Run the installer (auto-elevates to Admin)
& "D:\path\to\backup\INSTALL.ps1"

# 3. After restart (if Docker was freshly installed):
& "D:\path\to\backup\INSTALL.ps1" -SkipPrerequisites
```

That's it. The system will be accessible at `http://localhost:11080` (RecallHub) and `http://localhost:11081` (Quellex).

## What INSTALL.ps1 Does

The installer runs in 3 phases:

### Phase 1: Prerequisites

- Enables WSL2 and VirtualMachinePlatform Windows features
- Creates `.wslconfig` with optimized resource limits (75% RAM, n-1 CPU cores)
- Installs Docker Desktop silently from `apps/DockerDesktopInstaller.exe`
- Installs Ollama silently from `apps/OllamaSetup.exe`
- Sets `OLLAMA_MODELS` environment variable
- **Triggers restart** if WSL2 was freshly enabled

### Phase 2: Model Restore

- Copies Ollama model blobs and manifests from `models/` to the configured `OLLAMA_MODELS` path
- Verifies models are accessible via `ollama list`

### Phase 3: Full System Restore

1. Copies source code from `source/` to `InstallPath` (default: `C:\RecallHub`)
2. Writes `.env` and `.env.docker` configuration files
3. Loads Docker images from `images/*.tar` via `docker load`
4. Starts MongoDB container
5. Restores database from `mongodb/dump/` via `mongorestore`
6. Copies Test_Data to configured path
7. Starts all services via `docker compose up -d`
8. Runs health checks on all endpoints
9. Writes install manifest for future uninstall/upgrade

### Installer Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-InstallPath` | `C:\RecallHub` | Where source code is deployed |
| `-TestDataPath` | `C:\Test_Data` | Where document corpus is stored |
| `-SkipPrerequisites` | false | Skip Docker/Ollama install (use after restart) |
| `-Force` | false | Skip confirmation prompts |

## Creating a Backup (Source Machine)

Run these from the project root on a machine with a working RecallHub installation:

```powershell
# Full backup (source, database, config — without Docker images)
.\scripts\full-backup.ps1 -OutputPath "E:\RecallHub_Backups" -TestDataPath "D:\Test_Data"
```

The backup folder is created at `E:\RecallHub_Backups\RecallHub_v0.8.8_YYYYMMDD_HHMMSS\`.

### Export Docker images (large, ~35 GB total)

```powershell
.\scripts\export-docker-images.ps1 -OutputPath "E:\RecallHub_Backups\RecallHub_v0.8.8_...\images"
```

### Export Ollama models

```powershell
.\scripts\backup-ollama-models.ps1 -OutputPath "E:\RecallHub_Backups\RecallHub_v0.8.8_...\models"
```

### Bundle prerequisite installers

```powershell
Copy-Item "path\to\DockerDesktopInstaller.exe" "E:\...\apps\"
Copy-Item "path\to\OllamaSetup.exe" "E:\...\apps\"
```

### Verify backup completeness

A valid backup must contain:
- `INSTALL.ps1` — copied from `scripts/INSTALL.ps1`
- `apps/` — both installers present
- `images/` — all 6 tar files
- `models/blobs/` — non-empty
- `source/` — contains `docker-compose.yml`
- `mongodb/dump/` — non-empty
- `manifest.json` — valid JSON with version info

## Uninstallation

Clean removal of all RecallHub components:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "C:\RecallHub\scripts\UNINSTALL.ps1"
```

### Options

```powershell
# Preview what would be removed (no changes made)
& "C:\RecallHub\scripts\UNINSTALL.ps1" -DryRun

# Keep Docker Desktop installed
& "C:\RecallHub\scripts\UNINSTALL.ps1" -KeepDocker

# Keep Ollama installed
& "C:\RecallHub\scripts\UNINSTALL.ps1" -KeepOllama

# Keep Test_Data and DB data (useful before reinstall)
& "C:\RecallHub\scripts\UNINSTALL.ps1" -KeepData

# Skip all prompts
& "C:\RecallHub\scripts\UNINSTALL.ps1" -Force
```

The uninstaller removes (in order):
1. Docker containers, volumes, and networks
2. All RecallHub Docker images (built and loaded)
3. Source code directory
4. Test_Data directory
5. Ollama models (RecallHub-specific)
6. Ollama application (if installed by us)
7. Docker Desktop (if installed by us)
8. WSL configuration (if created by us)
9. WSL2 Windows features (if enabled by us)
10. Install manifest and metadata

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
& ".\INSTALL.ps1" -InstallPath "D:\MyRecallHub" -TestDataPath "D:\MyData"
```

### Docker Resource Allocation

Automatically configured in `.wslconfig`:
- Memory: 75% of system RAM
- CPU: Total cores minus 1
- Swap: 8 GB

### Environment Variables Set

| Variable | Purpose |
|----------|---------|
| `OLLAMA_MODELS` | Points to Ollama model storage directory |

## Troubleshooting

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

Common cause: insufficient RAM allocated to WSL2.

### "WSL2 not enabled"

A restart is required after first-time WSL2 enablement. The installer will prompt for this automatically.

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
|--------|---------|
| `scripts/INSTALL.ps1` | Single entry point for offline deployment |
| `scripts/UNINSTALL.ps1` | Clean removal of all components |
| `scripts/install-prerequisites.ps1` | Docker Desktop + Ollama silent install |
| `scripts/restore-ollama-models.ps1` | Restore model blobs and manifests |
| `scripts/full-restore.ps1` | Source, DB, data, Docker images, service start |
| `scripts/full-backup.ps1` | Create backup package from running system |
| `scripts/export-docker-images.ps1` | Save Docker images as tar files |
| `scripts/backup-ollama-models.ps1` | Export Ollama model files |

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v0.8.8 | 2026-05-13 | Initial offline deployment package |
