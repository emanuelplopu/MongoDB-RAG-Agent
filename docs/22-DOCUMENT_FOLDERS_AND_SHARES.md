# 22 – Document Folders and Network Shares

Last reviewed: 2026-10-15

How a profile gets its document folders, how an organisation admin (for
example `office@diejuristin.at`, the Quellex team account) adds the firm's
network folders herself, and what the administrator has to attach first.

Related: `docs/21-LAN_ACCESS.md` (who can reach the install),
`docs/14-OFFLINE_DEPLOYMENT.md` (installer), `01-SYSTEM_BLUEPRINT.md`
(roles, profile API).

## What you get

- A profile lists several folders (`documents_folders` in `profiles.yaml`).
  Every ingestion run scans all of them into the same database; the
  Documents page, search, chat and the document viewer work across all of
  them because the worker and the API see the folders at the same container
  paths.
- Organisation admins (`is_org_admin`) open **Profiles** in the sidebar, see
  the profiles they have access to and edit name, description and folders.
  Folders are picked from the server's document roots in a folder picker or
  typed as `/app/mounts/<share>/<folder>`. The System menu (ingestion,
  configuration, prompts, backups, ...) stays superadmin-only; organisation
  admins keep only **Users** there.
- Superadmins keep everything: create/delete profiles, change the database,
  the access matrix, arbitrary folder paths.
- Local folders: any sub-folder of `<install>\mounts` is visible as
  `/app/mounts/<name>` without touching Compose.
- Network shares (SMB/CIFS on a NAS or file server): attached once by the
  administrator with `installer\scripts\Add-NetworkShare.ps1`; they appear
  as `/app/mounts/<name>` too.
- New folders are picked up by the next ingestion run (manual or scheduled)
  without restarting anything: the worker re-reads `profiles.yaml` before
  every job.

## Pieces

| Piece | Where | Role |
|---|---|---|
| Document roots | `DOCUMENT_ROOTS` (`backend/core/config.py`), default `/app/mounts,/app/documents` | Container directories below which organisation admins may pick folders |
| Folder validation and browsing | `backend/services/document_folders.py` | Normalises paths, refuses anything outside the roots or not mounted, lists sub-folders |
| Profile API | `backend/routers/profiles.py` | `PUT /api/v1/profiles/{key}` accepts organisation admins for their own profiles; `GET /api/v1/profiles/folders/browse` feeds the picker |
| Folder picker | `frontend/src/components/ServerFolderPicker.tsx`, `frontend/src/pages/ProfilesPage.tsx` | Browse roots → share → sub-folder, add/remove folders one by one |
| Sidebar | `frontend/src/components/Layout.tsx`, `frontend/src/api/roles.ts` (`canManageProfiles`) | Profiles entry for superadmins and organisation admins |
| Mounts | `docker-compose.yml` (`./mounts:/app/mounts:ro` in `backend`, `backend-quellex`, `ingestion-worker`, `cli`) | Local folders |
| Shares | `docker-compose.shares.yml` (generated, git-ignored; template `docker-compose.shares.example.yml`) | One CIFS volume per share, attached read-only at `/app/mounts/<name>` |
| Share script | `installer/scripts/Add-NetworkShare.ps1` | Tests the share, writes `.env` + `docker-compose.shares.yml`, restarts |
| Compose file set | `Start-Services.ps1`, `Stop-Services.ps1`, tray app `ServiceManager.cs`, `COMPOSE_FILE` in `.env` | All load `docker-compose.override.yml` and `docker-compose.shares.yml` when present |
| Reload | `src/profile.py` (`ProfileManager.reload`), `backend/workers/ingestion_worker.py`, `backend/routers/ingestion.py` | Jobs see folder changes without a restart |
| Open in Explorer | `MOUNT_MAPPINGS` in `.env` (`container_path=host_path|...`) | Documents page shows the Windows/UNC path |

## Rules the backend enforces

- An organisation admin may only update profiles in her `profile_access`
  list (superadmin grants them in Profiles → Access rights), and only
  `name`, `description`, `documents_folders`. `database` → 403.
