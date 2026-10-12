# Offline Installer — Test-Machine Postmortem (2026-10-10)

The offline package (`full-backup.ps1` → USB → `INSTALL.ps1`) was tested repeatedly on
the mini PC `DESKTOP-8ESH62J` (this machine) between 2026-05-13 and 2026-10-10 and
never reached a fully working state. This document records what the machine itself
showed, the root causes behind it, and what was changed so the process works end to end.

## What the machine showed

| Evidence | Meaning |
|---|---|
| `C:\ProgramData\RecallHub\install-manifest.json`: `install_complete: false`, no `tenant` key, dated 2026-05-13 | The only `INSTALL.ps1` run that got far enough to write a manifest never finished; every later attempt bypassed the installer. |
| `%LocalAppData%\RecallHub\logs\start-services-*.log` (7 runs, May → October) all end at `Running docker compose up from …`; two earlier runs end with `DISTRO-001 RecallHub WSL distribution not found` | The tray app's start script died on every run. The services only came up because Docker Desktop restarted containers with `restart: unless-stopped`. |
| `%LocalAppData%\RecallHub\docker-compose.yml` / `.env` / `config.json` are the **hardened installer's** files (named volumes, `recallhub-hardened-*` images, Ollama in Docker); `config.json` has no `installPath` | The tray app was pointed at a compose project that cannot start here, in a folder whose Compose project name ("recallhub") collides with the real stack. |
| Container `rag-frontend-11080` (Quellex frontend image, `SERVER_DOMAINS=localhost`, port 11080), created by hand on 2026-06-01 | Workaround so the tray app, which only knows port 11080, shows something for a Quellex install (11081). |
| `docker images` shows `mongodb-rag-agent-*`, `recallhub-*` and `quellex-*` families; `recallhub-backend-quellex` was **built on this machine** on 2026-05-14 | Image names depended on the folder name of the Compose project, so the images exported from the dev machine were never used; the stack rebuilt from source instead. |
| `C:\Quellex` is a copy of `C:\RecallHub`; network `quellex_rag-network` exists; nothing runs from it | Installing the second tenant into its own folder cannot work: both folders claim the same container names. |
| `C:\RecallHub\Hub_Backups\Recallhub_v0.8.8_20260519_110625` contains only `config/` | A backup run died early (dev-machine defaults `E:\…`, `D:\Test_Data`) and left a package with no manifest, source, dump or images. |
| Worker container mounts `D:/parhelion.energy` (no D: drive here); the Quellex backend was created without the override file | Dev-machine paths leaked through the package; the two containers see different document mounts. |
| Fixes dated 2026-06-01 exist only in `C:\Quellex\installer\…` (setup.iss, Load-Images.ps1, Import-OllamaModels.ps1, Start-Services.ps1) | Work done on the test machine never made it back into git. |
| Backend defaults and the restored `.env` use OpenAI/Google; the package still ships 26 GB of gemma4 models and prints "Ollama models loaded" | The "fully offline" claim is not what the restored configuration does. |
| Compose pins `backend-quellex` to `rag_test_law` (1 MB) while the 1.8 GB Parhelion data is in `rag_parhelion`; docs say Quellex → `rag_parhelion` | The live Quellex tenant answers from an almost empty database. |

## Root causes

1. **Two deployment designs shared one tray-app folder.** The hardened Inno Setup design and the backup/restore design both wrote into `%LocalAppData%\RecallHub`, and `INSTALL.ps1` never wrote a runtime `config.json`, so the tray app guessed paths and ports.
2. **PowerShell 5.1 + Docker stderr.** `Start-Services.ps1`/`Stop-Services.ps1` ran with `$ErrorActionPreference = "Stop"` and `2>&1`; Docker's first progress line on stderr terminated the script.
3. **Folder-dependent names.** Without `name:` and `image:` in `docker-compose.yml`, project, network, container and image names changed with the install folder, which broke image reuse and made a second tenant folder impossible.
4. **The installer declared success without checking.** `install_complete` was written unconditionally; restore errors did not stop the installer; the backup script shipped half-finished packages without a verdict.
5. **Port assumptions.** The tray app hard-coded `http://localhost:11080` and polled `/api/v1/status`, which does not exist on the backend (`/api/v1/system/health` does).
6. **Machine-specific defaults and leaked paths** (`E:\RecallHub_Backups`, `D:\Test_Data`, `D:/parhelion.energy`, `G:/My Drive/root`), only partially sanitized and only when a manifest existed.

## What changed

