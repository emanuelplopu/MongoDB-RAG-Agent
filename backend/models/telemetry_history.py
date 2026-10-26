"""Response models for the telemetry history API (``/api/v1/telemetry``).

Host metrics come from the Windows host agent (``host_samples``), activity from the API and the
ingestion worker (``activity_events``); see docs/12-TELEMETRY_SYSTEM.md. All timestamps are UTC.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HostPoint(BaseModel):
    """One aggregation bucket of host metrics (averages unless the name ends in ``_max``)."""

    t: datetime
    samples: int = 0
    cpu_pct: Optional[float] = None
    cpu_pct_max: Optional[float] = None
    mem_pct: Optional[float] = None
    mem_pct_max: Optional[float] = None
    mem_used_bytes: Optional[float] = None
    mem_total_bytes: Optional[float] = None
    gpu_util_pct: Optional[float] = None
    gpu_util_pct_max: Optional[float] = None
    vram_used_bytes: Optional[float] = None
    vram_total_bytes: Optional[float] = None
    gpu_temp_c: Optional[float] = None
    gpu_temp_c_max: Optional[float] = None
    cpu_temp_c: Optional[float] = None
    cpu_temp_c_max: Optional[float] = None
    gpu_power_w: Optional[float] = None
    gpu_power_w_max: Optional[float] = None
    cpu_power_w: Optional[float] = None
    cpu_power_w_max: Optional[float] = None
    disk_read_bps: Optional[float] = None
    disk_write_bps: Optional[float] = None
    net_rx_bps: Optional[float] = None
    net_tx_bps: Optional[float] = None
    backend_ms_max: Optional[float] = None
    backend_down: int = 0
    gpu_throttled: int = 0


class ContainerPoint(BaseModel):
    """One aggregation bucket of one container's CPU (cores) and memory (bytes)."""

    t: datetime
    cpu_cores: Optional[float] = None
    cpu_cores_max: Optional[float] = None
    mem_bytes: Optional[float] = None
    mem_bytes_max: Optional[float] = None


class ActivityPoint(BaseModel):
    """Activity per bucket: finished files by outcome and chat requests."""

    t: datetime
    files_ok: int = 0
    files_failed: int = 0
    files_other: int = 0
    file_ms_avg: Optional[float] = None
    chats: int = 0
    chat_errors: int = 0
    chat_ms_avg: Optional[float] = None


class PhaseInterval(BaseModel):
    """A stretch of time an ingestion job spent in one phase."""

    job_id: str
    phase: str
    start: datetime
    end: datetime
    open: bool = Field(default=False, description="Still running at the end of the range.")


class JobSummary(BaseModel):
    """An ingestion job seen in the activity events."""

    job_id: str
    status: Optional[str] = None
    tenant: Optional[str] = None
    source: Optional[str] = None
    first_ts: datetime
    last_ts: datetime
    files: int = 0
    failed: int = 0


class HistoryResponse(BaseModel):
    """Host metrics, per-container use, activity and job phases over a time range."""

    start: datetime
    end: datetime
    bucket_seconds: int
    agent_samples: int = Field(description="Host-agent samples in the range (0 = agent was not running).")
    host: List[HostPoint]
    containers: Dict[str, List[ContainerPoint]]
    activity: List[ActivityPoint]
    phases: List[PhaseInterval]
    jobs: List[JobSummary]


class Episode(BaseModel):
    """A stretch of consecutive flagged samples."""

    start: datetime
    end: datetime
    samples: int
    peak: Optional[float] = None
    detail: Optional[str] = None


class Anomaly(BaseModel):
    """One detected problem with its evidence."""

    kind: str = Field(description="e.g. memory_pressure, gpu_throttling, slow_files, failed_files, unfinished_files.")
    severity: str = Field(description="info, warning or critical.")
    title: str
    detail: str
    count: int = 0
    episodes: List[Episode] = Field(default_factory=list)
    examples: List[Dict[str, Any]] = Field(default_factory=list)


class StepStat(BaseModel):
    """Duration statistics of one per-file step (milliseconds)."""

    step: str
    files: int
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[float] = None
    total_ms: Optional[float] = None
    share_pct: Optional[float] = Field(default=None, description="Share of all step time.")


class ExtStat(BaseModel):
    """Per file type: counts, outcomes and durations."""

    ext: str
    files: int
    ok: int = 0
    failed: int = 0
    empty: int = 0
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None
    max_ms: Optional[float] = None
    bytes: Optional[int] = None
    pages: Optional[int] = None
    ms_per_page: Optional[float] = None


