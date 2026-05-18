"""Shared in-process async Mongo fakes for ``backend/tests/strategy/``.

Originally introduced by Task 60 (``test_spec_store.py``) and lifted here
in Task 61 so that scheduler-store and run-now tests can reuse the same
collection-shape semantics without circular imports between sibling test
modules.

The fakes implement only the small slice of the async Mongo collection
API actually exercised by :mod:`backend.agent.strategy.spec_store` and
:mod:`backend.agent.strategy.scheduler_store`. They intentionally do not
attempt to be a general-purpose Mongo emulator.
"""

from __future__ import annotations

import copy
from typing import Any, Iterator, Optional

__all__ = ["_DeleteResult", "_AsyncCursor", "_FakeAsyncCollection", "_FakeAsyncDB"]


class _DeleteResult:
    """Lightweight stand-in for PyMongo's ``DeleteResult`` / ``UpdateResult``."""

    def __init__(self, deleted: int) -> None:
        self.deleted_count = deleted


class _AsyncCursor:
    """Minimal async iterator + ``sort`` wrapper over an in-memory list."""

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def sort(self, key: str, direction: int = 1) -> "_AsyncCursor":
        self._docs.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
        return self

    def __aiter__(self) -> "_AsyncCursor":
        self._iter: Iterator[dict[str, Any]] = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:  # noqa: PERF203 - simple test fake
            raise StopAsyncIteration from exc


