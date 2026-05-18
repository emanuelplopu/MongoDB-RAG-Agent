"""Strategy spec CRUD API with optimistic locking.

Provides REST endpoints for managing :class:`StrategySpec` definitions
with persistent (Mongo-backed) storage and an optional in-memory
fallback. Implements optimistic concurrency control via the
``X-Expected-Version`` header.

Storage is provided through dependency injection: when ``app.state.db``
is available (FastAPI lifespan), a
:class:`backend.agent.strategy.spec_store.MongoSpecStore` is used;
otherwise the request falls back to a process-local
:class:`InMemorySpecStore` mirroring the legacy module-level
``_specs_store`` shim that was retained for backwards-compatibility
with the existing promotion-manager test suite.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.promotion_manager import (
    InvalidStateTransitionError,
    PromotionManager,
    RollbackCooldownError,
)
from backend.agent.strategy.spec_loader import validate_spec
from backend.agent.strategy.spec_store import (
    InMemorySpecStore,
    MongoSpecStore,
    SpecStore,
    VersionConflictError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategy-specs", tags=["strategy-specs"])


# ═══════════════════════════════════════════════════════════════════════════════
# Legacy in-memory shim (kept for promotion-manager + existing tests)
# ═══════════════════════════════════════════════════════════════════════════════
#
# This dict is still consumed by ``backend.agent.strategy.promotion_manager``
# via a lazy import. We keep it as the backing store for the default
# ``InMemorySpecStore`` so promotion lifecycle endpoints continue to work
# unchanged when Mongo is not wired in. Mongo-backed deployments still see
# this shim populated lazily for the duration of the process; full
# DB-backed promotion persistence is tracked as a follow-up task.
_specs_store: dict[str, list[dict[str, Any]]] = {}


# ═══════════════════════════════════════════════════════════════════════════════
# Response models
# ═══════════════════════════════════════════════════════════════════════════════


class SpecValidationResponse(BaseModel):
    """Result of running ``validate_spec`` on a stored spec."""

    valid: bool
    errors: list[str] = Field(default_factory=list)


class SpecVersionInfo(BaseModel):
    """Metadata for a single stored spec snapshot."""

    version: str
    version_counter: int
    status: str
    created_at: datetime
    spec_hash: str


class SpecHistoryResponse(BaseModel):
    """Full version history for a single spec."""

    spec_id: str
    versions: list[SpecVersionInfo]


class SpecListResponse(BaseModel):
    """Response for the list endpoint."""

    specs: list[dict[str, Any]]
    total: int


class SpecDeleteResponse(BaseModel):
    """Response for the soft-delete endpoint."""

    spec_id: str
    status: str
    message: str


class PromotionRequest(BaseModel):
    """Body for promote/deprecate/archive endpoints."""

    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


class RollbackRequest(BaseModel):
    """Body for the rollback endpoint."""

    target_version: str = Field(..., min_length=1)
    actor: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


# ═══════════════════════════════════════════════════════════════════════════════
# Dependency injection
# ═══════════════════════════════════════════════════════════════════════════════


_default_in_memory_store: InMemorySpecStore = InMemorySpecStore(snapshots=_specs_store)


def _resolve_mongo_db(request: Request) -> Any:
    """Return an async Mongo database handle from ``app.state.db`` (or None).

    Supports both the project's :class:`DatabaseManager` wrapper (exposing
    ``.db``) and a raw async database handle.
    """
    db_state = getattr(request.app.state, "db", None) if hasattr(request, "app") else None
    if db_state is None:
        return None
    return getattr(db_state, "db", db_state)


def get_spec_store(request: Request) -> SpecStore:
    """FastAPI dependency that returns the appropriate :class:`SpecStore`.

    Resolution order:

    1. ``request.app.state.spec_store`` — the process-wide
       :class:`MongoSpecStore` instance bound during the FastAPI
       lifespan (Phase 5 / Task 65). Reusing it keeps the spec router,
       the promotion manager, and the strategy spec selector aligned
       on a single in-memory mirror per process.
    2. A freshly built :class:`MongoSpecStore` when only the raw
       database handle is available (legacy code paths / tests).
    3. The shared process-local :class:`InMemorySpecStore` wrapping
       ``_specs_store`` when no database handle is exposed.
    """
    shared = (
        getattr(request.app.state, "spec_store", None)
        if hasattr(request, "app")
        else None
    )
    if shared is not None:
        return shared
    db = _resolve_mongo_db(request)
    if db is None:
        return _default_in_memory_store
    return MongoSpecStore(db)


_promotion_manager = PromotionManager(store=_specs_store)


def get_promotion_manager(
    store: SpecStore = Depends(get_spec_store),
) -> PromotionManager:
    """Return a :class:`PromotionManager` bound to the resolved spec store.

    The manager mutates a dict-of-list snapshot view in place. Both
    :class:`InMemorySpecStore` and :class:`MongoSpecStore` expose this
    view via ``.snapshots`` so the same manager class works against
    either backend without modification.
    """
    snapshots = store.snapshots
    if snapshots is _specs_store:
        return _promotion_manager
    return PromotionManager(store=snapshots)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _compute_spec_hash(spec_data: dict[str, Any]) -> str:
    """Return a deterministic sha256 hash for a spec payload."""
    canonical = json.dumps(spec_data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_spec(spec: StrategySpec) -> dict[str, Any]:
    """Serialize a StrategySpec to a JSON-compatible dict."""
    return json.loads(spec.model_dump_json())


def _parse_spec(payload: dict[str, Any]) -> StrategySpec:
    """Parse a request payload into a StrategySpec, raising 400 on failure."""
    try:
        return StrategySpec(**payload)
    except Exception as exc:  # pydantic.ValidationError or others
        raise HTTPException(
            status_code=400,
            detail=f"Invalid StrategySpec payload: {exc}",
        ) from exc


def _make_snapshot(
    spec: StrategySpec,
    version_counter: int,
    created_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build a snapshot dict from a StrategySpec."""
    spec_data = _serialize_spec(spec)
    spec_hash = _compute_spec_hash(spec_data)
    spec_data["spec_hash"] = spec_hash
    return {
        "spec_data": spec_data,
        "version": spec.version,
        "version_counter": version_counter,
        "status": spec.status,
        "created_at": created_at or datetime.utcnow(),
        "spec_hash": spec_hash,
    }