class FileRow(BaseModel):
    """One file with its timings."""

    path: str
    ext: Optional[str] = None
    outcome: Optional[str] = None
    duration_ms: Optional[float] = None
    size_bytes: Optional[int] = None
    pages: Optional[int] = None
    chunks: Optional[int] = None
    steps: Dict[str, float] = Field(default_factory=dict)
    error_type: Optional[str] = None
    error: Optional[str] = None
    ts: Optional[datetime] = None


class PhaseResources(BaseModel):
    """Host and container resource use while a job was in one phase."""

    phase: str
    start: datetime
    end: datetime
    duration_s: float
    host: Dict[str, Optional[float]] = Field(default_factory=dict)
    containers: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)


class JobReport(BaseModel):
    """Everything known about one ingestion job, correlated with host telemetry."""

    job_id: str
    status: Optional[str] = None
    tenant: Optional[str] = None
    source: Optional[str] = None
    start: datetime
    end: datetime
    running: bool
    files: int
    outcomes: Dict[str, int]
    total_bytes: Optional[int] = None
    total_pages: Optional[int] = None
    total_chunks: Optional[int] = None
    files_per_hour: Optional[float] = None
    phases: List[PhaseResources]
    steps: List[StepStat]
    by_ext: List[ExtStat]
    slowest: List[FileRow]
    failures: List[Dict[str, Any]]
    unfinished: List[FileRow]
    throughput: List[ActivityPoint]
    throughput_bucket_seconds: int = Field(description="Bucket size of `throughput`; buckets without files are omitted (zero).")
    anomalies: List[Anomaly]
    agent_samples: int


class AnomalyReport(BaseModel):
    """Anomalies over a time range (all jobs and host conditions)."""

    start: datetime
    end: datetime
    anomalies: List[Anomaly]


# ── MongoDB statistics (``db_samples``, written by backend.services.db_stats) ──


class DbCollectionStats(BaseModel):
    """Size figures of one collection (``$collStats`` storage statistics)."""

    count: Optional[int] = None
    data_bytes: Optional[int] = Field(default=None, description="Uncompressed BSON size.")
    storage_bytes: Optional[int] = Field(default=None, description="Compressed size on disk, indexes excluded.")
    index_bytes: Optional[int] = None
    avg_bytes: Optional[float] = Field(default=None, description="Average document size (uncompressed).")


class DbSearchIndex(BaseModel):
    """One Atlas Search / Vector Search index on the chunks collection."""

    name: str
    type: Optional[str] = Field(default=None, description="search or vectorSearch.")
    status: Optional[str] = Field(default=None, description="READY, BUILDING, FAILED, ...")
    queryable: Optional[bool] = None
    dimensions: Optional[int] = None
    similarity: Optional[str] = None
    quantization: Optional[str] = None


class DbSearchLag(BaseModel):
    """How far the search index trails the collection (documents not yet searchable)."""

    index: str
    indexed_count: int
    collection_count: int
    lag: int = Field(description="collection_count - indexed_count, never negative.")


class DbChunkSample(BaseModel):
    """Shape of a random sample of chunks: embedding coverage, models, chunking method, tokens."""

    size: int
    with_embedding: int
    embedding_coverage_pct: Optional[float] = None
    embedding_dims: Dict[str, int] = Field(default_factory=dict, description="Vector length -> chunks in the sample.")
    embedding_models: Dict[str, int] = Field(default_factory=dict)
    chunk_methods: Dict[str, int] = Field(default_factory=dict)
    with_context_pct: Optional[float] = Field(default=None, description="Chunks embedded with contextual text.")
    token_p50: Optional[float] = None
    token_p95: Optional[float] = None


class DbDocumentSample(BaseModel):
    """Shape of a random sample of documents."""

    size: int
    zero_chunks: int = Field(description="Documents whose metadata says they produced no chunks.")
    zero_chunks_pct: Optional[float] = None


class DbBackupInfo(BaseModel):
    """The newest backup recorded for a database."""

    count: int = 0
    newest_at: Optional[datetime] = None
    newest_status: Optional[str] = None
    newest_type: Optional[str] = None
    newest_size_bytes: Optional[int] = None


