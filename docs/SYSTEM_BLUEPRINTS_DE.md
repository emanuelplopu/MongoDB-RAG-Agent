# RecallHub - Systemblaupausen

## Inhaltsverzeichnis
1. [Backend-Kerndienste](#1-backend-kerndienste)
2. [Föderierte Agenten-Architektur](#2-föderierte-agenten-architektur)
3. [Ingestion-Warteschlangensystem](#3-ingestion-warteschlangensystem)
4. [Worker-Architektur](#4-worker-architektur)
5. [Frontend-Architektur](#5-frontend-architektur)
6. [i18n-System](#6-i18n-system)
7. [Docker-Deployment-Strategie](#7-docker-deployment-strategie)
8. [Zusätzliche Datenbanksammlungen](#8-zusätzliche-datenbanksammlungen)

---

## 1. Backend-Kerndienste

### 1.1 Konfigurationsdienst (`backend/core/config.py`)

**Zweck:** Zentrale Laufzeitkonfigurationsverwaltung mit Datenbankpersistenz.

**Hauptfunktionen:**
- Konfiguration aus Umgebungsvariablen mit Standardwerten laden
- Konfigurationsänderungen in MongoDB `config`-Sammlung speichern
- Unterstützung für Ingestion-Leistungsparameter
- Thread-sichere Konfigurationsaktualisierungen

**Konfigurationsparameter:**

| Parameter | Typ | Standard | Beschreibung |
|-----------|-----|----------|--------------|
| `concurrent_files` | int | 4 | Parallel verarbeitete Dateien |
| `embedding_batch_size` | int | 100 | Chunks pro Embedding-Batch |
| `max_file_size_mb` | int | 100 | Maximale Dateigröße |
| `chunk_size` | int | 1000 | Ziel-Chunk-Größe (Zeichen) |
| `chunk_overlap` | int | 200 | Überlappung zwischen Chunks |
| `enable_ocr` | bool | True | OCR für Bilder aktivieren |
| `enable_audio_transcription` | bool | True | Whisper-Transkription aktivieren |

---

### 1.2 Datenbankmanager (`backend/core/database.py`)

**Zweck:** Verwaltet MongoDB-Verbindungen mit async/sync Client-Zugriff und profilbewusster Datenbankumschaltung.

**Architektur:**
```
DatabaseManager
├── async_client (AsyncIOMotorClient)
├── sync_client (MongoClient)
├── current_database_name
├── documents_collection
├── chunks_collection
└── switch_profile(profile_key)
```

**Hauptfunktionen:**
- Verbindungspooling mit konfigurierbarer Pool-Größe
- Automatische Wiederverbindung bei Ausfall
- Profilbasierte Datenbank-Isolation
- Sammlungsreferenzen für Dokumente/Chunks

---

### 1.3 Credential Vault (`backend/core/credential_vault.py`)

**Zweck:** Sichere Speicherung und Abruf von OAuth-Tokens und API-Anmeldedaten.

**Verschlüsselung:**
- AES-256-Verschlüsselung im Ruhezustand
- Master-Schlüssel aus `CREDENTIAL_VAULT_KEY` Umgebungsvariable abgeleitet
- Pro-Anmeldedaten-Verschlüsselung mit eindeutigen IVs

**Schema:**
```python
class EncryptedCredential:
    connection_id: str      # Referenz zur Cloud-Verbindung
    provider_type: str      # google_drive, dropbox, etc.
    encrypted_data: bytes   # AES-256 verschlüsseltes JSON
    iv: bytes              # Initialisierungsvektor
    created_at: datetime
    expires_at: Optional[datetime]
```

---

### 1.4 Dateicache (`backend/core/file_cache.py`)

**Zweck:** Lokales Datei-Caching für Cloud-Dokumente während der Verarbeitung.

**Funktionen:**
- LRU-Verdrängungsrichtlinie
- Konfigurierbares Cache-Größenlimit
- Asynchrone Dateioperationen
- Prüfsummenvalidierung

**Cache-Struktur:**
```
/tmp/recallhub_cache/
├── {connection_id}/
│   ├── {file_hash}.pdf
│   ├── {file_hash}.docx
│   └── ...
└── metadata.json
```

---

### 1.5 LLM-Anbieter (`backend/core/llm_providers.py`)

**Zweck:** Einheitliche Schnittstelle für mehrere LLM-Anbieter mit dynamischer Modellumschaltung.

**Unterstützte Anbieter:**

| Anbieter | Modelle | Funktionen |
|----------|---------|------------|
| OpenAI | gpt-4o, gpt-4o-mini, gpt-3.5-turbo | Streaming, Funktionsaufrufe |
| Google | gemini-2.0-flash, gemini-1.5-pro | Streaming, Vision |
| Anthropic | claude-3-sonnet, claude-3-haiku | Streaming, langer Kontext |
| Ollama | llama3, qwen2.5, mistral | Lokal, Offline-Modus |
| OpenRouter | Beliebiges Modell via Routing | Kostenoptimierung |

**Konfigurationsmodell:**
```python
class LLMConfig:
    orchestrator_provider: str  # Anbieter für Denken
    orchestrator_model: str     # Modell für Orchestrator
    worker_provider: str        # Anbieter für Worker
    worker_model: str           # Modell für Worker
    embedding_provider: str     # Anbieter für Embeddings
    embedding_model: str        # Embedding-Modellname
```

---

### 1.6 Modellversionen (`backend/core/model_versions.py`)

**Zweck:** Verfügbare Modellversionen verfolgen und neueste von Anbietern abrufen.

**Schema:**
```python
class ModelVersion:
    provider: str           # openai, google, anthropic
    model_id: str          # gpt-4o, gemini-2.0-flash
    display_name: str      # GPT-4o
    context_window: int    # 128000
    input_price: float     # pro 1M Token
    output_price: float    # pro 1M Token
    supports_vision: bool
    supports_streaming: bool
    supports_function_calling: bool
    last_updated: datetime
```

---

### 1.7 Profilmodelle (`backend/core/profile_models.py`)

**Zweck:** Pydantic-Modelle für Profilkonfiguration und Validierung.

**Modelle:**
```python
class Profile(BaseModel):
    key: str                    # Eindeutiger Bezeichner
    name: str                   # Anzeigename
    description: Optional[str]
    documents_folders: List[str]  # Lokale Pfade
    database: str               # MongoDB-Datenbankname
    collection_documents: str = "documents"
    collection_chunks: str = "chunks"
    vector_index: str = "vector_index"
    text_index: str = "text_index"
    cloud_sources: List[CloudSourceConfig] = []
```

---

### 1.8 Sicherheit (`backend/core/security.py`)

**Zweck:** Authentifizierungs- und Autorisierungsdienstprogramme.

**JWT-Authentifizierung:**
- Token-Generierung mit konfigurierbarer Ablaufzeit
- Token-Validierung mit Benutzer-ID-Extraktion

**API-Schlüssel-Authentifizierung:**
- Schlüsselgenerierung mit Präfix `rh_`
- SHA-256-Hash zur Speicherung
- Schlüsselverifizierung

**Ratenbegrenzung:**
| Endpunkttyp | Ratenbegrenzung |
|-------------|-----------------|
| Auth-Endpunkte | 10/Minute |
| Such-Endpunkte | 60/Minute |
| Chat-Endpunkte | 30/Minute |
| Ingestion-Endpunkte | 5/Minute |
| Admin-Endpunkte | 30/Minute |

---

## 2. Föderierte Agenten-Architektur

### 2.1 Übersicht

Der föderierte Agent verwendet ein Orchestrator-Worker-Muster für komplexe Abfragebearbeitung über mehrere Datenquellen.

```
Benutzerabfrage
    │
    ▼
┌─────────────────────────────────────┐
│           Koordinator               │
│  - Weiterleitung an Handler         │
│  - Streaming-Antworten verwalten    │
│  - Worker-Ergebnisse aggregieren    │
└─────────────────────────────────────┘
    │
    ├─── Einfache Abfrage ──▶ Direkte Antwort
    │
    └─── Komplexe Abfrage ──▶ Orchestrator
                              │
                         ┌────┴────┐
                         │         │
                    ┌────▼────┐ ┌──▼───┐
                    │Orchestrator│Worker│
                    │  (Plan)   ││Pool │
                    └────┬────┘ └──┬───┘
                         │         │
                    Aufgabenplan  Ausführen
                         │         │
                         └────┬────┘
                              │
                         Synthese
                              │
                              ▼
                       Endantwort
```

---

### 2.2 Koordinator (`backend/agent/coordinator.py`)

**Zweck:** Einstiegspunkt für alle Agentenanfragen, leitet an entsprechenden Handler weiter.

**Verantwortlichkeiten:**
1. Benutzerabfrage und Kontext empfangen
2. Abfragekomplexität bestimmen
3. An direkte Antwort oder Orchestrator weiterleiten
4. Streaming-Antwortbereitstellung verwalten
5. Ergebnisse von Workern aggregieren

**Abfrageklassifizierung:**
```python
class QueryComplexity(Enum):
    SIMPLE = "simple"        # Begrüßung, Klärung
    SEARCH = "search"        # Einzelquellen-Suche
    FEDERATED = "federated"  # Mehrquellen-Suche
    COMPLEX = "complex"      # Erfordert Planung/Reasoning
```

---

### 2.3 Orchestrator (`backend/agent/orchestrator.py`)

**Zweck:** Plant und koordiniert komplexe mehrstufige Abfragen.

**Phasen:**

| Phase | Beschreibung | Ausgabe |
|-------|--------------|---------|
| ANALYZE | Intent parsen, Entitäten extrahieren | QueryAnalysis |
| PLAN | Ausführungsplan erstellen | TaskPlan |
| EXECUTE | Aufgaben an Worker senden | TaskResults |
| EVALUATE | Ergebnisqualität bewerten | EvaluationResult |
| SYNTHESIZE | Endantwort generieren | SynthesizedResponse |

---

### 2.4 Worker-Pool (`backend/agent/worker_pool.py`)

**Zweck:** Aufgaben parallel ausführen mit schnellen, kosteneffizienten Modellen.

**Worker-Konfiguration:**
```python
class WorkerPoolConfig:
    max_workers: int = 4          # Parallele Worker-Grenze
    worker_model: str             # Schnelles Modell (z.B. Gemini Flash)
    task_timeout_seconds: int = 30
    retry_attempts: int = 2
```

**Aufgabentypen:**

| Typ | Beschreibung | Worker-Aktion |
|-----|--------------|---------------|
| `search_profile` | Aktuelles Profil durchsuchen | Vektor + Textsuche |
| `search_all` | Alle Profile durchsuchen | Föderierte Suche |
| `web_search` | Internetsuche | Brave/Google API |
| `browse_url` | URL-Inhalt abrufen | HTTP fetch + extrahieren |
| `email_search` | E-Mails durchsuchen | Verbundene E-Mail-Quellen |

---

### 2.5 Föderierte Suche (`backend/agent/federated_search.py`)

**Zweck:** Suche über mehrere Datenquellen und Profile.

**Quellen:**
1. **Profildatenbanken** - MongoDB-Sammlungen pro Profil
2. **Cloud-Quellen** - Google Drive, Dropbox, etc.
3. **E-Mail-Quellen** - Gmail, Outlook via Airbyte
4. **Extern** - Websuche, URL-Inhalte

**Ergebnis-Fusion:**
- Reciprocal Rank Fusion (RRF) zur Ergebniszusammenführung
- Deduplizierung nach Inhalts-Hash
- Sortierung nach kombiniertem Score

---

### 2.6 Agenten-Schemas (`backend/agent/schemas.py`)

**Ereignistypen für Streaming:**
```python
class AgentEventType(str, Enum):
    START = "start"           # Anfrage gestartet
    THINKING = "thinking"     # Agent denkt nach
    PLANNING = "planning"     # Plan wird erstellt
    SEARCHING = "searching"   # Suche läuft
    TASK_START = "task_start" # Aufgabe gestartet
    TASK_COMPLETE = "task_complete"  # Aufgabe abgeschlossen
    TOKEN = "token"           # Text-Token
    SOURCE = "source"         # Quelle gefunden
    COMPLETE = "complete"     # Fertig
    ERROR = "error"           # Fehler
```

---

## 3. Ingestion-Warteschlangensystem

### 3.1 Übersicht

Die Ingestion-Warteschlange bietet asynchrone, profilbewusste Dokumentverarbeitung.

**Endpunkte:**

| Methode | Endpunkt | Beschreibung |
|---------|----------|--------------|
| GET | `/queue/jobs` | Warteschlangenjobs auflisten |
| POST | `/queue/add` | Einzelne Datei/Ordner hinzufügen |
| POST | `/queue/add-multiple` | Mehrere Elemente hinzufügen |
| GET | `/queue/status/{job_id}` | Jobstatus abrufen |
| POST | `/queue/cancel/{job_id}` | Ausstehenden Job abbrechen |
| POST | `/queue/retry/{job_id}` | Fehlgeschlagenen Job wiederholen |
| DELETE | `/queue/clear` | Abgeschlossene Jobs löschen |
| GET | `/queue/stats` | Warteschlangenstatistiken |

---

### 3.2 Job-Schema

```python
class QueuedIngestionJob(BaseModel):
    id: str                     # UUID
    profile: str                # Zielprofil
    documents_folder: str       # Quellordnerpfad
    status: JobStatus           # pending, running, completed, failed
    priority: int = 0           # Höher = früher
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    progress: JobProgress
    error: Optional[str]

class JobProgress(BaseModel):
    total_files: int = 0
    processed_files: int = 0
    failed_files: int = 0
    current_file: Optional[str]
    percent: float = 0.0
```

---

## 4. Worker-Architektur

### 4.1 Ingestion-Worker (`backend/workers/ingestion_worker.py`)

**Zweck:** Hintergrundprozess, der Ingestion-Jobs aus der Warteschlange verarbeitet.

**Lebenszyklus:**
```
┌────────────────────────────────────────────┐
│            Ingestion Worker                │
├────────────────────────────────────────────┤
│ 1. DB-Verbindung initialisieren            │
│ 2. Aktuelles Profil laden                  │
│ 3. Nach ausstehenden Jobs abfragen         │
│ 4. Job verarbeiten:                        │
│    a. Dateien entdecken                    │
│    b. Jede Datei verarbeiten (parallel)    │
│    c. Embeddings generieren (gebatcht)     │
│    d. In Profildatenbank speichern         │
│    e. Fortschritt aktualisieren            │
│ 5. Job als abgeschlossen/fehlgeschlagen    │
│ 6. Zurück zu Schritt 3                     │
└────────────────────────────────────────────┘
```

---

### 4.2 Sync-Worker (`backend/workers/sync_worker.py`)

**Zweck:** Cloud-Quellen mit lokaler Wissensbasis synchronisieren.

**Sync-Typen:**
- **Volle Synchronisierung:** Alle Dateien von Cloud-Quelle neu indizieren
- **Inkrementelle Synchronisierung:** Nur Änderungen seit letzter Sync verarbeiten
- **Geplante Synchronisierung:** Automatische Sync basierend auf Cron-Zeitplan

---

## 5. Frontend-Architektur

### 5.1 React-Kontexte

**AuthContext:** Benutzerauthentifizierung und Sitzungsverwaltung
**ThemeContext:** Hell/Dunkel/System-Themenumschaltung
**LanguageContext:** Sprachumschaltung (Deutsch/Englisch)
**ToastContext:** Benachrichtigungssystem
**ChatSidebarContext:** Chat-Sitzungen und Ordnerverwaltung
**UserPreferencesContext:** Benutzereinstellungen (Agentenmodus, etc.)

---

### 5.2 Benutzerdefinierte Hooks

**useLocalStorage:** Persistente Speicherung im Browser
**useKeyboardShortcuts:** Globale Tastaturkürzel
**useClipboard:** Zwischenablage-Operationen
**useSelection:** Mehrfachauswahl-Verwaltung

**Globale Tastaturkürzel:**
- `Strg/Cmd+K`: Befehlspalette öffnen
- `/`: Suche fokussieren
- `Escape`: Modal schließen
- `Strg/Cmd+Enter`: Nachricht senden

---

### 5.3 Seitenübersicht

| Seite | Pfad | Zweck | Admin |
|-------|------|-------|-------|
| Dashboard | `/` | Übersicht, Schnellaktionen | Nein |
| Chat | `/chat/:id?` | KI-Gesprächsschnittstelle | Nein |
| Suche | `/search` | Direkte Dokumentensuche | Nein |
| Dokumente | `/documents` | Indizierte Dokumente durchsuchen | Nein |
| Dokumentvorschau | `/documents/:id` | Dokumentinhalt anzeigen | Nein |
| Konfiguration | `/configuration` | LLM/Embedding-Einstellungen | Ja |
| Profile | `/profiles` | Profilverwaltung | Ja |
| Suchindizes | `/indexes` | Indexstatus und Erstellung | Ja |
| Lokales LLM | `/local-llm` | Offline-Modus-Einstellungen | Ja |
| Cloud-Quellen | `/cloud-sources` | Cloud-Anbieter-Dashboard | Nein |
| Benutzerverwaltung | `/admin/users` | Benutzer-CRUD, Zugriffskontrolle | Ja |
| Ingestion | `/admin/ingestion` | Dokument-Ingestion-Warteschlange | Ja |
| Prompts | `/admin/prompts` | Prompt-Vorlagenverwaltung | Ja |
| API-Schlüssel | `/api-keys` | Persönliche API-Schlüsselverwaltung | Nein |
| Status | `/status` | Systemstatus-Dashboard | Nein |
| Anmeldung | `/login` | Authentifizierung | Nein |

---

## 6. i18n-System

### 6.1 Struktur

```
frontend/src/i18n/
├── index.ts          # i18next-Initialisierung
└── locales/
    ├── en.json       # Englische Übersetzungen
    └── de.json       # Deutsche Übersetzungen
```

### 6.2 Übersetzungskategorien

| Kategorie | Schlüsselpräfix | Beschreibung |
|-----------|-----------------|--------------|
| Navigation | `nav.*` | Seitenleiste und Header-Navigation |
| Allgemein | `common.*` | Gemeinsame UI-Elemente |
| Auth | `auth.*` | Anmeldung, Abmeldung, Registrierung |
| Chat | `chat.*` | Chat-Schnittstelle |
| Suche | `search.*` | Suchseite |
| Dokumente | `documents.*` | Dokumentenverwaltung |
| Ingestion | `ingestion.*` | Ingestion-Warteschlange |
| Profile | `profiles.*` | Profilverwaltung |
| Einstellungen | `settings.*` | Konfigurationsseiten |
| Fehler | `errors.*` | Fehlermeldungen |
| Leere Zustände | `emptyStates.*` | Leere Zustandsnachrichten |

---

## 7. Docker-Deployment-Strategie

### 7.1 Servicearchitektur

```yaml
# docker-compose.yml Dienste
services:
  mongodb:           # Datenbank
  backend:           # FastAPI-Anwendung
  ingestion-worker:  # Hintergrund-Jobprozessor
  frontend:          # React-Anwendung (Nginx)
```

### 7.2 Image-Strategie

**Zweistufiger Build:**

1. **Basis-Image** (`Dockerfile.base`):
   - Python-Laufzeit
   - Systemabhängigkeiten (poppler, tesseract)
   - ML-Bibliotheken (torch, transformers)
   - Gecacht für schnellere Rebuilds

2. **Anwendungs-Image** (`Dockerfile`):
   - FROM Basis-Image
   - Nur Anwendungscode
   - Schneller Rebuild (~30 Sekunden)

### 7.3 Volume-Mounts

```yaml
volumes:
  # Dokumentquellen
  - ./documents:/app/documents:ro
  - ./projects:/app/projects:ro
  
  # Profilspezifische Mounts
  - ./mounts/parhelion-energy:/app/mounts/parhelion-energy:ro
  
  # Konfiguration
  - ./profiles.yaml:/app/profiles.yaml:ro
  
  # Datenpersistenz
  - mongodb_data:/data/db
```

---

## 8. Zusätzliche Datenbanksammlungen

### 8.1 `ingestion_queue` Sammlung

```javascript
{
  "_id": "uuid",
  "profile": "string",            // Zielprofil-Schlüssel
  "documents_folder": "string",   // Quellordnerpfad
  "status": "pending|running|completed|failed|cancelled",
  "priority": 0,                  // Höher = früher
  "created_at": ISODate,
  "progress": {
    "total_files": 0,
    "processed_files": 0,
    "failed_files": 0,
    "current_file": "string",
    "percent": 0.0
  },
  "error": "string"
}
```

### 8.2 `model_versions` Sammlung

```javascript
{
  "_id": "provider:model_id",
  "provider": "openai|google|anthropic|ollama",
  "model_id": "gpt-4o",
  "display_name": "GPT-4o",
  "context_window": 128000,
  "input_price_per_million": 2.50,
  "output_price_per_million": 10.00,
  "supports_vision": true,
  "supports_streaming": true,
  "last_updated": ISODate
}
```

### 8.3 `worker_status` Sammlung

```javascript
{
  "_id": "worker_type",           // ingestion, sync
  "status": "healthy|unhealthy|unknown",
  "current_job_id": "uuid",
  "jobs_completed": 0,
  "jobs_failed": 0,
  "started_at": ISODate,
  "last_heartbeat": ISODate,
  "last_error": "string"
}
```

### 8.4 `sync_state` Sammlung

```javascript
{
  "_id": "connection_id",
  "provider_type": "google_drive|dropbox|etc",
  "delta_token": "string",        // Anbieterspezifischer Cursor
  "last_sync_at": ISODate,
  "last_sync_status": "success|partial|failed",
  "files_indexed": 0,
  "total_size_bytes": 0
}
```

---

## Dokumentversion

**Version:** 1.0.0  
**Zuletzt aktualisiert:** 19.02.2026  
**Status:** Vollständig

---

*Dieses Dokument ergänzt PROJECT_DOCUMENTATION.md mit detaillierten technischen Blaupausen für alle Systemkomponenten.*
