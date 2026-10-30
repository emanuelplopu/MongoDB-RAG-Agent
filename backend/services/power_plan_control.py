"""Host power plan control: the backend says whether the CPU clock should be capped, the host agent does it.

The ``office`` and ``office_lite`` load profiles can ask for a capped CPU clock during office
hours (``office_hours.cap_cpu_boost``): the Windows power plan's maximum processor state at 99 %
with the boost mode disabled. On the client's Minisforum MS-02 Ultra that meant 2.2 GHz, 28-35 W
and a 55 °C package instead of 4.4 GHz, 88 W and 87 °C, at roughly a tenth of the indexing pace
(``docs/04-EMBEDDING_AND_QUERY_SYSTEM.md``, "Clock cap"). Changing the plan needs administrator
rights the API container does not have, so the backend only publishes the desired state into the
host-wide telemetry database (collection ``host_control``, one document per tenant,
``_id: "power_plan:<tenant>"``) and the RecallHub host agent (``installer/host-agent``, a Windows
service running as LocalSystem) applies it with ``powercfg`` and writes back what it did
(``_id: "power_plan"``). The agent caps when any fresh tenant document asks for it and lifts the
cap otherwise; a desire older than ``DESIRED_FRESH_SECONDS`` counts as "normal", so a stopped
backend never leaves the machine capped.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field

from backend.services.ingestion_schedule import effective_limits

logger = logging.getLogger(__name__)

HOST_CONTROL = "host_control"
POWER_PLAN_STATUS_ID = "power_plan"
DESIRED_ID_PREFIX = "power_plan:"
DESIRED_FRESH_SECONDS = 600
PUBLISH_INTERVAL_SECONDS = 60.0

CAPPED = "capped"
NORMAL = "normal"


def desired_id(tenant: str) -> str:
    """The ``host_control`` document id of a tenant's desired power plan."""
    return f"{DESIRED_ID_PREFIX}{tenant}"


def desired_power_plan(config: Optional[Mapping], now: Optional[datetime] = None) -> Tuple[str, str]:
    """``("capped" | "normal", reason)`` for a performance config at ``now``.

    Capped only while an office profile is in its office hours and ``cap_cpu_boost`` is set.
    """
    limits = effective_limits(config, now)
    reason = f"{limits.label}, load profile {limits.profile}"
    return (CAPPED if limits.cap_cpu_boost else NORMAL), reason


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class PowerPlanDesired(BaseModel):
    """One tenant's published desire."""

    tenant: str
    desired: str
    reason: str = ""
    updated_at: Optional[datetime] = None
    fresh: bool = Field(description="Younger than DESIRED_FRESH_SECONDS; stale desires count as normal")


class PowerPlanAgentStatus(BaseModel):
    """What the host agent last reported (``host_control`` ``_id: "power_plan"``)."""

    state: str = Field(default="unknown", description="capped, normal or unknown")
    desired: Optional[str] = None
    max_state_ac: Optional[int] = None
    boost_mode_ac: Optional[int] = None
    applied_at: Optional[datetime] = Field(default=None, description="When the agent last changed the plan")
    updated_at: Optional[datetime] = Field(default=None, description="When the agent last checked")
    error: Optional[str] = None
    agent_version: Optional[str] = None


class PowerPlanStatusResponse(BaseModel):
    """``GET /api/v1/system/power-plan``."""

    desired: str = Field(description="This tenant's current desire: capped or normal")
    reason: str
    tenants: List[PowerPlanDesired] = Field(default_factory=list, description="Every tenant's published desire")
    agent: Optional[PowerPlanAgentStatus] = None
    agent_seen: bool = Field(description="The host agent reported within the last few minutes")
    stale_after_seconds: int = DESIRED_FRESH_SECONDS