class DbDatabaseStats(BaseModel):
    """Statistics of one application database (a profile's ``rag_*`` database)."""

    name: str
    profile_key: Optional[str] = Field(default=None, description="Profile using this database, when known.")
    data_bytes: Optional[int] = None
    storage_bytes: Optional[int] = None
    index_bytes: Optional[int] = None
    size_on_disk_bytes: Optional[int] = None
    collections: Optional[int] = None
    objects: Optional[int] = None
    documents: Optional[DbCollectionStats] = None
    chunks: Optional[DbCollectionStats] = None
    sample: Optional[DbChunkSample] = None
    document_sample: Optional[DbDocumentSample] = None
    search_indexes: List[DbSearchIndex] = Field(default_factory=list)
    search_lag: Optional[DbSearchLag] = None
    jobs_by_status: Dict[str, int] = Field(default_factory=dict)
    backup: Optional[DbBackupInfo] = None
    failed_documents: Optional[int] = None
    file_registry: Optional[int] = None


class DbConnections(BaseModel):
    """``serverStatus.connections``."""

    current: Optional[int] = None
    active: Optional[int] = None
    available: Optional[int] = None
    total_created: Optional[int] = None


class DbWiredTigerCache(BaseModel):
    """WiredTiger cache use."""

    used_bytes: Optional[int] = None
    max_bytes: Optional[int] = None
    dirty_bytes: Optional[int] = None


class DbServerStats(BaseModel):
    """Server-wide figures from ``serverStatus``."""

    version: Optional[str] = None
    uptime_s: Optional[float] = None
    connections: DbConnections = Field(default_factory=DbConnections)
    wt_cache: DbWiredTigerCache = Field(default_factory=DbWiredTigerCache)
    opcounters: Dict[str, int] = Field(default_factory=dict)
    mem_resident_bytes: Optional[int] = None


class DbOplogStats(BaseModel):
    """The replica-set oplog (``local.oplog.rs``): the biggest consumer of disk on a single-node set."""

    data_bytes: Optional[int] = None
    storage_bytes: Optional[int] = None
    max_bytes: Optional[int] = None
    window_s: Optional[float] = Field(default=None, description="Time span the oplog covers.")
    first_at: Optional[datetime] = None
    last_at: Optional[datetime] = None


class DbFsStats(BaseModel):
    """The filesystem holding the data directory (``dbStats.fsUsedSize`` / ``fsTotalSize``)."""

    used_bytes: Optional[int] = None
    total_bytes: Optional[int] = None
    pct: Optional[float] = None


class DbSizes(BaseModel):
    """Size figures of one database."""

    data_bytes: Optional[int] = None
    storage_bytes: Optional[int] = None
    index_bytes: Optional[int] = None
    size_on_disk_bytes: Optional[int] = None


class DbTotals(BaseModel):
    """Sums over the application databases (system and telemetry databases excluded)."""

    databases: int = 0
    documents: int = 0
    chunks: int = 0
    data_bytes: int = 0
    storage_bytes: int = 0
    index_bytes: int = 0
    size_on_disk_bytes: int = 0


class DbSample(BaseModel):
    """One db-stats run."""

    ts: datetime
    collector: Optional[str] = Field(default=None, description="Process that took the sample (<tenant>:<source>).")
    forced: bool = Field(default=False, description="Taken on request rather than on the schedule.")
    duration_ms: Optional[float] = None
    server: Optional[DbServerStats] = None
    oplog: Optional[DbOplogStats] = None
    fs: Optional[DbFsStats] = None
    telemetry: Optional[DbSizes] = Field(default=None, description="Size of the telemetry database itself.")
    totals: DbTotals = Field(default_factory=DbTotals)
    databases: List[DbDatabaseStats] = Field(default_factory=list)
    errors: Dict[str, str] = Field(default_factory=dict, description="Probe -> error message for probes that failed.")


class DbSeriesDatabasePoint(BaseModel):
    """Per-database charting fields of one series point."""

    name: str
    documents: Optional[int] = None
    chunks: Optional[int] = None
    storage_bytes: Optional[int] = None
    data_bytes: Optional[int] = None
    index_bytes: Optional[int] = None
    embedding_coverage_pct: Optional[float] = None
    search_lag: Optional[int] = None


class DbSeriesPoint(BaseModel):
    """Charting fields of one db-stats sample (the last sample of its bucket)."""

    t: datetime
    connections_current: Optional[int] = None
    connections_total_created: Optional[int] = None
    connections_created_per_min: Optional[float] = Field(default=None, description="Derived from consecutive points.")
    wt_cache_used_bytes: Optional[int] = None
    wt_cache_max_bytes: Optional[int] = None
    ops_insert: Optional[int] = None
    ops_query: Optional[int] = None
    inserts_per_min: Optional[float] = Field(default=None, description="Derived from consecutive points.")
    oplog_bytes: Optional[int] = None
    oplog_max_bytes: Optional[int] = None
    oplog_window_s: Optional[float] = None
    fs_used_bytes: Optional[int] = None
    fs_total_bytes: Optional[int] = None
    size_on_disk_bytes: Optional[int] = None
    data_bytes: Optional[int] = None
    storage_bytes: Optional[int] = None
    index_bytes: Optional[int] = None
    chunks: Optional[int] = None
    documents: Optional[int] = None
    databases: List[DbSeriesDatabasePoint] = Field(default_factory=list)