- `docker-compose.yml`: `name: recallhub`; explicit `image:` for every service; the backend image is shared by both tenants; tenant profile/database pins are overridable from `.env` (`QUELLEX_ACTIVE_PROFILE`, …) instead of edited in the file.
- `build-backend.ps1`, `scripts/export-docker-images.ps1`, `scripts/full-backup.ps1`, `scripts/full-restore.ps1`: use the pinned image names. `export-docker-images.ps1` no longer dies on Docker stderr (a missing optional base image used to abort the export after four images were already saved, so every package built with `-IncludeBase` on a machine without the base image was incomplete).
- `installer/scripts/Start-Services.ps1`, `Stop-Services.ps1`: never abort on Docker stderr; read `installPath`, `composeProfile` and ports from `config.json`; include the override file; health-check the real endpoints; explicit exit codes.
- `installer/scripts/Write-TrayConfig.ps1` (new): writes the tray app's `config.json` for the tenant, deploys the scripts, quarantines hardened-installer leftovers. Called by `INSTALL.ps1` and `full-restore.ps1`.
- `scripts/INSTALL.ps1`: refuses to take over a stack owned by another folder unless `-TakeOverStack`; writes the manifest where this user can actually write it (a manifest left by an elevated run in `ProgramData` is read-only for an unelevated run, so the probe tests the file, not the folder, and falls back to `%LocalAppData%` seeded from the old manifest); stops when the restore fails; marks the install complete only after the tenant endpoints answer; keeps `Test_Data` in repair mode unless `-PurgeTestData`; elevates only for Phase 1; `-NoLaunch` / `-NonInteractive` for automation; manifest falls back to `%LocalAppData%` when `ProgramData` is read-only.
- `scripts/full-restore.ps1`: sanitizes override mounts with or without a manifest and remaps the Test_Data mount to this machine's path instead of dropping it; removes containers that use the stack's fixed `rag-*` names but are not managed by Compose (hand-made helpers or containers created with partial labels are invisible to `compose down` and make `compose up` fail with "container name already in use"); `--remove-orphans`; a failed `compose up` is reported with Compose's own error lines and fails the restore.
- `scripts/full-backup.ps1`: UTF-8 (was UTF-16); defaults that do not assume drive letters; never restarts a running MongoDB; dumps every database that `profiles.yaml` references and that exists; writes a preliminary manifest; verifies the package and says `PACKAGE INCOMPLETE` when it is.
- Tray app: ports and profile from `config.json`; scripts first, compose as fallback; `tray-app.log` (incl. WebView2 initialization errors); correct health route; `System.Text.Json` 8.0.5; `IncludeNativeLibrariesForSelfExtract` in the csproj so `WebView2Loader.dll` travels inside the single exe (a plain `dotnet publish` leaves it next to the exe, and `INSTALL.ps1` copies only the exe, which showed up as "Browser Initialization Error" / `DllNotFoundException`).
- Hardened installer: the 2026-06-01 fixes from `C:\Quellex\installer` are now in the repo.
- Docs: `13-DESKTOP_APP.md`, `14-OFFLINE_DEPLOYMENT.md` (how the pieces fit, verification checklist, troubleshooting).

## Verification on this machine (2026-10-10)

Full cycle run on the test PC itself, as the logged-in user without elevation:

1. `scripts\full-backup.ps1 -Tenant quellex -IncludeDockerImages -SkipCompress -OutputPath C:\RecallHub_TestBackups` from the git checkout: 5 databases (1.24 GB gzipped dump), 2.5 GB Test_Data, 356 MB source, 4 image tars (7.7 GB), manifest `status: complete`. The first attempt was correctly flagged `PACKAGE INCOMPLETE` because the image export died on a missing optional base image - that bug is fixed too.
2. `INSTALL.ps1 -SkipPrerequisites -SkipModels -InstallPath C:\RecallHub-Verify -TestDataPath C:\Test_Data -TakeOverStack -NoLaunch -NonInteractive` from the package. Two defects surfaced and were fixed during the runs: the manifest left by the elevated May install was read-only for an unelevated run, and hand-made containers with the stack's names were invisible to Compose and blocked `compose up`.
3. Result of the final run: exit code 0, `install_complete: true`, Compose project owned by `C:\RecallHub-Verify`, four healthy containers, `http://localhost:11081/` and `http://localhost:11001/api/v1/system/health` return 200, `rag_parhelion` (1.7 GB) restored, dev-machine mounts commented out and the Test_Data mount remapped to `C:/Test_Data` for backend and worker alike, tray `config.json` with `installPath`/`composeProfile`/ports, rebuilt `RecallHubTray.exe` deployed.
4. `%LocalAppData%\RecallHub\scripts\Start-Services.ps1 -Wait` (what the tray app runs): exit 0, "All services healthy after 5s", full Compose output in the log.

5. `RecallHubTray.exe` (rebuilt): starts, reads the tenant config (frontend 11081, profile quellex, install path), opens the WebView2 window titled "RecallHub" against `http://localhost:11081`.

6. Atlas Search indexes: `mongorestore` does not carry them, so the restored `rag_parhelion` (48,189 chunks) had none. The `origin/main` merge that landed during this work adds a restore step that recreates `vector_index` (dimension taken from the data) and `text_index`; for the install verified above they were created by hand with the same commands.

Not exercised here: Phase 1 (Docker Desktop / Ollama install) and Phase 2 (model restore) - both were already present on this machine.

## Open decisions (not changed, need an owner)

- Which database the Quellex tenant should serve: compose pins `test-law` / `rag_test_law`, the docs and the backup table say `parhelion` / `rag_parhelion`.
- Whether an "offline" package should switch `.env` to Ollama. Embeddings are OpenAI `text-embedding-3-large` (3072 dims), so going fully offline also means re-ingesting with a local embedding model.
- Whether the hardened Inno Setup installer is still a target at all; it duplicates most of the restore flow with a different layout.
