"""Ingestion load profiles: how many files a run converts at once, by time of day.

The performance config the Configuration page saves (``ingestion_config.performance_config``)
carries a ``load_profile``:

* ``server_room`` - the machine is in nobody's office: ``max_concurrent_files`` files at once
  around the clock (the behaviour before load profiles existed).
* ``office`` - the machine sits in the office: during office hours (``office_hours``: the
  listed weekdays, ``start`` to ``end`` wall-clock time in the office's timezone) a run
  converts at most ``office_hours.max_concurrent_files`` files at once, so the fans stay
  quiet; nights and the weekend use the full ``max_concurrent_files``.
* ``office_lite`` - ``office`` with ``office_hours.lite_max_concurrent_files`` (default 4)
  during office hours: for a machine that stands next to the people working.

Both office profiles can also ask for a capped CPU clock during office hours
(``office_hours.cap_cpu_boost``): the host agent then sets the Windows power plan to 99 %
maximum processor state with the boost disabled and restores it afterwards
(``backend/services/power_plan_control.py``).

The limit follows the clock inside a running job. The concurrent file loop of
``DocumentIngestionPipeline`` takes its slot limit from :meth:`ConcurrencyController.current`
before every file (the ``slot_limit`` argument of ``ingest_documents``): a lowered limit takes
effect as running files finish, a raised one admits waiting files at once.
:meth:`ConcurrencyController.refresh_loop` re-reads the stored config, so switching the profile
on the Configuration page reaches the running job as well. The run's thread pool and its
conversion worker processes are sized once, for the maximum read when the run started; the
limit only changes how many of those slots are in use.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timezone
from typing import Awaitable, Callable, Dict, List, Literal, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from backend.core.config import settings

logger = logging.getLogger(__name__)

LoadProfile = Literal["server_room", "office", "office_lite"]
LoadPeriod = Literal["server_room", "office_hours", "off_hours"]

LOAD_PROFILES: Tuple[str, ...] = ("server_room", "office", "office_lite")
OFFICE_PROFILES: Tuple[str, ...] = ("office", "office_lite")
DEFAULT_LOAD_PROFILE: LoadProfile = "server_room"
DEFAULT_OFFICE_WEEKDAYS: Tuple[int, ...] = (0, 1, 2, 3, 4)  # Monday to Friday
CONFIG_REFRESH_SECONDS = 60.0

PERIOD_LABELS: Dict[str, str] = {
    "server_room": "server room",
    "office_hours": "office hours",
    "off_hours": "outside office hours",
}


def parse_hhmm(value: str) -> time:
    """``"HH:MM"`` to :class:`datetime.time`.

    Args:
        value: Wall-clock time, hours 0-23 and minutes 0-59 separated by a colon.

    Raises:
        ValueError: Anything else.
    """
    parts = str(value).strip().split(":")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"time must be HH:MM, got {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time out of range: {value!r}")
    return time(hour, minute)


class OfficeHoursConfig(BaseModel):
    """Office hours of the ``office`` load profile."""

    timezone: str = Field(
        default_factory=lambda: settings.ingestion_office_timezone,
        description="IANA timezone the hours are read in, e.g. Europe/Vienna",
    )
    start: str = Field(default="08:00", description="Office hours begin (HH:MM, local time)")
    end: str = Field(default="18:00", description="Office hours end (HH:MM, local time, after start)")
    weekdays: List[int] = Field(
        default_factory=lambda: list(DEFAULT_OFFICE_WEEKDAYS),
        description="Office days, 0 = Monday to 6 = Sunday",
    )
    max_concurrent_files: int = Field(
        default=8,
        ge=1,
        le=32,
        description="Files converted at once during office hours (never above the profile's max_concurrent_files)",
    )
    lite_max_concurrent_files: int = Field(
        default=4,
        ge=1,
        le=32,
        description="Files converted at once during office hours under the office_lite profile (never above max_concurrent_files)",
    )
    cap_cpu_boost: bool = Field(
        default=False,
        description=(
            "During office hours the host agent caps the CPU clock (Windows power plan: 99 % maximum "
            "processor state, boost off) and lifts the cap outside them; cool and quiet, but slow"
        ),
    )

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        name = str(value).strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return name

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        return parse_hhmm(value).strftime("%H:%M")

    @field_validator("weekdays")
    @classmethod
    def _weekdays(cls, value: List[int]) -> List[int]:
        days = sorted({int(day) for day in value})
        if any(day < 0 or day > 6 for day in days):
            raise ValueError("weekdays must be 0 (Monday) to 6 (Sunday)")
        return days

    @model_validator(mode="after")
    def _start_before_end(self) -> "OfficeHoursConfig":
        if parse_hhmm(self.start) >= parse_hhmm(self.end):
            raise ValueError("office hours must end after they start")
        return self

    def files_at_once(self, profile: str) -> int:
        """The office-hours limit of ``profile``: the lite number for ``office_lite``, else the normal one."""
        return self.lite_max_concurrent_files if profile == "office_lite" else self.max_concurrent_files

    def contains(self, now: datetime) -> bool:
        """Whether ``now`` falls into the office hours (a naive datetime counts as UTC)."""
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        local = now.astimezone(ZoneInfo(self.timezone))
        if local.weekday() not in self.weekdays:
            return False
        return parse_hhmm(self.start) <= local.time() < parse_hhmm(self.end)


class EffectiveLimits(BaseModel):
    """The file concurrency in force at one moment."""

    profile: LoadProfile
    period: LoadPeriod
    max_concurrent_files: int
    cap_cpu_boost: bool = Field(default=False, description="The CPU clock should be capped right now")

    @property
    def label(self) -> str:
        """Human-readable period, for logs and the job's status line."""
        return PERIOD_LABELS[self.period]