class DbStatsResponse(BaseModel):
    """db-stats series over a range plus the newest full sample."""

    start: datetime
    end: datetime
    bucket_seconds: int
    interval_seconds: int = Field(description="Configured sampling interval.")
    samples: int = Field(description="Points in the range.")
    points: List[DbSeriesPoint]
    latest: Optional[DbSample] = Field(default=None, description="Newest sample overall (may predate the range).")
    next_due_at: Optional[datetime] = None
    lease_holder: Optional[str] = None


# ── overview (the machine-stats widget's second poll) ─────────────────────────


class OverviewDb(BaseModel):
    """Database figures from the newest db-stats sample."""

    sampled_at: datetime
    age_seconds: float
    databases: int = 0
    documents: int = 0
    chunks: int = 0
    data_bytes: int = 0
    storage_bytes: int = 0
    index_bytes: int = 0
    size_on_disk_bytes: int = 0
    oplog_bytes: Optional[int] = None
    embedding_coverage_pct: Optional[float] = Field(default=None, description="Lowest coverage over the databases with chunks.")
    search_indexes_ok: Optional[bool] = Field(default=None, description="Every search index READY and queryable.")
    search_lag: Optional[int] = Field(default=None, description="Documents not yet searchable, summed.")


class OverviewAnomalies(BaseModel):
    """Anomaly counts over the last ``hours``."""

    hours: float
    critical: int = 0
    warning: int = 0
    info: int = 0
    top: List[str] = Field(default_factory=list, description="Titles of the most severe findings.")


class OverviewEtaExtension(BaseModel):
    """Remaining files of one extension and the pace they are estimated at."""

    extension: str
    remaining: int = 0
    files_per_minute: Optional[float] = None
    seconds: Optional[float] = None
    pace_source: Optional[str] = None


class OverviewProcess(BaseModel):
    """Liveness of one API or worker process (from ``activity_current``)."""

    key: str
    tenant: Optional[str] = None
    source: Optional[str] = None
    host: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    age_seconds: Optional[float] = None
    online: bool = False
    job_id: Optional[str] = None
    job_status: Optional[str] = None
    phase: Optional[str] = None
    step: Optional[str] = None
    processed_files: Optional[int] = None
    total_files: Optional[int] = None
    files_per_hour: Optional[float] = Field(default=None, description="Files finished in the last hour.")
    eta_seconds: Optional[float] = Field(
        default=None, description="Remaining time: the per-extension estimate of the ingestion status API, else remaining files at that pace."
    )
    eta_method: Optional[str] = Field(default=None, description="per_extension, overall or pace.")
    eta_by_extension: List[OverviewEtaExtension] = Field(default_factory=list)


class OverviewChat(BaseModel):
    """Chat requests over the last ``hours``."""

    hours: float
    requests: int = 0
    errors: int = 0
    p50_ms: Optional[float] = None
    p95_ms: Optional[float] = None


class OverviewBackup(BaseModel):
    """Newest backup of one database."""

    database: str
    profile_key: Optional[str] = None
    count: int = 0
    newest_at: Optional[datetime] = None
    age_seconds: Optional[float] = None
    size_bytes: Optional[int] = None
    status: Optional[str] = None


class OverviewLlmModel(BaseModel):
    """A model loaded in Ollama (host agent probe)."""

    name: str
    size_bytes: Optional[int] = None
    vram_bytes: Optional[int] = None
    ctx: Optional[int] = None


class OverviewLlm(BaseModel):
    """Models loaded in Ollama when the host agent last looked."""

    sampled_at: Optional[datetime] = None
    models: List[OverviewLlmModel] = Field(default_factory=list)


class TelemetryOverview(BaseModel):
    """Health at a glance: database, anomalies, process liveness, chat latency, backups, loaded LLMs."""

    generated_at: datetime
    db: Optional[OverviewDb] = None
    anomalies: OverviewAnomalies
    processes: List[OverviewProcess] = Field(default_factory=list)
    chat: OverviewChat
    backups: List[OverviewBackup] = Field(default_factory=list)
    llm: OverviewLlm = Field(default_factory=OverviewLlm)
