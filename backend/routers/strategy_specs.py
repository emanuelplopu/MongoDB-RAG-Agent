"""Strategy spec CRUD API with optimistic locking.

Provides REST endpoints for managing StrategySpec definitions with
in-memory storage, version history, and optimistic concurrency control
via the ``X-Expected-Version`` header.

Storage is module-level for now and will be migrated to MongoDB once the
database is wired through ``backend/main.py``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from backend.agent.strategy.models import StrategySpec
from backend.agent.strategy.spec_loader import validate_spec

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/strategy-specs", tags=["strategy-specs"])


# ═══════════════════════════════════════════════════════════════════════════════
# In-memory store
# ═══════════════════════════════════════════════════════════════════════════════
#
# Each entry is keyed by ``strategy_id`` and stores an ordered list of
# version snapshots. The newest snapshot is the last element.
#
# Snapshot shape:
#   {
#       "spec_data": dict,            # serialized StrategySpec
#       "version": str,               # semantic version string from the spec
#       "version_counter": int,       # monotonically increasing integer
#       "status": str,                # spec status (draft/active/archived/...)
#       "created_at": datetime,       # snapshot creation time
#       "spec_hash": str,             # sha256 of canonical spec JSON
#   }
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


def _latest(spec_id: str) -> dict[str, Any]:
    """Return the latest snapshot for ``spec_id`` or raise 404."""
    versions = _specs_store.get(spec_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")
    return versions[-1]


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


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("", status_code=201)
async def create_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new strategy spec.

    Validates the spec via ``validate_spec`` and stores it as version 1.
    Returns the stored spec data. Raises 400 on validation failures and
    409 if the strategy_id already exists.
    """
    spec = _parse_spec(payload)

    if spec.strategy_id in _specs_store:
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
    _specs_store[spec.strategy_id] = [snapshot]
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
) -> SpecListResponse:
    """List the latest version of each stored spec, with optional filters."""
    results: list[dict[str, Any]] = []
    for spec_id, versions in _specs_store.items():
        if not versions:
            continue
        latest = versions[-1]
        spec_data = latest["spec_data"]
        if status is not None and spec_data.get("status") != status:
            continue
        if capability_id is not None and spec_data.get("capability_id") != capability_id:
            continue
        if tenant_id is not None and spec_data.get("tenant_scope") != tenant_id:
            continue
        results.append(
            {
                "strategy_id": spec_id,
                "version": latest["version"],
                "version_counter": latest["version_counter"],
                "status": latest["status"],
                "created_at": latest["created_at"],
                "spec_hash": latest["spec_hash"],
                "spec": spec_data,
            }
        )

    total = len(results)
    return SpecListResponse(specs=results[:limit], total=total)


@router.get("/{spec_id}")
async def get_spec(
    spec_id: str,
    version: Optional[str] = Query(
        default=None,
        description="Optional semantic version. Defaults to latest.",
    ),
) -> dict[str, Any]:
    """Return a single spec snapshot by id (and optional version)."""
    versions = _specs_store.get(spec_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    if version is None:
        snapshot = versions[-1]
    else:
        match = next((v for v in versions if v["version"] == version), None)
        if match is None:
            raise HTTPException(
                status_code=404,
                detail=f"Spec '{spec_id}' has no version '{version}'",
            )
        snapshot = match

    return {
        "strategy_id": spec_id,
        "version": snapshot["version"],
        "version_counter": snapshot["version_counter"],
        "status": snapshot["status"],
        "created_at": snapshot["created_at"],
        "spec_hash": snapshot["spec_hash"],
        "spec": snapshot["spec_data"],
    }


@router.put("/{spec_id}")
async def update_spec(
    spec_id: str,
    payload: dict[str, Any],
    x_expected_version: int = Header(
        ...,
        alias="X-Expected-Version",
        description="Expected current version_counter for optimistic locking.",
    ),
) -> dict[str, Any]:
    """Update a spec, enforcing optimistic locking on the version counter."""
    versions = _specs_store.get(spec_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    current = versions[-1]
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
    _specs_store[spec_id].append(snapshot)
    logger.info(
        "Updated strategy spec %s (version=%s, counter=%d)",
        spec_id,
        new_spec.version,
        snapshot["version_counter"],
    )
    return snapshot["spec_data"]


@router.delete("/{spec_id}")
async def archive_spec(spec_id: str) -> SpecDeleteResponse:
    """Soft-delete a spec by setting the latest version's status to archived."""
    versions = _specs_store.get(spec_id)
    if not versions:
        raise HTTPException(status_code=404, detail=f"Spec '{spec_id}' not found")

    latest = versions[-1]
    latest["status"] = "archived"
    latest["spec_data"]["status"] = "archived"
    logger.info("Archived strategy spec %s", spec_id)
    return SpecDeleteResponse(
        spec_id=spec_id,
        status="archived",
        message=f"Spec '{spec_id}' archived",
    )


@router.post("/{spec_id}/validate")
async def validate_stored_spec(spec_id: str) -> SpecValidationResponse:
    """Run ``validate_spec`` against the latest stored snapshot."""
    snapshot = _latest(spec_id)
    try:
        spec = StrategySpec(**snapshot["spec_data"])
    except Exception as exc:
        return SpecValidationResponse(
            valid=False,
            errors=[f"Failed to deserialize stored spec: {exc}"],
        )

    errors = validate_spec(spec)
    return SpecValidationResponse(valid=not errors, errors=errors)


@router.get("/{spec_id}/history")
async def get_spec_history(spec_id: str) -> SpecHistoryResponse:
    """Return metadata for every stored version of a spec."""
    versions = _specs_store.get(spec_id)
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