def load_profile_from(config: Optional[Mapping]) -> LoadProfile:
    """The stored ``load_profile``; missing or unknown values mean ``server_room``."""
    value = (config or {}).get("load_profile") or DEFAULT_LOAD_PROFILE
    if value not in LOAD_PROFILES:
        logger.warning(f"Unknown ingestion load profile {value!r}, using {DEFAULT_LOAD_PROFILE}")
        return DEFAULT_LOAD_PROFILE
    return value  # type: ignore[return-value]


def office_hours_from(config: Optional[Mapping]) -> OfficeHoursConfig:
    """The stored ``office_hours``; missing or invalid values fall back to the defaults."""
    raw = (config or {}).get("office_hours")
    if not raw:
        return OfficeHoursConfig()
    try:
        return OfficeHoursConfig.model_validate(raw)
    except ValidationError as exc:
        logger.warning(f"Invalid office_hours in the performance config, using the defaults: {exc}")
        return OfficeHoursConfig()


def resolve_limits(
    profile: LoadProfile, office: OfficeHoursConfig, maximum: int, now: datetime
) -> EffectiveLimits:
    """The limit at ``now`` for an already parsed profile.

    Args:
        profile: ``server_room``, ``office`` or ``office_lite``.
        office: Office hours of the office profiles (ignored by ``server_room``).
        maximum: The run's slot count; office hours never go above it.
        now: The moment to resolve (aware; naive counts as UTC).
    """
    cap = max(1, int(maximum))
    if profile in OFFICE_PROFILES:
        if office.contains(now):
            return EffectiveLimits(
                profile=profile,
                period="office_hours",
                max_concurrent_files=max(1, min(cap, office.files_at_once(profile))),
                cap_cpu_boost=office.cap_cpu_boost,
            )
        return EffectiveLimits(profile=profile, period="off_hours", max_concurrent_files=cap)
    return EffectiveLimits(profile="server_room", period="server_room", max_concurrent_files=cap)


def effective_limits(
    config: Optional[Mapping], now: Optional[datetime] = None, *, maximum: Optional[int] = None
) -> EffectiveLimits:
    """The file concurrency in force at ``now`` under a stored performance config.

    Args:
        config: The ``performance_config`` document (or the saved model's dump); may be
            empty or None, which means the ``server_room`` profile.
        now: The moment to resolve; the current UTC time when omitted.
        maximum: The run's slot count (its ``max_concurrent_files`` read when it started).
            When omitted the config's ``max_concurrent_files``, else the settings default.
    """
    config = config or {}
    if maximum is None:
        maximum = int(config.get("max_concurrent_files", settings.ingestion_max_concurrent_files))
    return resolve_limits(
        load_profile_from(config),
        office_hours_from(config),
        maximum,
        now or datetime.now(timezone.utc),
    )


class ConcurrencyController:
    """The slot limit of one run, following its load profile and the clock.

    :meth:`current` is the pipeline's ``slot_limit`` callable: synchronous and cheap, called
    before every file. :meth:`refresh_loop` keeps the stored config current while the run is
    going. ``on_change`` is called with the new :class:`EffectiveLimits` whenever the limit or
    the period changes, including the first evaluation.

    Args:
        config: The performance config the run started with.
        max_slots: The run's slot count (its thread pool and worker processes are sized for it).
        now_fn: Clock, for tests.
        on_change: Called on every change of the effective limits.
    """

    def __init__(
        self,
        config: Optional[Mapping],
        max_slots: int,
        *,
        now_fn: Optional[Callable[[], datetime]] = None,
        on_change: Optional[Callable[[EffectiveLimits], None]] = None,
    ) -> None:
        self._max_slots = max(1, int(max_slots))
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._on_change = on_change
        self._last: Optional[EffectiveLimits] = None
        self._profile: LoadProfile = DEFAULT_LOAD_PROFILE
        self._office = OfficeHoursConfig()
        self.update_config(config)

    @property
    def max_slots(self) -> int:
        """The run's slot count."""
        return self._max_slots

    @property
    def last(self) -> Optional[EffectiveLimits]:
        """The limits of the last evaluation, None before the first."""
        return self._last

    def update_config(self, config: Optional[Mapping]) -> None:
        """Take a (re-read) performance config; the next evaluation uses it."""
        self._profile = load_profile_from(config)
        self._office = office_hours_from(config)

    def limits(self) -> EffectiveLimits:
        """Evaluate the limits now; logs and reports a change."""
        limits = resolve_limits(self._profile, self._office, self._max_slots, self._now())
        if limits != self._last:
            previous = self._last
            self._last = limits
            logger.info(
                f"File slots: {limits.max_concurrent_files} of {self._max_slots} "
                f"({limits.label}, load profile {limits.profile})"
                + (f", was {previous.max_concurrent_files}" if previous is not None else "")
            )
            if self._on_change is not None:
                try:
                    self._on_change(limits)
                except Exception as exc:  # a reporting hook must never stop the run
                    logger.warning(f"Load profile change callback failed: {exc}")
        return limits

    def current(self) -> int:
        """The number of file slots in use right now (the pipeline's ``slot_limit``)."""
        return self.limits().max_concurrent_files

    async def refresh_loop(
        self,
        load: Callable[[], Awaitable[Optional[Mapping]]],
        interval_s: float = CONFIG_REFRESH_SECONDS,
    ) -> None:
        """Re-read the stored config every ``interval_s`` seconds until cancelled.

        Args:
            load: Returns the current ``performance_config`` document (None keeps the last one).
            interval_s: Seconds between reads.
        """
        while True:
            await asyncio.sleep(interval_s)
            try:
                doc = await load()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"Could not re-read the ingestion performance config: {exc}")
                continue
            if doc is not None:
                self.update_config(doc)
            self.limits()