def _snapshot_to_list_entry(spec_id: str, snap: dict[str, Any]) -> dict[str, Any]:
    """Project a snapshot into the list-endpoint response envelope."""
    return {
        "strategy_id": spec_id,
        "version": snap["version"],
        "version_counter": snap["version_counter"],
        "status": snap["status"],
        "created_at": snap["created_at"],
        "spec_hash": snap["spec_hash"],
        "spec": snap["spec_data"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("", status_code=201)
async def create_spec(
    payload: dict[str, Any],
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Create a new strategy spec (version 1)."""
    spec = _parse_spec(payload)

    existing = await store.get(spec.strategy_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Spec '{spec.strategy_id}' already exists; use PUT to update",
        )

    errors = validate_spec(spec)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Strategy spec validation failed", "errors": errors},
        )

    snapshot = _make_snapshot(spec, version_counter=1)
    try:
        await store.upsert(snapshot, expected_version=0)
    except VersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Version conflict on create",
                "expected_version": exc.expected,
                "current_version": exc.current,
            },
        ) from exc
    logger.info(
        "Created strategy spec %s (version=%s, counter=1)",
        spec.strategy_id,
        spec.version,
    )
    return snapshot["spec_data"]


@router.get("")
async def list_specs(
    status: Optional[str] = Query(default=None),
    capability_id: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    store: SpecStore = Depends(get_spec_store),
) -> SpecListResponse:
    """List the latest version of each stored spec, with optional filters."""
    latest_snaps = await store.list(
        status=status,
        capability_id=capability_id,
        tenant_id=tenant_id,
        limit=limit,
    )
    results = [
        _snapshot_to_list_entry(snap["spec_data"]["strategy_id"], snap)
        for snap in latest_snaps
    ]
    return SpecListResponse(specs=results, total=len(results))


@router.get("/{spec_id}")
async def get_spec(
    spec_id: str,
    version: Optional[str] = Query(
        default=None,
        description="Optional semantic version. Defaults to latest.",
    ),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Return a single spec snapshot by id (and optional version)."""
    snap = await store.get(spec_id, version)
    if snap is None:
        if version is None:
            raise HTTPException(
                status_code=404, detail=f"Spec '{spec_id}' not found"
            )
        raise HTTPException(
            status_code=404,
            detail=f"Spec '{spec_id}' has no version '{version}'",
        )
    return _snapshot_to_list_entry(spec_id, snap)


@router.put("/{spec_id}")
async def update_spec(
    spec_id: str,
    payload: dict[str, Any],
    x_expected_version: int = Header(
        ...,
        alias="X-Expected-Version",
        description="Expected current version_counter for optimistic locking.",
    ),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Update a spec, enforcing optimistic locking on the version counter."""
    current = await store.get(spec_id)
    if current is None:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    current_counter = int(current["version_counter"])
    if x_expected_version != current_counter:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Version conflict: spec was modified by another writer",
                "expected_version": x_expected_version,
                "current_version": current_counter,
            },
        )

    new_spec = _parse_spec(payload)
    if new_spec.strategy_id != spec_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path strategy_id '{spec_id}' does not match payload "
                f"strategy_id '{new_spec.strategy_id}'"
            ),
        )

    errors = validate_spec(new_spec)
    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Strategy spec validation failed", "errors": errors},
        )

    snapshot = _make_snapshot(new_spec, version_counter=current_counter + 1)
    try:
        await store.upsert(snapshot, expected_version=current_counter)
    except VersionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Version conflict: spec was modified by another writer",
                "expected_version": exc.expected,
                "current_version": exc.current,
            },
        ) from exc
    logger.info(
        "Updated strategy spec %s (version=%s, counter=%d)",
        spec_id,
        new_spec.version,
        snapshot["version_counter"],
    )
    return snapshot["spec_data"]


@router.delete("/{spec_id}")
async def archive_spec(
    spec_id: str,
    store: SpecStore = Depends(get_spec_store),
) -> SpecDeleteResponse:
    """Soft-delete a spec by setting the latest version's status to archived."""
    latest = await store.get(spec_id)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    latest["status"] = "archived"
    latest["spec_data"]["status"] = "archived"
    try:
        await store.upsert(latest, expected_version=None)
    except VersionConflictError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logger.info("Archived strategy spec %s", spec_id)
    return SpecDeleteResponse(
        spec_id=spec_id,
        status="archived",
        message=f"Spec '{spec_id}' archived",
    )


@router.post("/{spec_id}/validate")
async def validate_stored_spec(
    spec_id: str,
    store: SpecStore = Depends(get_spec_store),
) -> SpecValidationResponse:
    """Run ``validate_spec`` against the latest stored snapshot."""
    snap = await store.get(spec_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")
    try:
        spec = StrategySpec(**snap["spec_data"])
    except Exception as exc:
        return SpecValidationResponse(
            valid=False,
            errors=[f"Failed to deserialize stored spec: {exc}"],
        )

    errors = validate_spec(spec)
    return SpecValidationResponse(valid=not errors, errors=errors)


@router.get("/{spec_id}/history")
async def get_spec_history(
    spec_id: str,
    store: SpecStore = Depends(get_spec_store),
) -> SpecHistoryResponse:
    """Return metadata for every stored version of a spec."""
    versions = await store.list_versions(spec_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    history = [
        SpecVersionInfo(
            version=v["version"],
            version_counter=v["version_counter"],
            status=v["status"],
            created_at=v["created_at"],
            spec_hash=v["spec_hash"],
        )
        for v in versions
    ]
    return SpecHistoryResponse(spec_id=spec_id, versions=history)


# ═══════════════════════════════════════════════════════════════════════════════
# Promotion / rollback endpoints
# ═══════════════════════════════════════════════════════════════════════════════


def _spec_response(spec: StrategySpec) -> dict[str, Any]:
    """Serialize a StrategySpec for endpoint responses."""
    return json.loads(spec.model_dump_json())


def _invalidate_selector_cache(spec: StrategySpec) -> None:
    """Drop selector cache entries affected by a lifecycle transition.

    The shared :class:`StrategySpecSelector` keeps a 5-minute TTL cache
    keyed by ``(capability_id, tenant_id)``. After a promote / deprecate /
    archive / rollback the cache must be invalidated for the affected
    capability so the new active spec is picked up immediately.
    """
    try:
        from backend.agent.coordinator import get_spec_selector

        selector = get_spec_selector()
        if selector is None:
            return
        selector.clear_cache(capability_id=spec.capability_id)
    except Exception as exc:  # noqa: BLE001 - cache invalidation is best-effort
        logger.warning("Failed to invalidate spec selector cache: %s", exc)


async def _flush_promotion_change(
    store: SpecStore, spec: StrategySpec, *, op: str
) -> None:
    """Persist a promotion-manager mutation back to the spec store.

    Promotion lifecycle changes (promote/deprecate/archive/rollback) mutate
    the in-memory snapshot mirror in place. For Mongo-backed deployments
    that mirror is not auto-flushed by the optimistic-locking ``upsert``
    path, so we explicitly call :meth:`SpecStore.flush_snapshot` after
    each transition. The flush is best-effort: failures are logged but
    do not propagate, since the request's in-process state is already
    consistent and the next mutation will retry the persistence.

    Args:
        store: Resolved :class:`SpecStore` for this request.
        spec: The spec returned by the promotion manager (post-mutation).
        op: Operation name used for log context (e.g. ``"promote"``).
    """
    try:
        await store.flush_snapshot(spec.strategy_id, spec.version)
    except Exception as exc:  # noqa: BLE001 - flush is best-effort durability
        logger.warning(
            "Failed to flush %s mutation for %s@%s: %s",
            op,
            spec.strategy_id,
            spec.version,
            exc,
        )


@router.post("/{spec_id}/versions/{version}/promote")
async def promote_spec(
    spec_id: str,
    version: str,
    body: PromotionRequest,
    manager: PromotionManager = Depends(get_promotion_manager),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Transition a draft spec snapshot to active.

    The mutation is applied to the in-memory snapshot mirror by the
    promotion manager and persisted to Mongo on success via
    :meth:`SpecStore.flush_snapshot` (best-effort).
    """
    try:
        spec = await manager.promote(
            spec_id, version, actor=body.actor, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _flush_promotion_change(store, spec, op="promote")
    _invalidate_selector_cache(spec)
    return _spec_response(spec)


@router.post("/{spec_id}/versions/{version}/deprecate")
async def deprecate_spec(
    spec_id: str,
    version: str,
    body: PromotionRequest,
    manager: PromotionManager = Depends(get_promotion_manager),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Transition an active spec snapshot to deprecated.

    The mutation is applied to the in-memory snapshot mirror by the
    promotion manager and persisted to Mongo on success via
    :meth:`SpecStore.flush_snapshot` (best-effort).
    """
    try:
        spec = await manager.deprecate(
            spec_id, version, actor=body.actor, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _flush_promotion_change(store, spec, op="deprecate")
    _invalidate_selector_cache(spec)
    return _spec_response(spec)


@router.post("/{spec_id}/versions/{version}/archive")
async def archive_spec_version(
    spec_id: str,
    version: str,
    body: PromotionRequest,
    manager: PromotionManager = Depends(get_promotion_manager),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Transition a deprecated spec snapshot to archived.

    The mutation is applied to the in-memory snapshot mirror by the
    promotion manager and persisted to Mongo on success via
    :meth:`SpecStore.flush_snapshot` (best-effort).
    """
    try:
        spec = await manager.archive(
            spec_id, version, actor=body.actor, reason=body.reason
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _flush_promotion_change(store, spec, op="archive")
    _invalidate_selector_cache(spec)
    return _spec_response(spec)


@router.post("/{spec_id}/rollback")
async def rollback_spec(
    spec_id: str,
    body: RollbackRequest,
    manager: PromotionManager = Depends(get_promotion_manager),
    store: SpecStore = Depends(get_spec_store),
) -> dict[str, Any]:
    """Roll the active spec for a capability back to ``target_version``.

    The mutation is applied to the in-memory snapshot mirror by the
    promotion manager and persisted to Mongo on success via
    :meth:`SpecStore.flush_snapshot` (best-effort).
    """
    try:
        spec = await manager.rollback(
            spec_id,
            body.target_version,
            actor=body.actor,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RollbackCooldownError as exc:
        raise HTTPException(
            status_code=409,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await _flush_promotion_change(store, spec, op="rollback")
    _invalidate_selector_cache(spec)
    return _spec_response(spec)
