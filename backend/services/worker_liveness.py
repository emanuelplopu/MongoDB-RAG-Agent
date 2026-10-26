"""Is an ingestion worker alive? Decides who runs a job started from the UI (F7).

The worker container reports itself through ``activity_current`` (telemetry history,
:mod:`backend.services.activity_events`): one document per process, rewritten every
``PROCESS_HEARTBEAT_SECONDS`` while idle. A worker document updated within a few heartbeats
means a worker is polling the job collection and will claim a ``PENDING`` job tagged for it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.core.config import settings
from backend.core.ingestion_jobs import RUNNER_API, RUNNER_WORKER

logger = logging.getLogger(__name__)

__all__ = ["choose_ui_job_runner", "worker_online"]

#: How many heartbeat intervals a worker may be silent and still count as alive.
_HEARTBEATS_GRACE = 3.0


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


async def worker_online(app_state: Any, within_s: Optional[float] = None, now: Optional[datetime] = None) -> bool:
    """Whether an ingestion worker has reported itself alive within ``within_s`` seconds.

    Args:
        app_state: ``app.state`` (its ``telemetry_store`` is read; no store = no worker known).
        within_s: Liveness window; three heartbeat intervals (at least 90 s) when omitted.
        now: Reference time (tests).
    """
    store = getattr(app_state, "telemetry_store", None)
    if store is None:
        return False
    window = within_s if within_s is not None else max(90.0, _HEARTBEATS_GRACE * float(settings.process_heartbeat_seconds))
    moment = now or datetime.now(timezone.utc)
    try:
        docs = await store.activity_current_docs()
    except Exception as exc:  # noqa: BLE001 - liveness is advisory
        logger.debug("worker liveness unavailable: %s", exc)
        return False
    for doc in docs:
        if doc.get("source") != RUNNER_WORKER:
            continue
        seen = _aware(doc.get("updated_at"))
        if seen is not None and (moment - seen).total_seconds() <= window:
            return True
    return False


async def choose_ui_job_runner(app_state: Any, configured: Optional[str] = None) -> str:
    """``RUNNER_WORKER`` or ``RUNNER_API`` for a job started from the UI.

    ``INGESTION_UI_JOB_RUNNER``: ``worker`` always hands the job to the worker (it waits in
    the job collection until one claims it), ``api`` always runs it in-process, ``auto``
    (default) hands it to the worker when one is alive.
    """
    mode = (configured if configured is not None else settings.ingestion_ui_job_runner or "auto").strip().lower()
    if mode == RUNNER_WORKER:
        return RUNNER_WORKER
    if mode == RUNNER_API:
        return RUNNER_API
    if mode != "auto":
        logger.warning("INGESTION_UI_JOB_RUNNER=%r is not auto/api/worker; using auto", mode)
    return RUNNER_WORKER if await worker_online(app_state) else RUNNER_API
