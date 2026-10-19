# 27 – Running RecallHub and Quellex on One Host

Last reviewed: 2026-10-19

RecallHub and Quellex are two Compose profiles of one pinned stack
(`docker-compose.yml`, `name: recallhub`). On a host that runs both, the two
backends share one MongoDB server, one read-write `profiles.yaml`, the host
folders `./data/backups` and `./data/telemetry`, the `./mounts` folder and the
ingestion worker. Until 2026-10-19 isolation rested only on each backend using
its own active profile database; nothing tied a profile to a tenant. This note
records how each shared resource is now scoped and what an operator has to do.

The production law-firm machine runs Quellex only. Everything below keeps a
Quellex-only (or RecallHub-only) install working without any change; the
scoping only bites when profiles are owned by a tenant.

Related: `docs/22-DOCUMENT_FOLDERS_AND_SHARES.md` (profile folders),
`docs/12-TELEMETRY_SYSTEM.md` (telemetry, incl. the tenant-aware history
database), `docs/14-OFFLINE_DEPLOYMENT.md` (backup/restore packages),
`docs/21-LAN_ACCESS.md` (login enforcement).

## Overview

| Shared resource | Before | Now |
|---|---|---|
| `profiles.yaml` profiles | every backend lists, switches, edits and deletes every profile; superadmins see all | a profile records its owner (`tenant`); each backend only sees its own and unassigned profiles |
| `profiles.yaml` writes | whole file rewritten from memory, no lock: two backends undo each other's edits | exclusive file lock, re-read, apply one change, rewrite; readers take a shared lock |
| file-level `active_profile` | rewritten by each backend at start (`ACTIVE_PROFILE`) and on every switch: last writer wins | `ACTIVE_PROFILE` is applied in memory only; persisted switches go to `active_profiles.<tenant>` |
| chat telemetry JSONL | both tenants append to `data/telemetry/<date>.jsonl` and `raw/<date>.jsonl`; the viewer shows everything | written to `data/telemetry/<tenant>/…`; the viewer reads its own folder plus legacy files filtered by `record.tenant` |
| personal databases | `user_rag_<email local part>`, `cloud_<email local part>` | `user_rag_<tenant>_<user id>`, `cloud_<tenant>_<user id>` |
| API backups | `data/backups/<type>/…`, profile unresolved (current DB labelled `default`), restore to any database, storage stats for the whole folder | `data/backups/<tenant>/<type>/…`, profile resolved and checked, restore only into the tenant's own databases, stats for the tenant's own backups |
| offline packages | `full-backup.ps1` dumps every profile DB, `full-restore.ps1` restores everything with `--drop`; Quellex mapped to `parhelion` / `rag_parhelion` | dump the tenant's own and unassigned profile DBs; `--drop` only for the tenant's own DBs; Quellex maps to `diejuristin` / `rag_diejuristin` |
| `DOCUMENT_ROOTS` | one value for both backends | per-tenant override `RECALLHUB_DOCUMENT_ROOTS` / `QUELLEX_DOCUMENT_ROOTS` (default unchanged) |

## 1. Profile ownership

`ProfileConfig` in `src/profile.py` has an optional `tenant` field:

```yaml
profiles:
  diejuristin:
    name: Die Juristin
    tenant: quellex
    database: rag_diejuristin
    documents_folders: [/app/mounts/akten]
```

The **effective tenant** of a profile is, in order:

1. its `tenant` field, lower-cased;
2. for profiles written before the field existed, a small table of known keys
   (`LEGACY_PROFILE_TENANTS` in `src/profile.py`): `diejuristin` → `quellex`,
   `parhelion` and `parhelion-full` → `recallhub`;
3. otherwise none: the profile is **unassigned** and, exactly as before,
   visible to every tenant.

Unassigned stays permissive on purpose. On the Quellex box every profile
except `diejuristin` is untagged today (`synthetic-kanzlei`, `default`, …);
hiding untagged profiles would break that install. The price is that an
untagged profile is still shared on a two-tenant host, so tag them there
(section 7).

