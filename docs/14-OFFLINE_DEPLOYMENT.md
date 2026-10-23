# RecallHub Offline Deployment Guide

## Overview

RecallHub deploys as a fully self-contained offline package. Backups are **per-tenant** — each backup targets a single tenant (Quellex or RecallHub) and contains only the Docker images, database collections, and configuration for that tenant. A single backup folder contains everything needed to stand up the tenant on a fresh Windows machine with zero internet connectivity:

- Docker Desktop (Hyper-V backend) runtime
- Ollama running natively on the Windows host with pre-loaded AI models
- Tenant-specific Docker images (backend + frontend)
- Shared service images (ingestion worker, MongoDB)
- Docling document-conversion models (layout analysis, TableFormer, RapidOCR) baked into the backend and worker images, so scanned PDFs and images convert without internet access
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
│   ├── mongodb-atlas-local.tar  (shared)
│   └── embedder-llamacpp-server[-cuda].tar  (optional: -IncludeEmbedder, ~1.2 GB each)
├── embedder/                    (optional: -IncludeEmbedder)
│   ├── embedder-models.tar      (contents of the embedder_models volume: GGUF + mmproj, ~0.8 GB)
│   └── embedder-models-manifest.json
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
- Writes Docker Desktop's quiet-mode settings to `%APPDATA%\Docker\settings-store.json` (Docker Desktop 4.35+, created when missing) and the legacy `settings.json`: start at sign-in, no Dashboard window at startup, no onboarding survey, no update banners, no telemetry
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
4. Loads tenant Docker images from `images/*.tar` via `docker load` (including the llama.cpp embedder image when bundled)
5. Starts MongoDB container
6. Restores tenant databases from `mongodb/dump/` via `mongorestore`
7. Copies documents to configured path
7b. Seeds the `recallhub_embedder_models` volume from `embedder/embedder-models.tar` via `installer/scripts/Import-EmbedderModels.ps1` and sets `EMBEDDER_OFFLINE=1` in `.env` (when bundled)
8. Starts tenant services via `docker compose --profile <tenant> [--profile embedder|embedder-gpu] up -d`
9. Runs health checks on tenant endpoints
10. Writes `%LocalAppData%\RecallHub\config.json` + service scripts for the desktop app (`Write-TrayConfig.ps1`)
11. Writes install manifest for future uninstall/upgrade

### Phase 4: Desktop App + verification

- Writes the tray config (`Write-TrayConfig.ps1`): tenant branding (`app.name` = `Quellex` / `RecallHub`) and the `network` section (`hostname`, `advertise`) for the LAN name
- Copies the newest `RecallHubTray.exe` from `installer\tray-app\bin\publish` or `bin\Release\net8.0-windows\win-x64\publish`, plus `icon.ico` and the tenant icon (`quellex.ico`)
- Creates desktop / Start Menu shortcuts named after the tenant (`Quellex.lnk`), the `recallhub://` protocol handler and the HKCU Run entry
- When running elevated, creates the Windows Firewall rules for network access (`Configure-LanAccess.ps1`: TCP 443/80 for the TLS gateway, UDP 5353/5355 for the name announcer); otherwise prints the command to run later
- Polls the tenant frontend and backend; only then marks `install_complete: true` (exit code 2 otherwise). Also probes the gateway port (443 for Quellex)

