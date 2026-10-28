"""Tests for the ingestion load profiles (``backend/services/ingestion_schedule.py``) and the
pipeline's adjustable file slots (``SlotLimiter`` in ``src/ingestion/ingest.py``).

No converter runs and nothing touches a database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.services.ingestion_schedule import (
    ConcurrencyController,
    EffectiveLimits,
    OfficeHoursConfig,
    effective_limits,
)

VIENNA = ZoneInfo("Europe/Vienna")

OFFICE_CONFIG = {
    "load_profile": "office",
    "max_concurrent_files": 16,
    "thread_pool_workers": 16,
    "office_hours": {
        "timezone": "Europe/Vienna",
        "start": "08:00",
        "end": "18:00",
        "weekdays": [0, 1, 2, 3, 4],
        "max_concurrent_files": 8,
    },
}


def vienna(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=VIENNA)


def limits_at(config, moment: datetime, **kwargs) -> tuple[int, str]:
    limits = effective_limits(config, moment, **kwargs)
    return limits.max_concurrent_files, limits.period


class TestEffectiveLimits:
    def test_server_room_is_the_maximum_all_week(self):
        config = {"load_profile": "server_room", "max_concurrent_files": 16}
        for moment in (vienna(2026, 10, 27, 10), vienna(2026, 10, 31, 12), vienna(2026, 10, 28, 3)):
            assert limits_at(config, moment) == (16, "server_room")

    def test_missing_profile_means_server_room(self):
        assert limits_at({"max_concurrent_files": 16}, vienna(2026, 10, 27, 10)) == (16, "server_room")
        assert limits_at(None, vienna(2026, 10, 27, 10), maximum=4) == (4, "server_room")
        assert limits_at({}, vienna(2026, 10, 27, 10), maximum=4) == (4, "server_room")

    def test_unknown_profile_is_server_room(self):
        config = {**OFFICE_CONFIG, "load_profile": "night_shift"}
        assert limits_at(config, vienna(2026, 10, 27, 10)) == (16, "server_room")

    def test_office_hours_on_a_weekday(self):
        # Tuesday 2026-10-27
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 10)) == (8, "office_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 8)) == (8, "office_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 17, 59)) == (8, "office_hours")

    def test_office_lite_uses_the_lite_number_in_office_hours(self):
        config = {**OFFICE_CONFIG, "load_profile": "office_lite"}
        assert limits_at(config, vienna(2026, 10, 27, 10)) == (4, "office_hours")
        assert limits_at(config, vienna(2026, 10, 27, 20)) == (16, "off_hours")
        assert limits_at(config, vienna(2026, 10, 31, 12)) == (16, "off_hours")
        assert effective_limits(config, vienna(2026, 10, 27, 10)).profile == "office_lite"
        custom = {**config, "office_hours": {**OFFICE_CONFIG["office_hours"], "lite_max_concurrent_files": 2}}
        assert limits_at(custom, vienna(2026, 10, 27, 10)) == (2, "office_hours")
        assert limits_at(config, vienna(2026, 10, 27, 10), maximum=3) == (3, "office_hours")

    def test_weekday_evening_and_night_use_the_maximum(self):
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 18)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 23, 30)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 28, 7, 59)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 28, 8)) == (8, "office_hours")

    def test_weekend_from_friday_evening_to_monday_morning(self):
        # Friday 2026-10-30 ... Monday 2026-11-02
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 30, 17, 59)) == (8, "office_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 30, 18)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 31, 12)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 11, 1, 12)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 11, 2, 7, 59)) == (16, "off_hours")
        assert limits_at(OFFICE_CONFIG, vienna(2026, 11, 2, 8)) == (8, "office_hours")

    def test_hours_are_read_in_the_office_timezone(self):
        # 07:30 UTC on 2026-10-27 is 08:30 in Vienna (CET): office hours
        assert limits_at(OFFICE_CONFIG, datetime(2026, 10, 27, 7, 30, tzinfo=timezone.utc)) == (8, "office_hours")
        # a naive datetime counts as UTC
        assert limits_at(OFFICE_CONFIG, datetime(2026, 10, 27, 7, 30)) == (8, "office_hours")
        # 17:30 UTC is 18:30 in Vienna: evening
        assert limits_at(OFFICE_CONFIG, datetime(2026, 10, 27, 17, 30, tzinfo=timezone.utc)) == (16, "off_hours")

    def test_office_limit_never_exceeds_the_run_maximum(self):
        assert limits_at(OFFICE_CONFIG, vienna(2026, 10, 27, 10), maximum=4) == (4, "office_hours")
        config = {**OFFICE_CONFIG, "max_concurrent_files": 6, "office_hours": {**OFFICE_CONFIG["office_hours"], "max_concurrent_files": 12}}
        assert limits_at(config, vienna(2026, 10, 27, 10)) == (6, "office_hours")
        assert limits_at(config, vienna(2026, 10, 27, 20)) == (6, "off_hours")

    def test_invalid_office_hours_fall_back_to_the_defaults(self):
        config = {**OFFICE_CONFIG, "office_hours": {"start": "25:00", "end": "18:00"}}
        assert limits_at(config, vienna(2026, 10, 27, 10)) == (8, "office_hours")
        assert limits_at(config, vienna(2026, 10, 31, 10)) == (16, "off_hours")

    def test_custom_days_and_hours(self):
        config = {
            **OFFICE_CONFIG,
            "office_hours": {**OFFICE_CONFIG["office_hours"], "start": "07:00", "end": "13:00", "weekdays": [5]},
        }
        assert limits_at(config, vienna(2026, 10, 31, 9)) == (8, "office_hours")   # Saturday morning
        assert limits_at(config, vienna(2026, 10, 31, 13)) == (16, "off_hours")
        assert limits_at(config, vienna(2026, 10, 27, 9)) == (16, "off_hours")     # Tuesday


class TestOfficeHoursConfig:
    def test_defaults(self):
        office = OfficeHoursConfig()
        assert (office.start, office.end, office.weekdays, office.max_concurrent_files, office.lite_max_concurrent_files) == (
            "08:00", "18:00", [0, 1, 2, 3, 4], 8, 4
        )
        assert (office.files_at_once("office"), office.files_at_once("office_lite")) == (8, 4)
        ZoneInfo(office.timezone)  # a real zone

    def test_times_are_normalised_and_days_sorted(self):
        office = OfficeHoursConfig(start="8:05", end="17:0", weekdays=[4, 0, 4, 2])
        assert (office.start, office.end, office.weekdays) == ("08:05", "17:00", [0, 2, 4])

    @pytest.mark.parametrize(
        "field, value",
        [
            ("start", "18:00"),      # not before the 18:00 end
            ("end", "7:61"),
            ("start", "noon"),
            ("weekdays", [7]),
            ("timezone", "Mars/Olympus"),
            ("max_concurrent_files", 0),
        ],
    )
    def test_rejects(self, field, value):
        with pytest.raises(ValidationError):
            OfficeHoursConfig(**{field: value})


class TestConcurrencyController:
    def test_current_follows_the_clock_and_reports_changes(self):
        now = [vienna(2026, 10, 27, 10)]
        seen: list[EffectiveLimits] = []
        controller = ConcurrencyController(OFFICE_CONFIG, 16, now_fn=lambda: now[0], on_change=seen.append)
        assert controller.last is None
        assert controller.current() == 8
        assert controller.current() == 8
        now[0] = vienna(2026, 10, 27, 18, 30)
        assert controller.current() == 16
        assert [(s.max_concurrent_files, s.period) for s in seen] == [(8, "office_hours"), (16, "off_hours")]
        assert controller.last is not None and controller.last.label == "outside office hours"

    def test_run_maximum_caps_the_profile(self):
        controller = ConcurrencyController(OFFICE_CONFIG, 4, now_fn=lambda: vienna(2026, 10, 27, 20))
        assert controller.current() == 4
        assert controller.max_slots == 4

    def test_update_config_switches_the_profile(self):
        controller = ConcurrencyController(OFFICE_CONFIG, 16, now_fn=lambda: vienna(2026, 10, 27, 10))
        assert controller.current() == 8
        controller.update_config({"load_profile": "server_room", "max_concurrent_files": 16})
        assert controller.current() == 16
        assert controller.last is not None and controller.last.period == "server_room"

    def test_a_failing_change_hook_does_not_break_the_run(self):
        def boom(_limits: EffectiveLimits) -> None:
            raise RuntimeError("hook")

        controller = ConcurrencyController(OFFICE_CONFIG, 16, now_fn=lambda: vienna(2026, 10, 27, 10), on_change=boom)
        assert controller.current() == 8

    @pytest.mark.asyncio
    async def test_refresh_loop_picks_up_a_profile_switch(self):
        controller = ConcurrencyController(OFFICE_CONFIG, 16, now_fn=lambda: vienna(2026, 10, 27, 10))
        assert controller.current() == 8

        async def load():
            return {"load_profile": "server_room", "max_concurrent_files": 16}

        task = asyncio.create_task(controller.refresh_loop(load, interval_s=0.01))
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if controller.last is not None and controller.last.period == "server_room":
                    break
            assert controller.current() == 16
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_refresh_loop_survives_a_failing_or_empty_load(self):
        controller = ConcurrencyController(OFFICE_CONFIG, 16, now_fn=lambda: vienna(2026, 10, 27, 10))
        calls = {"n": 0}

        async def load():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("mongo away")
            return None

        task = asyncio.create_task(controller.refresh_loop(load, interval_s=0.01))
        try:
            for _ in range(50):
                await asyncio.sleep(0.01)
                if calls["n"] >= 3:
                    break
            assert calls["n"] >= 3
            assert not task.done()
            assert controller.current() == 8
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


ingest = pytest.importorskip("src.ingestion.ingest")


async def settled(task: asyncio.Task, rounds: int = 5) -> bool:
    """Whether ``task`` finished after a few event-loop turns."""
    for _ in range(rounds):
        await asyncio.sleep(0)
    return task.done()


class TestSlotLimiter:
    @pytest.mark.asyncio
    async def test_static_limit_behaves_like_a_semaphore(self):
        slots = ingest.SlotLimiter(2)
        await slots.acquire()
        await slots.acquire()
        assert slots.active == 2
        third = asyncio.create_task(slots.acquire())
        assert not await settled(third)
        slots.release()
        assert await settled(third)
        assert slots.active == 2

    @pytest.mark.asyncio
    async def test_lowered_limit_takes_effect_as_files_finish(self):
        limit = [4]
        slots = ingest.SlotLimiter(4, lambda: limit[0])
        for _ in range(4):
            await slots.acquire()
        limit[0] = 2
        waiter = asyncio.create_task(slots.acquire())
        assert not await settled(waiter)
        slots.release()  # 3 active, still above the limit
        assert not await settled(waiter)
        slots.release()  # 2 active: the limit is full
        assert not await settled(waiter)
        slots.release()  # 1 active: a slot is free again
        assert await settled(waiter)
        assert slots.active == 2

    @pytest.mark.asyncio
    async def test_raised_limit_admits_waiters_on_kick_and_release(self):
        limit = [1]
        slots = ingest.SlotLimiter(4, lambda: limit[0])
        await slots.acquire()
        first = asyncio.create_task(slots.acquire())
        second = asyncio.create_task(slots.acquire())
        assert not await settled(first) and not second.done()
        limit[0] = 3
        slots.kick()
        assert await settled(first) and second.done()
        assert slots.active == 3
        # the next release admits nobody new beyond the limit
        third = asyncio.create_task(slots.acquire())
        assert not await settled(third)
        slots.release()
        assert await settled(third)
        assert slots.active == 3

    @pytest.mark.asyncio
    async def test_limit_never_exceeds_max_slots_and_never_drops_below_one(self):
        slots = ingest.SlotLimiter(2, lambda: 10)
        assert slots.limit() == 2
        await slots.acquire()
        await slots.acquire()
        third = asyncio.create_task(slots.acquire())
        assert not await settled(third)
        third.cancel()
        with pytest.raises(asyncio.CancelledError):
            await third
        assert ingest.SlotLimiter(4, lambda: 0).limit() == 1

    @pytest.mark.asyncio
    async def test_cancelled_waiter_takes_no_slot(self):
        slots = ingest.SlotLimiter(1)
        await slots.acquire()
        waiter = asyncio.create_task(slots.acquire())
        assert not await settled(waiter)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        slots.release()
        assert slots.active == 0
        await slots.acquire()
        assert slots.active == 1

    @pytest.mark.asyncio
    async def test_waiter_cancelled_while_being_admitted_passes_the_slot_on(self):
        slots = ingest.SlotLimiter(1)
        await slots.acquire()
        first = asyncio.create_task(slots.acquire())
        second = asyncio.create_task(slots.acquire())
        assert not await settled(first) and not second.done()
        slots.release()   # wakes the first waiter ...
        first.cancel()    # ... which is cancelled before it runs
        with pytest.raises(asyncio.CancelledError):
            await first
        assert await settled(second)
        assert slots.active == 1

    def test_broken_limit_function_falls_back_to_the_maximum(self):
        def boom() -> int:
            raise RuntimeError("no config")

        assert ingest.SlotLimiter(3, boom).limit() == 3

    def test_release_without_acquire_is_an_error(self):
        with pytest.raises(ValueError):
            ingest.SlotLimiter(1).release()
