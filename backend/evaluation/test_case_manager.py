"""Test case lifecycle management with version semantics."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from backend.evaluation.models import EvaluationTestCase

logger = logging.getLogger(__name__)


class TestCaseManager:
    """
    Manages evaluation test cases with version semantics.

    Version rules:
    - Version increments ONLY on semantic changes:
      user_prompt, expected_source_ids, expected_topics, scoring_profile
    - Non-versioning changes (title, notes, tags) do NOT bump version

    Lifecycle:
    - Active: available for evaluation runs
    - Deprecated: soft-deleted (deprecated_at set), excluded from new runs
    - Stale: references documents that no longer exist (is_stale=True)
    """

    # Semantic fields that trigger version increment
    SEMANTIC_FIELDS = {"user_prompt", "expected_source_ids", "expected_topics", "scoring_profile"}

    def __init__(self, db=None):
        self.db = db
        self._collection_name = "strategy_test_cases"
        # In-memory storage: {id: [EvaluationTestCase, ...]} ordered by version
        self._in_memory: dict[str, list[EvaluationTestCase]] = {}

    async def create(self, test_case: EvaluationTestCase) -> EvaluationTestCase:
        """Create a new test case (version 1)."""
        test_case.version = 1
        test_case.created_at = datetime.utcnow()
        test_case.updated_at = datetime.utcnow()

        if self.db is not None:
            collection = self.db[self._collection_name]
            await collection.insert_one(test_case.model_dump())
        else:
            self._in_memory.setdefault(test_case.id, []).append(test_case)

        logger.info(
            "Created test case: id=%s, version=%d, dataset=%s",
            test_case.id,
            test_case.version,
            test_case.dataset_id,
        )
        return test_case

    async def update(self, test_case_id: str, updates: dict) -> EvaluationTestCase:
        """
        Update a test case.
        If semantic fields changed: create new version (new document with incremented version).
        If non-semantic fields: update in-place without version bump.
        """
        current = await self.get(test_case_id)
        if current is None:
            raise ValueError(f"Test case not found: {test_case_id}")

        if self._is_semantic_change(updates):
            # Create new version
            new_version = current.model_copy(update={
                **updates,
                "version": current.version + 1,
                "updated_at": datetime.utcnow(),
            })

            if self.db is not None:
                collection = self.db[self._collection_name]
                await collection.insert_one(new_version.model_dump())
            else:
                self._in_memory.setdefault(test_case_id, []).append(new_version)

            logger.info(
                "Version bump for test case %s: v%d -> v%d (semantic change)",
                test_case_id,
                current.version,
                new_version.version,
            )
            return new_version
        else:
            # In-place update (non-semantic)
            updated = current.model_copy(update={
                **updates,
                "updated_at": datetime.utcnow(),
            })

            if self.db is not None:
                collection = self.db[self._collection_name]
                await collection.update_one(
                    {"id": test_case_id, "version": current.version},
                    {"$set": {**updates, "updated_at": datetime.utcnow()}},
                )
            else:
                # Replace latest version in-memory
                versions = self._in_memory.get(test_case_id, [])
                if versions:
                    versions[-1] = updated

            logger.debug(
                "In-place update for test case %s v%d (non-semantic fields)",
                test_case_id,
                current.version,
            )
            return updated

    async def deprecate(self, test_case_id: str) -> None:
        """Soft-delete: set deprecated_at, exclude from future runs."""
        current = await self.get(test_case_id)
        if current is None:
            raise ValueError(f"Test case not found: {test_case_id}")

        now = datetime.utcnow()

        if self.db is not None:
            collection = self.db[self._collection_name]
            await collection.update_many(
                {"id": test_case_id},
                {"$set": {"deprecated_at": now}},
            )
        else:
            versions = self._in_memory.get(test_case_id, [])
            for tc in versions:
                tc.deprecated_at = now

        logger.info("Deprecated test case: %s", test_case_id)

    async def get(
        self, test_case_id: str, version: Optional[int] = None
    ) -> Optional[EvaluationTestCase]:
        """Get test case by ID (latest version if version not specified)."""
        if self.db is not None:
            collection = self.db[self._collection_name]
            if version is not None:
                doc = await collection.find_one(
                    {"id": test_case_id, "version": version}
                )
            else:
                cursor = collection.find({"id": test_case_id}).sort("version", -1).limit(1)
                docs = await cursor.to_list(length=1)
                doc = docs[0] if docs else None

            if doc is None:
                return None
            doc.pop("_id", None)
            return EvaluationTestCase(**doc)
        else:
            versions = self._in_memory.get(test_case_id, [])
            if not versions:
                return None
            if version is not None:
                for tc in versions:
                    if tc.version == version:
                        return tc
                return None
            return versions[-1]  # Latest version

    async def list_by_dataset(
        self, dataset_id: str, include_deprecated: bool = False
    ) -> list[EvaluationTestCase]:
        """List all active test cases in a dataset (latest version of each)."""
        if self.db is not None:
            collection = self.db[self._collection_name]
            query: dict = {"dataset_id": dataset_id}
            if not include_deprecated:
                query["deprecated_at"] = None

            # Aggregate to get latest version per id
            pipeline = [
                {"$match": query},
                {"$sort": {"version": -1}},
                {"$group": {"_id": "$id", "doc": {"$first": "$$ROOT"}}},
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            cursor = collection.aggregate(pipeline)
            results = []
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(EvaluationTestCase(**doc))
            return results
        else:
            seen_ids: set[str] = set()
            results: list[EvaluationTestCase] = []
            for tc_id, versions in self._in_memory.items():
                if not versions:
                    continue
                latest = versions[-1]
                if latest.dataset_id != dataset_id:
                    continue
                if not include_deprecated and latest.deprecated_at is not None:
                    continue
                if tc_id not in seen_ids:
                    seen_ids.add(tc_id)
                    results.append(latest)
            return results

    async def check_stale(self, test_case: EvaluationTestCase) -> bool:
        """
        Check if test case references documents that no longer exist.
        Checks expected_source_ids against the document registry.
        """
        if self.db is None:
            # Can't check documents without DB
            return False

        if not test_case.expected_source_ids:
            return False

        # Check document registry for missing source IDs
        doc_collection = self.db.get_collection("documents")
        for source_id in test_case.expected_source_ids:
            doc = await doc_collection.find_one({"source_id": source_id})
            if doc is None:
                logger.info(
                    "Test case %s references missing source: %s",
                    test_case.id,
                    source_id,
                )
                return True

        return False

    async def mark_stale(self, test_case_id: str) -> None:
        """Mark a test case as stale (needs attention)."""
        if self.db is not None:
            collection = self.db[self._collection_name]
            await collection.update_many(
                {"id": test_case_id},
                {"$set": {"is_stale": True, "updated_at": datetime.utcnow()}},
            )
        else:
            versions = self._in_memory.get(test_case_id, [])
            for tc in versions:
                tc.is_stale = True
                tc.updated_at = datetime.utcnow()

        logger.info("Marked test case as stale: %s", test_case_id)

    async def run_stale_detection(self, dataset_id: Optional[str] = None) -> list[str]:
        """
        Run stale detection across all (or dataset-specific) test cases.
        Returns list of test case IDs marked stale.
        """
        stale_ids: list[str] = []

        if self.db is None:
            # In-memory mode: can't verify document existence
            return stale_ids

        # Get all active test cases
        if dataset_id:
            test_cases = await self.list_by_dataset(dataset_id)
        else:
            # Get all non-deprecated test cases across all datasets
            collection = self.db[self._collection_name]
            pipeline = [
                {"$match": {"deprecated_at": None}},
                {"$sort": {"version": -1}},
                {"$group": {"_id": "$id", "doc": {"$first": "$$ROOT"}}},
                {"$replaceRoot": {"newRoot": "$doc"}},
            ]
            cursor = collection.aggregate(pipeline)
            test_cases = []
            async for doc in cursor:
                doc.pop("_id", None)
                test_cases.append(EvaluationTestCase(**doc))

        for tc in test_cases:
            if await self.check_stale(tc):
                await self.mark_stale(tc.id)
                stale_ids.append(tc.id)

        logger.info(
            "Stale detection complete: %d/%d test cases marked stale",
            len(stale_ids),
            len(test_cases),
        )
        return stale_ids

    def _is_semantic_change(self, updates: dict) -> bool:
        """Check if updates contain semantic fields requiring version bump."""
        return bool(set(updates.keys()) & self.SEMANTIC_FIELDS)
