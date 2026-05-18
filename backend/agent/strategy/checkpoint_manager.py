"""Checkpoint persistence for long-running strategy executions."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from backend.agent.strategy.models import StrategyRunState

logger = logging.getLogger(__name__)

__all__ = ["CheckpointManager"]


class CheckpointManager:
    """
    Manages checkpoint persistence for strategy runs.

    Allows long-running strategies to resume after process crashes.
    Checkpoints are stored in MongoDB ``strategy_run_checkpoints`` collection
    with a 1-hour TTL (auto-cleanup) configured on the ``created_at`` field
    (see ``scripts/migrate_strategy_os_collections.py``).
    """

    def __init__(self, db=None, ttl_minutes: int = 60):
        """Initialize the checkpoint manager.

        Args:
            db: An async MongoDB database handle (e.g. ``pymongo.AsyncMongoClient``
                database or ``AsyncIOMotorDatabase``). When ``None``, all
                operations become no-ops returning sensible defaults.
            ttl_minutes: How long checkpoints remain valid before being
                considered expired by ``list_pending_checkpoints``. The actual
                MongoDB TTL index is configured by the migration script.
        """
        self.db = db
        self.ttl_minutes = ttl_minutes
        self._collection_name = "strategy_run_checkpoints"

    async def save_checkpoint(
        self,
        state: StrategyRunState,
        completed_level: int,
    ) -> bool:
        """Save a checkpoint after a level completes.

        Args:
            state: Current ``StrategyRunState``.
            completed_level: Index of the level just completed.

        Returns:
            True on success, False on failure (failures are logged).
        """
        if self.db is None:
            return False

        try:
            now = datetime.utcnow()
            if hasattr(state, "model_dump"):
                snapshot = state.model_dump(mode="json")
            else:
                snapshot = state.dict()

            doc = {
                "run_id": state.run_id,
                "trace_id": state.trace_id,
                "completed_level": completed_level,
                "state_snapshot": snapshot,
                "saved_at": now,
                "created_at": now,
                "expires_at": now + timedelta(minutes=self.ttl_minutes),
            }
            await self.db[self._collection_name].replace_one(
                {"run_id": state.run_id},
                doc,
                upsert=True,
            )
            return True
        except Exception as e:
            logger.warning(
                "Failed to save checkpoint for run %s: %s", state.run_id, e
            )
            return False

    async def load_checkpoint(self, run_id: str) -> Optional[dict]:
        """Load a checkpoint by ``run_id``.

        Returns:
            The checkpoint document with ``state_snapshot`` and
            ``completed_level``, or ``None`` if not found.
        """
        if self.db is None:
            return None

        try:
            doc = await self.db[self._collection_name].find_one(
                {"run_id": run_id}
            )
            return doc
        except Exception as e:
            logger.warning(
                "Failed to load checkpoint for run %s: %s", run_id, e
            )
            return None

    async def delete_checkpoint(self, run_id: str) -> bool:
        """Delete a checkpoint after successful run completion.

        Returns:
            True if a document was deleted, False otherwise.
        """
        if self.db is None:
            return False

        try:
            result = await self.db[self._collection_name].delete_one(
                {"run_id": run_id}
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.warning(
                "Failed to delete checkpoint for run %s: %s", run_id, e
            )
            return False

    async def list_pending_checkpoints(self) -> list[dict]:
        """List all unexpired checkpoints (for recovery on startup)."""
        if self.db is None:
            return []

        try:
            cursor = self.db[self._collection_name].find(
                {"expires_at": {"$gt": datetime.utcnow()}}
            )
            return [doc async for doc in cursor]
        except Exception as e:
            logger.warning("Failed to list pending checkpoints: %s", e)
            return []

    def restore_state_from_checkpoint(
        self, checkpoint_doc: dict
    ) -> StrategyRunState:
        """Reconstruct a ``StrategyRunState`` from a checkpoint document."""
        snapshot = checkpoint_doc.get("state_snapshot", {}) or {}
        return StrategyRunState(**snapshot)
