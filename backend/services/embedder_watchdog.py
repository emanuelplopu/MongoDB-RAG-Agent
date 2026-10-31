"""Embedder watchdog status: what the host agent reports about the llama.cpp embedding servers.

The llama.cpp embedder of the compose stack (``rag-embedder-gpu``, host port 11090; since
2026-10-31 also ``rag-embedder-query`` on 11091) degrades after a day or two of uptime: one CPU
core pinned, 8-10 GB RSS, a few hundred tokens/s instead of 13-16k on the GPU, every file's
embedding step taking an hour instead of seconds (seen 2026-10-26, -28 and -30). A container
restart restores it within a minute. The API container has no Docker socket, so the RecallHub host
agent (``installer/host-agent/EmbedderWatchdog.cs``, a Windows service) probes each server every
minute with a fixed ~150-token embedding request, restarts the container through the Docker Engine
API when the probes stay slow (``docs/12-TELEMETRY_SYSTEM.md``, "Embedder watchdog") and reports
every check into the host-wide telemetry database (collection ``host_control``,
``_id: "embedder_watchdog"``). This module only reads that report for
``GET /api/v1/system/embedder-watchdog``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field

HOST_CONTROL = "host_control"
EMBEDDER_WATCHDOG_STATUS_ID = "embedder_watchdog"
AGENT_STALE_SECONDS = 300


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _float(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _int(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class EmbedderWatchdogThresholds(BaseModel):
    """The agent's detection rule (``Agent:EmbedderWatchdog`` options)."""

    slow_ms: Optional[int] = Field(default=None, description="A probe answered later than this is slow")
    probe_timeout_seconds: Optional[int] = None
    consecutive_checks: Optional[int] = Field(default=None, description="Slow probes in a row before a restart")
    window_seconds: Optional[int] = Field(default=None, description="The slow probes must span at least this long")
    min_cpu_cores: Optional[float] = Field(default=None, description="Mean container CPU over the slow probes (0 = ignored)")
    warmup_seconds: Optional[int] = Field(default=None, description="No judgement this long after a container start")
    min_restart_interval_seconds: Optional[int] = None
    probe_words: Optional[int] = None


class EmbedderWatchdogTarget(BaseModel):
    """One watched embedding server."""

    name: str
    url: str = ""
    containers: List[str] = Field(default_factory=list, description="Container names behind the URL")
    container: Optional[str] = Field(default=None, description="The container found running")
    container_state: Optional[str] = None
    container_health: Optional[str] = None
    started_at: Optional[datetime] = None
    state: str = Field(default="unknown", description="healthy, slow, degraded, cooldown, warming_up, unreachable, absent or unknown")
    reason: str = ""
    slow_checks: int = 0
    latency_ms: Optional[float] = Field(default=None, description="The last probe's round trip")
    prompt_tokens: Optional[int] = None
    tokens_per_second: Optional[float] = Field(default=None, description="Of the last probe, queue wait included")
    cpu_cores: Optional[float] = Field(default=None, description="Container CPU since the previous probe")
    mem_bytes: Optional[int] = None
    last_check: Optional[datetime] = None
    last_error: Optional[str] = None
    last_restart: Optional[datetime] = None
    last_restart_reason: Optional[str] = None
    restart_count: int = 0
    next_restart_allowed_at: Optional[datetime] = None


class EmbedderWatchdogStatusResponse(BaseModel):
    """``GET /api/v1/system/embedder-watchdog``."""

    reported: bool = Field(description="The host agent has ever written a watchdog report")
    agent_seen: bool = Field(description="The report is younger than stale_after_seconds")
    enabled: Optional[bool] = Field(default=None, description="The watchdog option in the agent")
    check_seconds: Optional[int] = None
    thresholds: Optional[EmbedderWatchdogThresholds] = None
    targets: List[EmbedderWatchdogTarget] = Field(default_factory=list)
    updated_at: Optional[datetime] = Field(default=None, description="When the agent last reported")
    agent_version: Optional[str] = None
    host: Optional[str] = None
    stale_after_seconds: int = AGENT_STALE_SECONDS