class _FakeAsyncCollection:
    """In-process stand-in for an async Mongo collection.

    Supports the small subset of the API surface that the strategy stores
    actually invoke: ``find_one``, ``find`` (with ``.sort``),
    ``replace_one``, ``insert_one``, ``delete_one``, ``delete_many``,
    ``aggregate`` for the latest-per-key grouping pipeline, and
    ``create_index``.
    """

    def __init__(self) -> None:
        self._docs: list[dict[str, Any]] = []
        self._auto_id = 0
        self.created_indexes: list[Any] = []

    # ── Helpers ─────────────────────────────────────────────────────────

    def _matches(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if "." in key:
                parts = key.split(".")
                value: Any = doc
                for part in parts:
                    if not isinstance(value, dict):
                        value = None
                        break
                    value = value.get(part)
            else:
                value = doc.get(key)
            if isinstance(expected, dict):
                # Tiny operator support for queries used by scheduler /
                # report aggregations: $ne, $gt, $gte, $lt, $lte, $in, $exists.
                ok = True
                for op, operand in expected.items():
                    if op == "$ne":
                        if value == operand:
                            ok = False
                            break
                    elif op == "$gt":
                        if value is None or not (value > operand):
                            ok = False
                            break
                    elif op == "$gte":
                        if value is None or not (value >= operand):
                            ok = False
                            break
                    elif op == "$lt":
                        if value is None or not (value < operand):
                            ok = False
                            break
                    elif op == "$lte":
                        if value is None or not (value <= operand):
                            ok = False
                            break
                    elif op == "$in":
                        if value not in operand:
                            ok = False
                            break
                    elif op == "$exists":
                        present = key in doc if "." not in key else value is not None
                        if bool(operand) != bool(present):
                            ok = False
                            break
                    else:
                        # Unknown operators -> treat as no match for safety.
                        ok = False
                        break
                if not ok:
                    return False
                continue
            if value != expected:
                return False
        return True

    def _filter(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return [d for d in self._docs if self._matches(d, query)]

    # ── Mongo-ish methods ───────────────────────────────────────────────

    async def find_one(self, query: dict[str, Any]) -> Optional[dict[str, Any]]:
        matches = self._filter(query)
        return copy.deepcopy(matches[0]) if matches else None

    def find(self, query: dict[str, Any]) -> _AsyncCursor:
        return _AsyncCursor([copy.deepcopy(d) for d in self._filter(query)])

    async def insert_one(self, doc: dict[str, Any]) -> Any:
        self._auto_id += 1
        stored = copy.deepcopy(doc)
        stored.setdefault("_id", self._auto_id)
        self._docs.append(stored)
        return _DeleteResult(1)

    async def replace_one(
        self,
        query: dict[str, Any],
        doc: dict[str, Any],
        upsert: bool = False,
    ) -> Any:
        for i, existing in enumerate(self._docs):
            if self._matches(existing, query):
                stored = copy.deepcopy(doc)
                stored["_id"] = existing.get("_id")
                self._docs[i] = stored
                return _DeleteResult(1)
        if upsert:
            await self.insert_one(doc)
            return _DeleteResult(1)
        return _DeleteResult(0)

    async def delete_one(self, query: dict[str, Any]) -> _DeleteResult:
        for i, d in enumerate(self._docs):
            if self._matches(d, query):
                del self._docs[i]
                return _DeleteResult(1)
        return _DeleteResult(0)

    async def delete_many(self, query: dict[str, Any]) -> _DeleteResult:
        before = len(self._docs)
        self._docs = [d for d in self._docs if not self._matches(d, query)]
        return _DeleteResult(before - len(self._docs))

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> _DeleteResult:
        """Apply a tiny subset of update operators (``$set`` / ``$addToSet`` / ``$push`` / ``$inc``).

        Only the operators actually used by the strategy stores are
        implemented; unknown operators raise ``KeyError`` so callers
        notice missing fake support immediately rather than silently
        no-oping.
        """
        for i, existing in enumerate(self._docs):
            if not self._matches(existing, query):
                continue
            for op, payload in update.items():
                if op == "$set":
                    for k, v in payload.items():
                        existing[k] = copy.deepcopy(v)
                elif op == "$addToSet":
                    for k, v in payload.items():
                        bucket = existing.setdefault(k, [])
                        if v not in bucket:
                            bucket.append(copy.deepcopy(v))
                elif op == "$push":
                    for k, v in payload.items():
                        bucket = existing.setdefault(k, [])
                        bucket.append(copy.deepcopy(v))
                elif op == "$inc":
                    for k, v in payload.items():
                        existing[k] = int(existing.get(k, 0)) + int(v)
                else:
                    raise KeyError(f"_FakeAsyncCollection.update_one: unsupported operator {op!r}")
            self._docs[i] = existing
            return _DeleteResult(1)
        if upsert:
            seed: dict[str, Any] = {}
            for k, v in query.items():
                if not isinstance(v, dict):
                    seed[k] = v
            for op, payload in update.items():
                if op == "$set":
                    for k, v in payload.items():
                        seed[k] = copy.deepcopy(v)
                elif op == "$addToSet":
                    for k, v in payload.items():
                        seed.setdefault(k, []).append(copy.deepcopy(v))
                elif op == "$push":
                    for k, v in payload.items():
                        seed.setdefault(k, []).append(copy.deepcopy(v))
                elif op == "$inc":
                    for k, v in payload.items():
                        seed[k] = int(seed.get(k, 0)) + int(v)
            await self.insert_one(seed)
            return _DeleteResult(1)
        return _DeleteResult(0)

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        self.created_indexes.append((args, kwargs))
        return "ix_fake"

    def aggregate(self, pipeline: list[dict[str, Any]]) -> _AsyncCursor:
        docs = [copy.deepcopy(d) for d in self._docs]
        for stage in pipeline:
            if "$match" in stage:
                docs = [d for d in docs if self._matches(d, stage["$match"])]
            elif "$sort" in stage:
                key, direction = next(iter(stage["$sort"].items()))
                docs.sort(key=lambda d: d.get(key, 0), reverse=direction < 0)
            elif "$group" in stage:
                key = stage["$group"]["_id"].lstrip("$")
                seen: dict[Any, dict[str, Any]] = {}
                for d in docs:
                    k = d.get(key)
                    if k not in seen:
                        seen[k] = {"_id": k, "doc": copy.deepcopy(d)}
                docs = list(seen.values())
            elif "$replaceRoot" in stage:
                docs = [d["doc"] for d in docs]
            elif "$limit" in stage:
                docs = docs[: int(stage["$limit"])]
        return _AsyncCursor(docs)


class _FakeAsyncDB:
    """Dict-like async DB exposing :class:`_FakeAsyncCollection` per key."""

    def __init__(self) -> None:
        self._cols: dict[str, _FakeAsyncCollection] = {}

    def __getitem__(self, name: str) -> _FakeAsyncCollection:
        if name not in self._cols:
            self._cols[name] = _FakeAsyncCollection()
        return self._cols[name]