- Every folder she stores must be an absolute path below one of the
  document roots and an existing directory inside the API container.
  Otherwise 400 with a message that the Profiles page shows verbatim
  (`... is outside the document roots ...`, `... is not reachable from the
  server ...`).
- Paths are normalised (`..` resolved, no symlink resolution) before they
  are written to `profiles.yaml`; duplicates are dropped.
- Queued ingestion jobs (`POST /api/v1/ingestion-queue/queue/add`) refuse to
  start when any configured folder is missing or unreadable and name the
  folders, instead of silently indexing a subset.

## Setting it up

### A. A folder on this computer

1. Create `<install>\mounts\<name>` (for example
   `C:\RecallHub-Verify\mounts\diejuristin`) and put or sync the documents
   there. Any sub-folder of `mounts` works; nothing in Compose changes.
2. The folder is `/app/mounts/<name>` in the containers immediately (the
   whole `mounts` folder is mounted). A stack started before the folder
   existed sees it as soon as it is created.
3. Optional, for "Open in Explorer" on the Documents page:
   `MOUNT_MAPPINGS=/app/mounts/<name>=C:\...\mounts\<name>` in `.env`, then
   `docker compose up -d`.

### B. A network share (NAS, file server)

Prerequisites: the share is reachable from this computer (`\\server\share`
opens in Explorer), an account with read access (preferably a dedicated
read-only service account whose password contains no `,`), Docker Desktop
running. Docker Desktop's Linux kernel has the CIFS client built in, so the
VM mounts the share itself; no drive letter or Windows mapping is involved.

```powershell
cd C:\RecallHub-Verify            # the folder with docker-compose.yml
.\scripts\Add-NetworkShare.ps1 -Name kanzlei -UncPath \\nas.diejuristin\Kanzlei -Username svc-recallhub -Tenant quellex
```

(In a checkout the script is `installer\scripts\Add-NetworkShare.ps1`.)
The script asks for the password, then:

1. mounts the share into a throwaway Docker volume and lists it; a wrong
   server, path, account or SMB dialect fails here, before anything is
   written (`-SmbVersion 3.0` or `2.1` for old servers, `-Domain` for AD
   accounts, `-SkipTest` to skip);
2. writes `SHARE_KANZLEI_NAME/_UNC/_USER/_PASSWORD/_OPTIONS/_TENANT` into
   `.env` (the password lives only there; `.env` is git-ignored and part of
   the backup set);
3. regenerates `docker-compose.shares.yml` from all `SHARE_*` entries:
   a `local` volume with `type: cifs` per share, attached read-only at
   `/app/mounts/<name>` in `ingestion-worker` and the tenant backend(s);
4. sets `MOUNT_MAPPINGS` (`/app/mounts/kanzlei=\\nas.diejuristin\Kanzlei`)
   and `COMPOSE_FILE` in `.env` so a plain `docker compose up -d` loads the
   shares file as well (the installer scripts and the tray app add it on
   their own);
5. runs `docker compose up -d`, which recreates the backend and worker
   containers with the new volume.

Repeat per share. `-Remove` detaches a share (profiles that still list it
show it as not reachable). `-NoRestart` only writes the configuration.

Manual alternative: copy `docker-compose.shares.example.yml` to
`docker-compose.shares.yml`, edit it, put the credentials into `.env`, add
`COMPOSE_FILE=docker-compose.yml;docker-compose.override.yml;docker-compose.shares.yml`
to `.env` and run `docker compose up -d`. `docker compose config` validates
the files without mounting.

### C. Grant the team account access

Superadmin only, once per profile: Profiles → **Access rights** → tick the
profile for the organisation admin. (`office@diejuristin.at` already has
`diejuristin`.)

### D. What the organisation admin does

1. Sign in, open **Profiles** in the sidebar.
2. On the profile card click the pencil, then the folder icon next to
   *Add folder*. The picker opens at the profile's first folder; **Document
   roots** → `/app/mounts` → the share (`kanzlei`) → sub-folder → **Use this
   folder**. Or type `/app/mounts/kanzlei/Akten` and press Enter.