def target_from_doc(doc: Mapping[str, Any]) -> EmbedderWatchdogTarget:
    """One ``targets[]`` entry of the agent's report as a typed target."""
    return EmbedderWatchdogTarget(
        name=str(doc.get("name") or ""),
        url=str(doc.get("url") or ""),
        containers=[str(c) for c in (doc.get("containers") or [])],
        container=doc.get("container"),
        container_state=doc.get("container_state"),
        container_health=doc.get("container_health"),
        started_at=_aware(doc.get("started_at")),
        state=str(doc.get("state") or "unknown"),
        reason=str(doc.get("reason") or ""),
        slow_checks=_int(doc.get("slow_checks")) or 0,
        latency_ms=_float(doc.get("latency_ms")),
        prompt_tokens=_int(doc.get("prompt_tokens")),
        tokens_per_second=_float(doc.get("tokens_per_second")),
        cpu_cores=_float(doc.get("cpu_cores")),
        mem_bytes=_int(doc.get("mem_bytes")),
        last_check=_aware(doc.get("last_check")),
        last_error=doc.get("last_error"),
        last_restart=_aware(doc.get("last_restart")),
        last_restart_reason=doc.get("last_restart_reason"),
        restart_count=_int(doc.get("restart_count")) or 0,
        next_restart_allowed_at=_aware(doc.get("next_restart_allowed_at")),
    )


def status_from_doc(doc: Optional[Mapping[str, Any]], now: Optional[datetime] = None,
                    *, agent_stale_seconds: int = AGENT_STALE_SECONDS) -> EmbedderWatchdogStatusResponse:
    """The agent's ``host_control`` report (or None) as the API response."""
    if not doc:
        return EmbedderWatchdogStatusResponse(reported=False, agent_seen=False, stale_after_seconds=agent_stale_seconds)
    moment = now or datetime.now(timezone.utc)
    updated = _aware(doc.get("updated_at"))
    thresholds_doc: Dict[str, Any] = dict(doc.get("thresholds") or {})
    thresholds = EmbedderWatchdogThresholds(
        slow_ms=_int(thresholds_doc.get("slow_ms")),
        probe_timeout_seconds=_int(thresholds_doc.get("probe_timeout_seconds")),
        consecutive_checks=_int(thresholds_doc.get("consecutive_checks")),
        window_seconds=_int(thresholds_doc.get("window_seconds")),
        min_cpu_cores=_float(thresholds_doc.get("min_cpu_cores")),
        warmup_seconds=_int(thresholds_doc.get("warmup_seconds")),
        min_restart_interval_seconds=_int(thresholds_doc.get("min_restart_interval_seconds")),
        probe_words=_int(thresholds_doc.get("probe_words")),
    ) if thresholds_doc else None
    return EmbedderWatchdogStatusResponse(
        reported=True,
        agent_seen=updated is not None and (moment - updated).total_seconds() <= agent_stale_seconds,
        enabled=doc.get("enabled") if isinstance(doc.get("enabled"), bool) else None,
        check_seconds=_int(doc.get("check_seconds")),
        thresholds=thresholds,
        targets=[target_from_doc(t) for t in (doc.get("targets") or []) if isinstance(t, Mapping)],
        updated_at=updated,
        agent_version=doc.get("agent_version"),
        host=doc.get("host"),
        stale_after_seconds=agent_stale_seconds,
    )


async def embedder_watchdog_status(collection: Any, now: Optional[datetime] = None,
                                   *, agent_stale_seconds: int = AGENT_STALE_SECONDS) -> EmbedderWatchdogStatusResponse:
    """Reads the agent's report from the ``host_control`` collection (async)."""
    doc = await collection.find_one({"_id": EMBEDDER_WATCHDOG_STATUS_ID})
    return status_from_doc(doc, now, agent_stale_seconds=agent_stale_seconds)