### Binding a backend to its tenant

The global `ProfileManager` (`get_profile_manager()`) is **bound** to a tenant:

- at construction from the `TENANT_ID` environment variable (Compose sets it
  for `backend` and `backend-quellex`, not for the shared ingestion worker or
  the CLI), and
- in the API lifespan from `settings.tenant_id` (`backend/main.py`), which
  covers local runs without `TENANT_ID` (`recallhub` by default).

A bound manager:

- `list_profiles()` returns only own and unassigned profiles; `get_profile()`
  returns `None` for the others; `switch_profile()` refuses them;
- so every router that goes through it is scoped without per-route code:
  `GET/PUT/DELETE /api/v1/profiles…`, the profile-access matrix and grants
  (`backend/routers/auth.py`), the status dashboard (`backend/routers/status.py`),
  the ingestion queue and schedules (`backend/routers/ingestion_queue.py`; a
  queued job whose profile is hidden or deleted now fails instead of being
  ingested into the current profile) and the in-API ingestion runner. Another
  tenant's profile answers **404**, not 403, so a backend does not even
  confirm that it exists.
- `get_user_accessible_profiles()` intersects stored `profile_access` grants
  with the visible profiles, so a stale grant for another tenant's key is
  ignored.

An **unbound** manager (worker, `quellexctl`, `src/cli.py`) sees every profile:
the one ingestion worker serves both tenants and switches per job.

The process's own active profile can be another tenant's profile only when an
operator pins it with `ACTIVE_PROFILE`; the backend then logs an error at
start and serves it anyway (explicit configuration wins over silently serving
another database). An inherited file-level `active_profile` that belongs to
another tenant is skipped in favour of the first visible profile.

### Creating, claiming and releasing

- `POST /api/v1/profiles/create` stores the creating backend's tenant.
- A superadmin claims an unassigned profile with
  `PUT /api/v1/profiles/{key}` and `{"tenant": "<own tenant>"}`, and releases
  one of the own profiles with `{"tenant": ""}`. Assigning a profile to the
  other tenant is refused (400): that tenant's admins would have to claim it.
  Organisation admins cannot change ownership.
- Editing `profiles.yaml` by hand works too; ingestion jobs and the next write
  re-read the file.

`GET /api/v1/profiles` returns `tenant` (effective owner, `null` when
unassigned) for every profile.

## 2. Safe `profiles.yaml` writes

Both backends mount the same `profiles.yaml` read-write (the worker and the
CLI service mount it read-only). Every mutation in `ProfileManager`
(create, update, delete, cloud sources, Airbyte config, model settings,
persisted switches) now runs as one transaction:

1. take an exclusive `flock` on the file itself (a sidecar lock file would not
   be shared: only the file is bind-mounted into the containers);
2. re-read the file, so changes written by the other backend are kept;
3. apply the one change to that fresh copy;
4. rewrite the file in place and `fsync` (no write-to-temp-and-rename: a
   bind-mounted file cannot be replaced by `rename`).

Loads take a shared lock, so a reader never parses a half-written file. Where
`flock` does not exist (Windows dev runs) or the filesystem refuses it, the
manager falls back to an in-process lock and logs that once; re-read-before-
write still prevents the lost-update case of two backends editing different
profiles.

### The active profile is per process