Other computers then open `https://quellex.local` (see `docs/21-LAN_ACCESS.md`, including how to distribute the gateway's root certificate with `Export-LanCertificate.ps1`).

### Optional: telemetry host agent

`INSTALL.ps1` does not register the host agent (a Windows service needs administrator rights and
is a deliberate choice). After the install, run `installer\scripts\Install-HostAgent.ps1` from an
administrator prompt; `installer\build.ps1` puts the published agent in `output\host-agent`. Without
it the machine-stats widget shows the Docker VM's view and System → Telemetry History has no host
figures. CPU temperatures need the PawnIO driver: offline, bundle the PawnIO installer and pass
`-EnableHardwareSensors -PawnIOInstaller <path>`. See `docs/12-TELEMETRY_SYSTEM.md`, "Host Telemetry History".

### Installer Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-InstallPath` | `C:\<Tenant>` | Where source code is deployed (auto-set from tenant) |
| `-TestDataPath` | `C:\Test_Data` | Where document corpus is stored |
| `-SkipPrerequisites` | false | Skip Docker/Ollama install (use after restart) |
| `-Embedder` | `auto` | Local llama.cpp embedder: `auto` follows the manifest (`-IncludeEmbedder` packages), `none` disables it, `cpu`/`gpu` force the `embedder`/`embedder-gpu` profile |
| `-Repair` | false | Re-run restore steps without reinstalling prerequisites |
| `-PurgeTestData` | false | In `-Repair` mode also delete `Test_Data` (kept by default: it is user data) |
| `-TakeOverStack` | false | Allow taking over a stack that runs from a different install folder |
| `-NoLaunch` | false | Do not start the desktop app at the end (automation) |
| `-NonInteractive` | false | Never wait for a key press or a restart confirmation (automation) |
| `-NoLanAccess` | false | Skip the firewall rules and tell the tray app not to announce the LAN name (the gateway container still runs) |
| `-LanHostname` | tenant id | Name other computers use (`quellex` -> `https://quellex.local`) |

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

### Package the local llama.cpp embedder (optional)

The `embedder` / `embedder-gpu` compose profiles are not part of the default
package: each server image is ~1.2 GB and the EmbeddingGemma 2 GGUF lives in the
`embedder_models` Docker volume, not in an image. Opt in on the source machine
after the embedder has started at least once (the volume must hold the GGUF):

```powershell
.\scripts\full-backup.ps1 -Tenant recallhub -All -IncludeEmbedder                       # CPU image (implies -IncludeDockerImages)
.\scripts\full-backup.ps1 -Tenant recallhub -All -IncludeEmbedder -EmbedderVariant gpu   # CUDA image; "both" ships both

# or standalone (both support -WhatIf)
.\scripts\export-docker-images.ps1 -OutputPath "E:\Backups\...\images" -Tenant recallhub -IncludeEmbedder -EmbedderVariant both
.\scripts\export-embedder-models.ps1 -OutputPath "E:\Backups\...\embedder"
```

`manifest.json` then records `embedder_included`, `embedder_variant`,
`embedder_images_list` and `embedder_model`. On the target, `INSTALL.ps1` /
`full-restore.ps1 -Embedder auto|none|cpu|gpu` (default `auto` = follow the
manifest, CPU image for a "both" package) load the image, seed the volume with
`installer/scripts/Import-EmbedderModels.ps1`, write `EMBEDDER_OFFLINE=1` to
`.env`, start the extra profile and configure the tray app accordingly. See
`docs/04-EMBEDDING_AND_QUERY_SYSTEM.md` → "Local Embedding Server".

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
- `images/` — tenant backend + frontend tars, plus shared images (plus `embedder-llamacpp-server[-cuda].tar` with `-IncludeEmbedder`)
- `embedder/embedder-models.tar` — only with `-IncludeEmbedder`; the package is marked incomplete when it is missing
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

| Service | Port | Reachable from |
|---------|------|----------------|
| RecallHub Backend | 11000 | this machine (`BIND_ADDRESS`, default `127.0.0.1`) |
| RecallHub Frontend | 11080 | this machine |
| RecallHub LAN gateway (HTTPS / redirect) | 11443 / 11480 | network: `https://recallhub.local:11443` |
| Quellex Backend | 11001 | this machine |
| Quellex Frontend | 11081 | this machine |
| Quellex LAN gateway (HTTPS / redirect) | 443 / 80 | network: `https://quellex.local` |
| MongoDB Atlas Local | 11017 | this machine |

The gateway image `caddy:2.11-alpine` is part of every image export
(`export-docker-images.ps1`), so offline installs start it without a registry.

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
# 4. Tray app config points at the install folder and the tenant (app.name = Quellex, network.advertise = true)
Get-Content $env:LOCALAPPDATA\RecallHub\config.json
# 5. Tray app start path works end to end
& $env:LOCALAPPDATA\RecallHub\scripts\Start-Services.ps1 -Wait; $LASTEXITCODE   # must be 0
Get-Content $env:LOCALAPPDATA\RecallHub\logs\tray-app.log -Tail 20
# 6. Network access: gateway answers with TLS, API refuses guests, name is announced
curl.exe -k --resolve quellex.local:443:127.0.0.1 -I https://quellex.local/          # HTTP/1.1 200
curl.exe -k --resolve quellex.local:443:127.0.0.1 https://quellex.local/api/v1/ingestion/documents   # 401
Get-NetFirewallRule -DisplayName "Quellex LAN*" | Select-Object DisplayName, Enabled
Resolve-DnsName quellex.local   # from another computer on the LAN
# 7. Docling models are in the images (scanned documents must not need huggingface.co)
docker exec rag-ingestion-worker sh -c 'ls $DOCLING_ARTIFACTS_PATH'   # docling-project--docling-layout-heron, ds4sd--docling-models, RapidOcr, ...
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

The same applies to the local llama.cpp embedder (`EMBEDDING_BASE_URL=http://embedder:8080/v1`, EmbeddingGemma 2, 768 dimensions): the package must be created with `-IncludeEmbedder` and the source `.env` must already point at the embedder. `full-restore.ps1` warns when `.env` and the enabled profile disagree.

### Docker Desktop window opens at every login

Docker Desktop is single-instance: launching `Docker Desktop.exe` while it is already running (or still booting after sign-in) starts nothing, it only opens the Dashboard window. Tray app builds and `Start-Services.ps1` copies from before October 2026 did exactly that whenever `docker info` did not answer yet, so non-technical users saw the Docker Dashboard at every login. Current versions wait for a booting Docker Desktop and start it only when no process exists, quietly through `docker desktop start`. An existing install needs the new tray exe and scripts: re-run `INSTALL.ps1 -SkipPrerequisites` from a current package, or copy `installer\scripts\*.ps1` and `*.psm1` to `%LocalAppData%\RecallHub\scripts` and replace `RecallHubTray.exe`. A healthy `start-services-*.log` then says "Docker engine is ready" or "waiting up to …s (not relaunching it …)", never "Docker Desktop started from". Details: `docs/13-DESKTOP_APP.md`, "Why Docker Desktop is never relaunched".

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

### Scanned PDFs or images fail to convert, log mentions huggingface.co

The backend and worker images built before 2026-10-15 contained no Docling
models: the first scanned page after every container recreate downloaded
~0.5 GB from huggingface.co and failed on an offline machine. Current images
ship the models under `DOCLING_ARTIFACTS_PATH` (`/opt/docling-models`, set in
`backend/Dockerfile.base`). Check with:

```powershell
docker exec rag-ingestion-worker sh -c 'ls $DOCLING_ARTIFACTS_PATH'
```

If the variable is empty or the folder is missing, the package was exported
from an old base image. Rebuild it on a machine with internet access
(`.\build-backend.ps1 -Base`, then `-Fast` and `-Worker`) and re-export the
images (`docs/15-DOCKER_BUILD_GUIDE.md`, "Docling models in the base image").

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
| `scripts/export-docker-images.ps1` | Export tenant-specific Docker images (`-Tenant`, `-IncludeEmbedder -EmbedderVariant cpu\|gpu\|both`, `-WhatIf`) |
| `scripts/backup-ollama-models.ps1` | Export Ollama model files |
| `scripts/export-embedder-models.ps1` | Archive the llama.cpp GGUF cache volume (`recallhub_embedder_models`) into `embedder/embedder-models.tar` (`-WhatIf`) |
| `installer/scripts/Import-EmbedderModels.ps1` | Seed that volume from the archive on the target (used by `full-restore.ps1`, `INSTALL.ps1` and the hardened installer) |

## Version History

| Version | Date | Notes |
|---------|------|-------|
| v0.8.8 | 2026-05-13 | Initial offline deployment package |
| v0.8.8 | 2026-10-10 | Pinned Compose project/image names, tenant-aware tray config, start scripts no longer abort on Docker stderr, install verified by health check, package completeness check |
| v0.8.8 | 2026-10-10 | Optional llama.cpp embedder in the package (`-IncludeEmbedder`): server image export, `embedder_models` volume export/import, `-Embedder` restore switch, hardened installer `embedder` config |
| v0.8.8 | 2026-10-15 | Docling models (layout, TableFormer, RapidOCR) baked into the backend/worker base image (`DOCLING_ARTIFACTS_PATH`); scanned documents no longer need huggingface.co at runtime |
