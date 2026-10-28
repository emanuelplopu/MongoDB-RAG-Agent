"""Tests for the host power plan control channel (``backend/services/power_plan_control.py``).

A fake async collection stands in for ``recallhub_telemetry.host_control``; no database runs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pytest

from backend.services.power_plan_control import (
    CAPPED,
    DESIRED_FRESH_SECONDS,
    NORMAL,
    POWER_PLAN_STATUS_ID,
    PowerPlanPublisher,
    desired_id,
    desired_power_plan,
    power_plan_status,
)

VIENNA = ZoneInfo("Europe/Vienna")
OFFICE_CAPPED = {
    "load_profile": "office",
    "max_concurrent_files": 16,
    "office_hours": {"timezone": "Europe/Vienna", "start": "08:00", "end": "18:00", "weekdays": [0, 1, 2, 3, 4],
                     "max_concurrent_files": 8, "lite_max_concurrent_files": 4, "cap_cpu_boost": True},
}


def vienna(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=VIENNA)


class FakeCursor:
    def __init__(self, docs: List[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class FakeCollection:
    """Just enough of an async collection: update_one with $set/upsert, find by _id regex, find_one."""

    def __init__(self) -> None:
        self.docs: Dict[str, dict] = {}
        self.updates: List[dict] = []

    async def update_one(self, filter: dict, update: dict, upsert: bool = False) -> None:
        self.updates.append({"filter": filter, "update": update, "upsert": upsert})
        _id = filter["_id"]
        if _id not in self.docs:
            if not upsert:
                return
            self.docs[_id] = {"_id": _id}
        self.docs[_id].update(update["$set"])

    def find(self, filter: dict) -> FakeCursor:
        prefix = filter["_id"]["$regex"].lstrip("^")
        return FakeCursor([d for k, d in self.docs.items() if k.startswith(prefix)])

    async def find_one(self, filter: dict) -> Optional[dict]:
        return self.docs.get(filter["_id"])


class TestDesiredPowerPlan:
    def test_capped_only_in_office_hours_with_the_flag(self):
        assert desired_power_plan(OFFICE_CAPPED, vienna(2026, 10, 27, 10))[0] == CAPPED
        assert desired_power_plan(OFFICE_CAPPED, vienna(2026, 10, 27, 19))[0] == NORMAL
        assert desired_power_plan(OFFICE_CAPPED, vienna(2026, 10, 31, 10))[0] == NORMAL
        without_flag = {**OFFICE_CAPPED, "office_hours": {**OFFICE_CAPPED["office_hours"], "cap_cpu_boost": False}}
        assert desired_power_plan(without_flag, vienna(2026, 10, 27, 10))[0] == NORMAL
        assert desired_power_plan({"load_profile": "server_room"}, vienna(2026, 10, 27, 10))[0] == NORMAL
        assert desired_power_plan(None, vienna(2026, 10, 27, 10)) == (NORMAL, "server room, load profile server_room")

    def test_reason_names_the_period_and_profile(self):
        desired, reason = desired_power_plan(OFFICE_CAPPED, vienna(2026, 10, 27, 10))
        assert (desired, reason) == (CAPPED, "office hours, load profile office")


class TestPublisher:
    @pytest.mark.asyncio
    async def test_publish_once_upserts_this_tenants_desire(self):
        col = FakeCollection()
        now = [vienna(2026, 10, 27, 10)]

        async def load():
            return OFFICE_CAPPED

        pub = PowerPlanPublisher(col, load, "quellex", now_fn=lambda: now[0])
        assert await pub.publish_once() == (CAPPED, "office hours, load profile office")
        doc = col.docs[desired_id("quellex")]
        assert (doc["tenant"], doc["desired"], doc["source"]) == ("quellex", CAPPED, "api")
        assert doc["updated_at"] == now[0]
        assert pub.last == (CAPPED, "office hours, load profile office")

        now[0] = vienna(2026, 10, 27, 18, 30)
        assert (await pub.publish_once())[0] == NORMAL
        assert col.docs[desired_id("quellex")]["desired"] == NORMAL

    @pytest.mark.asyncio
    async def test_unreadable_config_publishes_normal(self):
        col = FakeCollection()

        async def load():
            raise RuntimeError("mongo away")

        pub = PowerPlanPublisher(col, load, "quellex", now_fn=lambda: vienna(2026, 10, 27, 10))
        assert (await pub.publish_once())[0] == NORMAL

    @pytest.mark.asyncio
    async def test_loop_publishes_and_stop_withdraws(self):
        col = FakeCollection()

        async def load():
            return OFFICE_CAPPED

        pub = PowerPlanPublisher(col, load, "quellex", interval_s=5, now_fn=lambda: vienna(2026, 10, 27, 10))
        await pub.start()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if desired_id("quellex") in col.docs:
                break
        assert col.docs[desired_id("quellex")]["desired"] == CAPPED
        await pub.stop()
        assert col.docs[desired_id("quellex")]["desired"] == NORMAL
        assert col.docs[desired_id("quellex")]["reason"] == "backend stopped"


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_reports_every_tenant_and_the_agent(self):
        col = FakeCollection()
        now = datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc)   # 10:00 in Vienna
        col.docs[desired_id("quellex")] = {"_id": desired_id("quellex"), "tenant": "quellex", "desired": CAPPED,
                                           "reason": "office hours, load profile office", "updated_at": now - timedelta(seconds=30)}
        col.docs[desired_id("recallhub")] = {"_id": desired_id("recallhub"), "tenant": "recallhub", "desired": CAPPED,
                                             "reason": "old", "updated_at": now - timedelta(seconds=DESIRED_FRESH_SECONDS + 1)}
        col.docs[POWER_PLAN_STATUS_ID] = {"_id": POWER_PLAN_STATUS_ID, "state": "capped", "desired": CAPPED,
                                          "max_state_ac": 99, "boost_mode_ac": 0, "applied_at": now - timedelta(minutes=5),
                                          "updated_at": now - timedelta(seconds=10), "agent_version": "1.1.0"}
        status = await power_plan_status(col, "quellex", OFFICE_CAPPED, now)
        assert (status.desired, status.reason) == (CAPPED, "office hours, load profile office")
        by_tenant = {t.tenant: t for t in status.tenants}
        assert by_tenant["quellex"].fresh is True
        assert by_tenant["recallhub"].fresh is False
        assert status.agent is not None and status.agent.state == "capped" and status.agent.max_state_ac == 99
        assert status.agent_seen is True

    @pytest.mark.asyncio
    async def test_status_without_agent(self):
        col = FakeCollection()
        status = await power_plan_status(col, "quellex", None, datetime(2026, 10, 27, 9, tzinfo=timezone.utc))
        assert (status.desired, status.agent, status.agent_seen, status.tenants) == (NORMAL, None, False, [])

    @pytest.mark.asyncio
    async def test_stale_agent_report_is_not_seen(self):
        col = FakeCollection()
        now = datetime(2026, 10, 27, 9, tzinfo=timezone.utc)
        col.docs[POWER_PLAN_STATUS_ID] = {"_id": POWER_PLAN_STATUS_ID, "state": "normal", "updated_at": now - timedelta(minutes=10)}
        status = await power_plan_status(col, "quellex", None, now)
        assert status.agent is not None and status.agent.state == "normal"
        assert status.agent_seen is False