3. Remove folders with the × next to them; keep at least one.
4. **Save**. The backend checks every folder; a refused folder is explained
   in the form.

Ingestion runs are started from the System menu by a superadmin, so set up a
schedule once (System → Ingestion → Schedules, e.g. daily) or run the
profile manually; the run picks up the new folders. Incremental ingestion
skips files that are already indexed.

## Configuration reference

| Key | Default | Meaning |
|---|---|---|
| `DOCUMENT_ROOTS` | `/app/mounts,/app/documents` | Container paths organisation admins may pick folders under (comma separated). Backends only. |
| `MOUNT_MAPPINGS` | empty | `container_path=host_path` pairs separated by `\|` for "Open in Explorer". Managed for shares by the script; local folders by hand. |
| `COMPOSE_FILE` | unset | Compose file list for plain `docker compose` calls; `;`-separated on Windows. Managed by the script unless customised. |
| `SHARE_<NAME>_UNC` | – | `//server/share` of the share (forward slashes). |
| `SHARE_<NAME>_USER` / `_PASSWORD` | – | Account for the mount. No `,`, `$` or line breaks in the password. |
| `SHARE_<NAME>_OPTIONS` | `vers=3.1.1` | Extra CIFS options (`vers=`, `domain=`). |
| `SHARE_<NAME>_TENANT` | `both` | `quellex`, `recallhub` or `both`: which backend gets the volume. The worker always does. |
| `SHARE_<NAME>_NAME` | – | Mount name (`/app/mounts/<name>`) as given to the script. |

## Verification

- `docker compose config --quiet` (with `COMPOSE_FILE` set, or `-f` for
  each file) parses the files.
- `docker exec rag-backend-quellex ls /app/mounts/<name>` and
  `docker exec rag-ingestion-worker ls /app/mounts/<name>` list the same
  folders.
- `GET /api/v1/profiles/folders/browse?path=/app/mounts/<name>` as the
  organisation admin returns the sub-folders.
- After saving the profile: `docker exec rag-backend-quellex cat
  /app/profiles.yaml` shows the folders; the next ingestion log line
  `Discovering files in N folders...` counts them.
- Tests: `uv run pytest backend/tests/test_profile_folders.py`,
  `npm --prefix frontend run test:run -- ProfilesPage ServerFolderPicker Layout`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Script: `docker volume create failed` / `Mounting the share failed` | Server name not resolvable from the Docker VM (use the IP), wrong share path, account or password, or the server needs an older dialect (`-SmbVersion 3.0`/`2.1`). SMB1-only devices are not supported. |
| `docker compose up` fails with `permission denied` / `mount error(13)` after a password change | Update `SHARE_<NAME>_PASSWORD` in `.env`, then `docker compose down` and `up -d`: the volume keeps the old options until it is recreated (`docker volume rm recallhub_share_<name>`). |
| Folder picker shows the root as *not mounted* | The container was started before the share/folder existed, or the shares file is not in the compose file set (`COMPOSE_FILE` in `.env`; installer scripts add it automatically). `docker compose up -d`. |
| Saving the profile: *outside the document roots* | The path is not below `DOCUMENT_ROOTS`; attach the folder as in A or B first. |
| Saving: *not reachable from the server* | Typo, or the share is offline/unmounted in the API container (`docker exec rag-backend-quellex ls <path>`). |
| Documents open as plain text instead of the PDF | The backend container does not have the folder at the same path as the worker; the shares file attaches it to both, local folders come from `./mounts` in both. |
| Ingestion queue refuses the profile: *Folder does not exist* | One of several configured folders is missing; fix or remove it in Profiles. |
| New folder not indexed | No ingestion run yet: run or schedule one (superadmin). The worker reloads `profiles.yaml` at job start; a job that was already running keeps its folder list. |
| Share works in Explorer but not in Docker | Explorer uses your Windows session credentials and may use SMB over a mapped drive; the VM needs explicit credentials and TCP 445 to the server (firewall). |
