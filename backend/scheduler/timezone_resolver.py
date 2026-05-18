"""DST-safe timezone-aware cron evaluation for the scheduler."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional

from zoneinfo import ZoneInfo

from backend.scheduler.models import StrategySchedule

logger = logging.getLogger(__name__)


@dataclass
class CronFields:
    """Parsed representation of a 5-field cron expression."""

    minutes: set[int] = field(default_factory=set)
    hours: set[int] = field(default_factory=set)
    days_of_month: set[int] = field(default_factory=set)
    months: set[int] = field(default_factory=set)
    days_of_week: set[int] = field(default_factory=set)  # 0=Mon … 6=Sun


class ScheduleTimezoneResolver:
    """Evaluates when the next scheduler run should occur,
    handling DST transitions safely.

    Key behaviors:
    - Spring-forward (clocks skip ahead): If scheduled time falls in the
      non-existent gap, skip that occurrence (next valid occurrence is used).
    - Fall-back (clocks repeat): If scheduled time falls in the ambiguous
      period, use date-granularity dedup key to prevent double-execution.
    - Allowed window enforcement: Only returns times within the configured
      allowed_window_start..allowed_window_end range (local time).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_next_run(
        self,
        schedule: StrategySchedule,
        now: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Return the next valid UTC run time for *schedule*, or ``None``.

        Steps:
        1. Parse the cron expression.
        2. Find the next matching local time in the schedule's timezone.
        3. Verify DST safety (skip spring-forward gaps).
        4. Verify the result is inside the allowed execution window.
        5. Convert to UTC and return.
        """
        tz = ZoneInfo(schedule.timezone)
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        cron = self._parse_cron(schedule.cron)
        candidate = self._next_cron_match(cron, after=now, tz=tz)
        if candidate is None:
            return None

        if not self.is_in_allowed_window(candidate, schedule):
            # Advance past the disallowed window and try again (once).
            window_start = self._parse_hhmm(schedule.allowed_window_start)
            local = candidate.astimezone(tz)
            next_window = local.replace(
                hour=window_start.hour,
                minute=window_start.minute,
                second=0,
                microsecond=0,
            )
            if next_window <= local:
                next_window += timedelta(days=1)
            candidate = self._next_cron_match(
                cron, after=next_window.astimezone(timezone.utc), tz=tz
            )
            if candidate is None or not self.is_in_allowed_window(candidate, schedule):
                return None

        return candidate.astimezone(timezone.utc)

    # ------------------------------------------------------------------

    @staticmethod
    def to_local(dt_utc: datetime, tz_name: str) -> datetime:
        """Convert a UTC datetime to local wall-clock time in *tz_name*.

        Uses :class:`zoneinfo.ZoneInfo` so DST transitions are honoured
        correctly: converting UTC -> local is unambiguous even during
        spring-forward gaps and fall-back overlaps.

        Args:
            dt_utc: A timezone-aware datetime. Naive inputs are assumed
                to already be in UTC.
            tz_name: An IANA timezone identifier (e.g. ``"Europe/Vienna"``).

        Returns:
            A timezone-aware datetime expressed in *tz_name*.

        Raises:
            zoneinfo.ZoneInfoNotFoundError: If *tz_name* is not a valid
                IANA timezone.
        """
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(ZoneInfo(tz_name))

    # ------------------------------------------------------------------

    def is_in_allowed_window(
        self,
        dt: datetime,
        schedule: StrategySchedule,
    ) -> bool:
        """Check whether *dt* falls inside the allowed execution window.

        The window is defined by ``allowed_window_start`` / ``…_end`` in
        **local** time (``schedule.timezone``).  Overnight windows that wrap
        past midnight (e.g. 22:00–06:00) are handled correctly.
        """
        tz = ZoneInfo(schedule.timezone)
        local = dt.astimezone(tz)
        local_time = local.time()

        win_start = self._parse_hhmm(schedule.allowed_window_start)
        win_end = self._parse_hhmm(schedule.allowed_window_end)

        if win_start <= win_end:
            # Same-day window (e.g. 08:00 – 17:00)
            return win_start <= local_time <= win_end
        # Overnight window (e.g. 22:00 – 06:00)
        return local_time >= win_start or local_time <= win_end

    # ------------------------------------------------------------------

    @staticmethod
    def get_dedup_key(schedule_id: str, dt: datetime, tz: ZoneInfo) -> str:
        """Date-granularity dedup key to avoid double-execution on fall-back.

        During a fall-back transition the same local time appears twice, but
        the *date* is identical.  Using ``schedule_id:YYYY-MM-DD`` as a
        deduplication key ensures only one execution per calendar day.
        """
        local = dt.astimezone(tz)
        return f"{schedule_id}:{local.strftime('%Y-%m-%d')}"

    # ------------------------------------------------------------------
    # Cron parsing
    # ------------------------------------------------------------------

    def _parse_cron(self, cron_expr: str) -> CronFields:
        """Parse a standard 5-field cron expression into :class:`CronFields`.

        Supported tokens per field:
        - ``*``          — all valid values
        - ``5``          — single value
        - ``1-5``        — inclusive range
        - ``1,3,5``      — explicit list
        - ``*/15``       — step over full range
        - ``1-5/2``      — step over sub-range
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(
                f"Expected 5-field cron expression, got {len(parts)} fields: "
                f"{cron_expr!r}"
            )

        ranges = [
            (0, 59),   # minute
            (0, 23),   # hour
            (1, 31),   # day of month
            (1, 12),   # month
            (0, 6),    # day of week (0=Mon … 6=Sun, matching Python weekday())
        ]

        parsed: list[set[int]] = []
        for token, (lo, hi) in zip(parts, ranges):
            parsed.append(self._expand_cron_field(token, lo, hi))

        return CronFields(
            minutes=parsed[0],
            hours=parsed[1],
            days_of_month=parsed[2],
            months=parsed[3],
            days_of_week=parsed[4],
        )

    @staticmethod
    def _expand_cron_field(token: str, lo: int, hi: int) -> set[int]:
        """Expand a single cron field token into a set of int values."""
        result: set[int] = set()
        for item in token.split(","):
            step: Optional[int] = None
            if "/" in item:
                item, step_str = item.split("/", 1)
                step = int(step_str)

            if item == "*":
                start, end = lo, hi
            elif "-" in item:
                a, b = item.split("-", 1)
                start, end = int(a), int(b)
            else:
                val = int(item)
                if step is not None:
                    start, end = val, hi
                else:
                    result.add(val)
                    continue

            if step is None:
                step = 1
            result.update(range(start, end + 1, step))

        return result

    # ------------------------------------------------------------------
    # Next-match iterator
    # ------------------------------------------------------------------

    def _next_cron_match(
        self,
        cron: CronFields,
        after: datetime,
        tz: ZoneInfo,
        max_lookahead_days: int = 7,
    ) -> Optional[datetime]:
        """Find the next datetime matching *cron* in timezone *tz*.

        Iterates minute-by-minute starting one minute after *after*, up to
        *max_lookahead_days*.  Times that fall into a spring-forward gap
        (non-existent local times) are silently skipped.
        """
        local_start = after.astimezone(tz)
        # Advance to the next whole minute.
        candidate = (local_start + timedelta(minutes=1)).replace(
            second=0, microsecond=0
        )
        deadline = local_start + timedelta(days=max_lookahead_days)

        while candidate <= deadline:
            if not self._cron_matches(cron, candidate):
                candidate = self._advance(candidate, cron)
                continue

            # DST safety: verify the local time actually exists.
            if self._is_in_spring_forward_gap(candidate, tz):
                logger.debug(
                    "Skipping non-existent time %s in %s (spring-forward gap)",
                    candidate.strftime("%Y-%m-%d %H:%M"),
                    tz,
                )
                candidate += timedelta(minutes=1)
                continue

            # Re-attach proper tz info (candidate may be naïve-ish after
            # arithmetic) and return as aware datetime.
            aware = candidate.replace(fold=0)
            return aware

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cron_matches(cron: CronFields, dt: datetime) -> bool:
        """Return True if *dt* matches all cron fields."""
        return (
            dt.minute in cron.minutes
            and dt.hour in cron.hours
            and dt.day in cron.days_of_month
            and dt.month in cron.months
            and dt.weekday() in cron.days_of_week
        )

    @staticmethod
    def _advance(dt: datetime, cron: CronFields) -> datetime:
        """Heuristic skip: jump ahead when the current minute clearly
        cannot match.  Falls back to +1 minute in ambiguous cases."""
        # If hour doesn't match, skip to start of next candidate hour.
        if dt.hour not in cron.hours:
            next_hour = None
            for h in sorted(cron.hours):
                if h > dt.hour:
                    next_hour = h
                    break
            if next_hour is not None:
                return dt.replace(hour=next_hour, minute=0)
            # Wrap to next day, first candidate hour.
            first_hour = min(cron.hours)
            return (dt + timedelta(days=1)).replace(hour=first_hour, minute=0)

        # Hour matches but minute doesn't — skip to next candidate minute.
        next_min = None
        for m in sorted(cron.minutes):
            if m > dt.minute:
                next_min = m
                break
        if next_min is not None:
            return dt.replace(minute=next_min)
        # Wrap to next candidate hour.
        next_hour = None
        for h in sorted(cron.hours):
            if h > dt.hour:
                next_hour = h
                break
        if next_hour is not None:
            return dt.replace(hour=next_hour, minute=min(cron.minutes))
        first_hour = min(cron.hours)
        return (dt + timedelta(days=1)).replace(
            hour=first_hour, minute=min(cron.minutes)
        )

    @staticmethod
    def _is_in_spring_forward_gap(dt: datetime, tz: ZoneInfo) -> bool:
        """Detect if *dt* (a local time in *tz*) falls in a DST gap.

        A local time is non-existent when constructing it with ``fold=0``
        and ``fold=1`` yields different UTC offsets **and** the wall-clock
        time shifts forward (the offset *increases*).
        """
        try:
            naive = dt.replace(tzinfo=None)
            t0 = naive.replace(tzinfo=tz, fold=0)  # type: ignore[call-overload]
            t1 = naive.replace(tzinfo=tz, fold=1)  # type: ignore[call-overload]
            off0 = t0.utcoffset()
            off1 = t1.utcoffset()
            if off0 is None or off1 is None:
                return False
            # In a spring-forward gap the two folds map to different UTC
            # offsets and fold=0 picks the *pre-transition* offset while
            # fold=1 picks the *post-transition* offset.  The post-transition
            # offset is larger (clocks moved forward).
            return off0 != off1 and off1 > off0
        except Exception:
            return False

    @staticmethod
    def _parse_hhmm(value: str) -> dt_time:
        """Parse an ``HH:MM`` string into a :class:`datetime.time`."""
        parts = value.strip().split(":")
        return dt_time(hour=int(parts[0]), minute=int(parts[1]))