- `ACTIVE_PROFILE` (each backend's Compose pin) is applied once with
  `persist=False` (`src/settings.py`). Before, both backends wrote their pin
  into the file at start, and whichever started last decided the file-level
  `active_profile`.
- `reload()` (ingestion jobs re-read the file first) keeps the process's
  active profile instead of resetting it to the file's value. Before, a
  Quellex job could flip the Quellex backend to whatever RecallHub had
  persisted.
- `POST /api/v1/profiles/switch` still persists, but a bound manager writes
  `active_profiles.<tenant>` and leaves the file-wide `active_profile` (the
  default for unbound processes) alone. At start a bound manager without an
  `ACTIVE_PROFILE` pin uses `active_profiles.<tenant>`, then `active_profile`.
- Within one backend the active profile is still process-global: all users of
  that backend follow a switch (chat, search, ingestion). That is the existing
  single-workspace design; it is now confined to the backend's own profiles.
- **Behaviour change for the shared ingestion worker.** At start the worker
  takes its job database from the file-wide `active_profile`
  (`IngestionWorker.initialize`, falling back to `MONGODB_DATABASE`).
  Backends used to rewrite that value with their `ACTIVE_PROFILE` pin at start
  and on every switch. They no longer do, so the worker follows only what the
  file says: set `active_profile` by hand (or let `full-restore.ps1` set it,
  `diejuristin` for Quellex). The API currently runs ingestion itself (runner
  `api`) and nothing queues worker jobs, so this only matters once it does. On
  the live Quellex host the file already says `diejuristin`.

## 3. Chat telemetry

`TelemetryService` (`backend/services/telemetry_service.py`) writes to
`<TELEMETRY_STORAGE_PATH>/<tenant>/<date>.jsonl` and `…/<tenant>/raw/<date>.jsonl`
(`data/telemetry/quellex/…` in Compose). The admin viewer
(`backend/routers/telemetry.py`: files, records, search, stats) reads the
backend's own folder plus the legacy files directly under
`data/telemetry/` and `data/telemetry/raw/`, keeping only records whose
`tenant` is the backend's own. Retention rotation covers both. Existing
history therefore stays visible to its tenant after the upgrade; nothing has
to be moved.

The activity/host history in the `recallhub_telemetry` database is scoped
separately (`TelemetryStore(tenant_scope=…)`, PR #8, `docs/12`).

## 4. Personal databases

`FederatedSearch.get_accessible_sources()` (`backend/agent/federated_search.py`)
names a user's private sources

- `user_rag_<tenant>_<user id>` (personal data) and
- `cloud_<tenant>_<user id>` (private cloud storage),

built by `personal_database_name()`: lower-case, characters outside
`[a-z0-9_-]` replaced by `_`, and cut to 63 bytes with a short hash suffix when
longer (MongoDB's database-name limit). The old names, derived from the email
local part only, collided across tenants (each tenant has its own `users`
collection) and across users such as `anna@firm-a.at` / `anna@firm-b.at`.
`cloud_shared_<profile key>` is unchanged: profile keys are unique in the one
`profiles.yaml`, and a profile now has one owner.

**Migration.** No code path writes these databases yet (they are searched only
when they exist), and the live Quellex host had none on 2026-10-19
(`listDatabases`: `admin, config, local, rag_db, rag_diejuristin,
rag_synth_kanzlei_v1, recallhub_telemetry`). Old databases are no longer
searched. If an install does have `user_rag_*` / `cloud_*` databases, copy each
to the new name (user id from that tenant's `users` collection), check, then
drop the old one:

```powershell
docker exec rag-mongodb sh -c "mongodump --db=user_rag_anna --archive | mongorestore --archive --nsFrom='user_rag_anna.*' --nsTo='user_rag_quellex_<user id>.*'"
```

## 5. API backups

`BackupService` (`backend/services/backup_service.py`) is built with the
tenant-bound profile manager and the tenant in the API (`BackupService.for_api`
in `backend/main.py` and `backend/routers/backup.py`). The worker's
post-ingestion backup passes its unbound profile manager, so it resolves the
job's profile database and files the backup under the profile owner's folder
(`shared` for unassigned profiles).

- Files go to `data/backups/<tenant>/<type>/<backup id>` (`backup_root()`).
  Existing backups stay where they are; their metadata records the path.
- `profile_key` is resolved: omitted means the backend's active profile (it
  used to back up the current database under the label `default`), and an
  unknown or other tenant's profile is refused.
- Metadata (`backups_metadata`) records `tenant`. Listing, details, chains,
  delete and restore only cover the tenant's own backups; legacy entries
  without `tenant` are attributed through their profile's effective tenant.
- `restore` with `target_database` only accepts the database of one of the
  tenant's visible profiles or the backup's own database. Before, any
  database name was accepted, including the other tenant's.
- `GET /api/v1/backups/storage` counts and sizes only the tenant's own
  backups instead of everything under `data/backups`.

System collections (`users`, `chat_sessions`, …) are read from and restored
into the backend's own base database, which already differs per tenant.

## 6. Offline packages (`full-backup.ps1`, `full-restore.ps1`)

- `full-backup.ps1 -Tenant <t>` dumps the shared databases, the tenant's
  default database and every database in `profiles.yaml` whose profile is
  owned by `<t>` or unassigned. Profiles owned by the other tenant are
  skipped. Quellex's defaults are `diejuristin` / `rag_diejuristin` (they were
  `parhelion` / `rag_parhelion`, a RecallHub profile).
- `full-restore.ps1 -Tenant <t>` restores with `--drop` only the databases of
  the tenant's own profiles; shared and unassigned databases are restored
  without `--drop` (missing documents are added, existing ones kept), and
  databases of the other tenant found in older packages are skipped with a
  warning. The tenant's `.env` keys are written (and added when missing):
  for Quellex `ACTIVE_PROFILE`, `MONGODB_DATABASE`, `QUELLEX_ACTIVE_PROFILE`,
  `QUELLEX_MONGODB_DATABASE`.

Both scripts read `profiles.yaml` with the same small parser
(`Get-ProfileTenants`) and the same legacy key table as `src/profile.py`.

## 7. Document roots and operator checklist

`DOCUMENT_ROOTS` limits which folders organisation admins may pick
(`docs/22`). Compose now reads `RECALLHUB_DOCUMENT_ROOTS` and
`QUELLEX_DOCUMENT_ROOTS` first, then `DOCUMENT_ROOTS`, then the old default
`/app/mounts,/app/documents`. Both backends still mount the whole `./mounts`
folder; on a two-tenant host give each tenant its own sub-folder, for example
`QUELLEX_DOCUMENT_ROOTS=/app/mounts/quellex` and
`RECALLHUB_DOCUMENT_ROOTS=/app/mounts/recallhub`.

Checklist for a host running both tenants:

1. Tag every profile in `profiles.yaml` with `tenant: recallhub` or
   `tenant: quellex` (or claim them through the API).
2. Set per-tenant document roots (above).
3. Give each tenant its own JWT secret and login enforcement (separate
   proposals; see "Not covered").

A Quellex-only install needs none of this.

## Not covered (follow-ups)

- **MongoDB access control.** Both backends and the worker connect as the same
  user to the same server; scoping is enforced in the application only.
  Per-tenant MongoDB users with database-level roles would make it hard
  isolation.
- **JWT signing secret and role checks on ingestion endpoints**: separate
  proposals.
- **Strategy OS** loads `tenant_scope: recallhub` capabilities for every
  tenant (`backend/agent/strategy/business_context_resolver.py`); needs an
  evaluation run before changing.
- **Users, sessions and API keys live in the active profile database**
  (`db_manager.db` follows profile switches). Now confined to the tenant's own
  profiles, but switching profiles inside one tenant still changes the user
  store.
- **Worker post-ingestion backups** land in the worker container (it has no
  `./data/backups` mount) and their metadata in the worker's base database,
  so the API does not list them. The worker now resolves the right profile
  database and tenant folder; mounting `./data/backups` into the worker is
  the remaining step.
- **Mount-level separation**: mounting `./data/telemetry/<tenant>` and
  `./data/backups/<tenant>` per backend (instead of the whole folders) would
  stop a compromised backend from reading the other tenant's files.
