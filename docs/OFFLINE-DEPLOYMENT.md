# RecallHub Offline Deployment Guide

## Overview

RecallHub deploys as a fully self-contained offline package. Backups are **per-tenant** — each backup targets a single tenant (Quellex or RecallHub) and contains only the Docker images, database collections, and configuration for that tenant. A single backup folder contains everything needed to stand up the tenant on a fresh Windows machine with zero internet connectivity:

- Docker Desktop + WSL2 runtime
- Ollama with pre-loaded AI models
- Tenant-specific Docker images (backend + frontend)
- Shared service images (ingestion worker, MongoDB)
- Tenant-scoped MongoDB database dump
- Complete source code and configuration

The installer is idempotent — safe to re-run after interruptions or restarts. It reads the tenant from `manifest.json` and adjusts paths, ports, and profiles automatically.

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

WSL2 and Hyper-V capable hardware required (most modern PCs).

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

1. Reads tenant from `manifest.json` (e.g., `quellex` or `recallhub`)
2. Copies source code from `source/` to `InstallPath` (default: `C:\<Tenant>`)
3. Writes `.env` and `.env.docker` with tenant-specific configuration
4. Loads tenant Docker images from `images/*.tar` via `docker load`
5. Starts MongoDB container
6. Restores tenant databases from `mongodb/dump/` via `mongorestore`
7. Copies documents to configured path
8. Starts tenant services via `docker compose --profile <tenant> up -d`
9. Runs health checks on tenant endpoints
10. Writes install manifest for future uninstall/upgrade

### Installer Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-InstallPath` | `C:\<Tenant>` | Where source code is deployed (auto-set from tenant) |
| `-TestDataPath` | `C:\Test_Data` | Where document corpus is stored |
| `-SkipPrerequisites` | false | Skip Docker/Ollama install (use after restart) |
| `-Repair` | false | Re-run restore steps without reinstalling prerequisites |
| `-Force` | false | Skip confirmation prompts |

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
# Tenant is read from manifest.json; override install path if needed
& ".\INSTALL.ps1" -InstallPath "D:\MyQuellex" -TestDataPath "D:\MyData"
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
