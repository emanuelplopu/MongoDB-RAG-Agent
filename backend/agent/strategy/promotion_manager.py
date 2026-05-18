"""Strategy spec promotion, deprecation, archive, and rollback workflow.

Implements the lifecycle state machine for ``StrategySpec`` instances::

    draft -> active -> deprecated -> archived

Plus a ``rollback`` operation that re-promotes a previously deprecated (or
archived) version of the same strategy back to ``active`` while demoting any
currently active spec for the same ``capability_id``. Rollbacks are gated by
a configurable cooldown window (default 7 days) to prevent flap loops.

The manager is backend-agnostic: it operates on a dict-of-list snapshot store
that mirrors the in-memory shim used by ``backend/routers/strategy_specs.py``.
The real MongoDB-backed store can be injected via the constructor in a later
task without changing the public interface.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from backend.agent.strategy.models import StrategySpec

logger = logging.getLogger(__name__)

#: Default cooldown between consecutive rollbacks of the same capability.
ROLLBACK_COOLDOWN_DAYS: int = 7

#: Allowed snapshot states for rollback ``target_version``.
_ROLLBACK_SOURCE_STATES: frozenset[str] = frozenset({"deprecated", "archived"})


class InvalidStateTransitionError(Exception):
    """Raised when a requested state transition is not allowed by the FSM."""


class RollbackCooldownError(Exception):
    """Raised when a rollback is attempted inside the cooldown window.

    Attributes:
        retry_after_seconds: Seconds remaining until the cooldown expires.
            Suitable for use as an HTTP ``Retry-After`` header value.
    """

    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PromotionManager:
    """Coordinate lifecycle transitions for stored ``StrategySpec`` snapshots.

    The manager owns no spec data of its own: it mutates the snapshot list
    structure shared with the strategy-specs CRUD router. Each public method
    appends an audit entry describing the transition (timestamp, actor,
    spec id, version, source state, target state, reason).

    Args:
        store: Optional snapshot store keyed by ``strategy_id``. Defaults to
            the module-level shim in ``backend.routers.strategy_specs``.
        cooldown_days: Days that must elapse between rollbacks for the same
            capability. Defaults to :data:`ROLLBACK_COOLDOWN_DAYS`.
    """

    def __init__(
        self,
        store: Optional[dict[str, list[dict[str, Any]]]] = None,
        *,
        cooldown_days: int = ROLLBACK_COOLDOWN_DAYS,
    ) -> None:
        if store is None:
            # Lazy import to avoid a circular dependency between this module
            # and ``backend.routers.strategy_specs`` (which imports the
            # PromotionManager class to wire up endpoints).
            from backend.routers.strategy_specs import _specs_store

            store = _specs_store
        self._store = store
        self._cooldown = timedelta(days=cooldown_days)
        self._audit_log: list[dict[str, Any]] = []
        self._last_rollback_at: dict[str, datetime] = {}

    # ── Public read accessors ───────────────────────────────────────────────

    @property
    def audit_log(self) -> list[dict[str, Any]]:
        """Return a shallow copy of the audit trail."""
        return list(self._audit_log)

    @property
    def store(self) -> dict[str, list[dict[str, Any]]]:
        """Return the underlying snapshot store (mutable reference)."""
        return self._store

    def last_rollback_at(self, capability_or_spec_id: str) -> Optional[datetime]:
        """Return the timestamp of the last rollback keyed by capability/spec."""
        return self._last_rollback_at.get(capability_or_spec_id)

    # ── Lifecycle operations ────────────────────────────────────────────────

    async def promote(
        self,
        spec_id: str,
        version: str,
        *,
        actor: str,
        reason: str,
    ) -> StrategySpec:
        """Promote a draft spec to ``active``.

        Demotes every currently active spec sharing the same
        ``capability_id`` to ``deprecated`` in the same call. The store shim
        is in-memory and single-process, so the operation is effectively
        atomic; a real Mongo backend would need a transaction to preserve
        this guarantee.

        Args:
            spec_id: Strategy id whose snapshot will be promoted.
            version: Semantic version of the snapshot to promote.
            actor: Identifier of the user/service performing the action.
            reason: Free-form justification recorded in the audit log.

        Returns:
            The newly active ``StrategySpec`` rebuilt from the store.

        Raises:
            KeyError: ``spec_id``/``version`` not found.
            InvalidStateTransitionError: Snapshot is not in ``draft`` state.
        """
        snapshot = self._find_snapshot(spec_id, version)
        current_state = snapshot["status"]
        if current_state != "draft":
            raise InvalidStateTransitionError(
                f"Cannot promote {spec_id}@{version}: "
                f"expected status 'draft', got '{current_state}'"
            )

        capability_id = snapshot["spec_data"].get("capability_id")
        for sid, snap in self._find_active_for_capability(capability_id):
            if sid == spec_id and snap["version"] == version:
                continue
            self._set_status(snap, "deprecated")
            self._record(
                actor=actor,
                spec_id=sid,
                version=snap["version"],
                from_state="active",
                to_state="deprecated",
                reason=f"Auto-deprecated by promotion of {spec_id}@{version}",
            )

        self._set_status(snapshot, "active")
        self._record(
            actor=actor,
            spec_id=spec_id,
            version=version,
            from_state=current_state,
            to_state="active",
            reason=reason,
        )
        logger.info(
            "Promoted strategy spec %s@%s by actor=%s", spec_id, version, actor
        )
        return self._to_strategy_spec(snapshot)

    async def deprecate(
        self,
        spec_id: str,
        version: str,
        *,
        actor: str,
        reason: str,
    ) -> StrategySpec:
        """Transition an ``active`` snapshot to ``deprecated``.

        Raises:
            KeyError: Spec/version not found.
            InvalidStateTransitionError: Snapshot is not currently ``active``.
        """
        snapshot = self._find_snapshot(spec_id, version)
        current_state = snapshot["status"]
        if current_state != "active":
            raise InvalidStateTransitionError(
                f"Cannot deprecate {spec_id}@{version}: "
                f"expected status 'active', got '{current_state}'"
            )
        self._set_status(snapshot, "deprecated")
        self._record(
            actor=actor,
            spec_id=spec_id,
            version=version,
            from_state=current_state,
            to_state="deprecated",
            reason=reason,
        )
        logger.info(
            "Deprecated strategy spec %s@%s by actor=%s", spec_id, version, actor
        )
        return self._to_strategy_spec(snapshot)

    async def archive(
        self,
        spec_id: str,
        version: str,
        *,
        actor: str,
        reason: str,
    ) -> StrategySpec:
        """Transition a ``deprecated`` snapshot to ``archived``.

        Active specs must be deprecated first; archive is a terminal state.

        Raises:
            KeyError: Spec/version not found.
            InvalidStateTransitionError: Snapshot is not ``deprecated``.
        """
        snapshot = self._find_snapshot(spec_id, version)
        current_state = snapshot["status"]
        if current_state != "deprecated":
            raise InvalidStateTransitionError(
                f"Cannot archive {spec_id}@{version}: "
                f"expected status 'deprecated', got '{current_state}'"
            )
        self._set_status(snapshot, "archived")
        self._record(
            actor=actor,
            spec_id=spec_id,
            version=version,
            from_state=current_state,
            to_state="archived",
            reason=reason,
        )
        logger.info(
            "Archived strategy spec %s@%s by actor=%s", spec_id, version, actor
        )
        return self._to_strategy_spec(snapshot)

    async def rollback(
        self,
        spec_id: str,
        target_version: str,
        *,
        actor: str,
        reason: str,
    ) -> StrategySpec:
        """Roll the active spec for a capability back to ``target_version``.

        ``target_version`` must already exist in the strategy's history and
        be in a ``deprecated`` or ``archived`` state. Any currently active
        spec for the same capability is demoted to ``deprecated``. The
        target snapshot is then promoted back to ``active`` and stamped with
        ``last_rollback_at``.

        A cooldown window (default 7 days) is enforced per capability (or
        per ``spec_id`` when no capability is configured) to prevent
        thrashing between versions.

        Raises:
            KeyError: Spec/version not found.
            InvalidStateTransitionError: ``target_version`` is not in a
                rollback-eligible state.
            RollbackCooldownError: Previous rollback occurred inside the
                cooldown window.
        """
        target_snapshot = self._find_snapshot(spec_id, target_version)
        capability_id = target_snapshot["spec_data"].get("capability_id")
        cooldown_key = capability_id or spec_id

        now = self._now()
        last_rb = self._last_rollback_at.get(cooldown_key)
        if last_rb is not None:
            elapsed = now - last_rb
            if elapsed < self._cooldown:
                remaining = self._cooldown - elapsed
                seconds = max(int(remaining.total_seconds()), 1)
                raise RollbackCooldownError(
                    f"Rollback for '{cooldown_key}' is in cooldown; "
                    f"retry in {seconds}s",
                    retry_after_seconds=seconds,
                )

        target_state = target_snapshot["status"]
        if target_state not in _ROLLBACK_SOURCE_STATES:
            raise InvalidStateTransitionError(
                f"Cannot rollback to {spec_id}@{target_version}: "
                f"expected one of {sorted(_ROLLBACK_SOURCE_STATES)}, "
                f"got '{target_state}'"
            )

        for sid, snap in self._find_active_for_capability(capability_id):
            if sid == spec_id and snap["version"] == target_version:
                continue
            from_state = snap["status"]
            self._set_status(snap, "deprecated")
            self._record(
                actor=actor,
                spec_id=sid,
                version=snap["version"],
                from_state=from_state,
                to_state="deprecated",
                reason=f"Auto-deprecated by rollback to {spec_id}@{target_version}",
            )

        self._set_status(target_snapshot, "active")
        target_snapshot["spec_data"]["last_rollback_at"] = now.isoformat()
        target_snapshot["last_rollback_at"] = now
        self._last_rollback_at[cooldown_key] = now
        self._record(
            actor=actor,
            spec_id=spec_id,
            version=target_version,
            from_state=target_state,
            to_state="active",
            reason=reason,
        )
        logger.info(
            "Rolled back strategy %s to version %s by actor=%s",
            spec_id,
            target_version,
            actor,
        )
        return self._to_strategy_spec(target_snapshot)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _now(self) -> datetime:
        """Return the current UTC time. Override in tests for determinism."""
        return datetime.utcnow()

    def _find_snapshot(self, spec_id: str, version: str) -> dict[str, Any]:
        """Return the snapshot matching ``spec_id`` and ``version`` or raise."""
        versions = self._store.get(spec_id)
        if not versions:
            raise KeyError(f"Spec '{spec_id}' not found")
        match = next((v for v in versions if v["version"] == version), None)
        if match is None:
            raise KeyError(f"Spec '{spec_id}' has no version '{version}'")
        return match

    def _find_active_for_capability(
        self, capability_id: Optional[str]
    ) -> list[tuple[str, dict[str, Any]]]:
        """Return ``(spec_id, snapshot)`` pairs that are active for a capability."""
        if not capability_id:
            return []
        results: list[tuple[str, dict[str, Any]]] = []
        for sid, versions in self._store.items():
            for snap in versions:
                if (
                    snap.get("status") == "active"
                    and snap.get("spec_data", {}).get("capability_id") == capability_id
                ):
                    results.append((sid, snap))
        return results

    def _set_status(self, snapshot: dict[str, Any], status: str) -> None:
        """Update both the snapshot envelope and embedded spec data status."""
        snapshot["status"] = status
        snapshot["spec_data"]["status"] = status
        snapshot["spec_data"]["updated_at"] = self._now().isoformat()

    def _record(
        self,
        *,
        actor: str,
        spec_id: str,
        version: str,
        from_state: str,
        to_state: str,
        reason: str,
    ) -> None:
        """Append a structured entry to the in-memory audit log."""
        self._audit_log.append(
            {
                "timestamp": self._now(),
                "actor": actor,
                "spec_id": spec_id,
                "version": version,
                "from_state": from_state,
                "to_state": to_state,
                "reason": reason,
            }
        )

    def _to_strategy_spec(self, snapshot: dict[str, Any]) -> StrategySpec:
        """Rebuild a ``StrategySpec`` from a snapshot's serialized payload."""
        return StrategySpec(**snapshot["spec_data"])


__all__ = [
    "InvalidStateTransitionError",
    "PromotionManager",
    "ROLLBACK_COOLDOWN_DAYS",
    "RollbackCooldownError",
]