class PowerPlanPublisher:
    """Publishes this tenant's desired power plan every ``interval_s`` seconds (and on start).

    Args:
        collection: The telemetry database's ``host_control`` collection (async).
        load_config: Returns the current ``performance_config`` document (None = defaults).
        tenant: This backend's tenant id.
        interval_s: Seconds between publications; the host agent treats a desire older than
            ``DESIRED_FRESH_SECONDS`` as normal, so keep this well below it.
        now_fn: Clock, for tests.
    """

    def __init__(
        self,
        collection: Any,
        load_config: Callable[[], Awaitable[Optional[Mapping]]],
        tenant: str,
        *,
        interval_s: float = PUBLISH_INTERVAL_SECONDS,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._collection = collection
        self._load_config = load_config
        self._tenant = tenant
        self._interval = max(5.0, float(interval_s))
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._task: Optional[asyncio.Task] = None
        self._last: Optional[Tuple[str, str]] = None

    @property
    def tenant(self) -> str:
        return self._tenant

    @property
    def last(self) -> Optional[Tuple[str, str]]:
        """The last published ``(desired, reason)``."""
        return self._last

    async def publish_once(self) -> Tuple[str, str]:
        """Evaluate the config now and upsert this tenant's desire."""
        try:
            config = await self._load_config()
        except Exception as exc:
            logger.warning(f"Power plan: could not read the performance config, publishing 'normal': {exc}")
            config = None
        desired, reason = desired_power_plan(config, self._now())
        await self._collection.update_one(
            {"_id": desired_id(self._tenant)},
            {"$set": {
                "tenant": self._tenant,
                "desired": desired,
                "reason": reason,
                "source": "api",
                "updated_at": self._now(),
            }},
            upsert=True,
        )
        if (desired, reason) != self._last:
            logger.info(f"Power plan desired: {desired} ({reason})")
            self._last = (desired, reason)
        return desired, reason

    async def _loop(self) -> None:
        while True:
            try:
                await self.publish_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Power plan publication failed: {exc}")
            await asyncio.sleep(self._interval)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name=f"power-plan-publisher:{self._tenant}")

    async def stop(self) -> None:
        """Stop publishing and withdraw this tenant's desire (the agent then lifts the cap)."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        try:
            await self._collection.update_one(
                {"_id": desired_id(self._tenant)},
                {"$set": {"desired": NORMAL, "reason": "backend stopped", "updated_at": self._now()}},
            )
        except Exception as exc:
            logger.debug(f"Power plan: could not withdraw the desire on shutdown: {exc}")


async def power_plan_status(
    collection: Any,
    tenant: str,
    config: Optional[Mapping],
    now: Optional[datetime] = None,
    *,
    agent_stale_seconds: int = 180,
) -> PowerPlanStatusResponse:
    """This tenant's desire, every tenant's published desire and the agent's last report."""
    moment = now or datetime.now(timezone.utc)
    desired, reason = desired_power_plan(config, moment)
    tenants: List[PowerPlanDesired] = []
    async for doc in collection.find({"_id": {"$regex": f"^{DESIRED_ID_PREFIX}"}}):
        updated = _aware(doc.get("updated_at"))
        tenants.append(PowerPlanDesired(
            tenant=str(doc.get("tenant") or str(doc["_id"])[len(DESIRED_ID_PREFIX):]),
            desired=str(doc.get("desired") or NORMAL),
            reason=str(doc.get("reason") or ""),
            updated_at=updated,
            fresh=updated is not None and (moment - updated).total_seconds() <= DESIRED_FRESH_SECONDS,
        ))
    status_doc = await collection.find_one({"_id": POWER_PLAN_STATUS_ID})
    agent: Optional[PowerPlanAgentStatus] = None
    agent_seen = False
    if status_doc:
        updated = _aware(status_doc.get("updated_at"))
        agent = PowerPlanAgentStatus(
            state=str(status_doc.get("state") or "unknown"),
            desired=status_doc.get("desired"),
            max_state_ac=status_doc.get("max_state_ac"),
            boost_mode_ac=status_doc.get("boost_mode_ac"),
            applied_at=_aware(status_doc.get("applied_at")),
            updated_at=updated,
            error=status_doc.get("error"),
            agent_version=status_doc.get("agent_version"),
        )
        agent_seen = updated is not None and (moment - updated).total_seconds() <= agent_stale_seconds
    return PowerPlanStatusResponse(desired=desired, reason=reason, tenants=tenants, agent=agent, agent_seen=agent_seen)
