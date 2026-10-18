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
