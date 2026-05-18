"""
Evaluation System MongoDB Collection Migration Script.

Creates all collections and indexes required by the Strategy OS Evaluation System.
This script is idempotent - safe to run multiple times.

Usage:
    Standalone: python scripts/migrate_evaluation_collections.py
    From code:  await migrate_evaluation_collections(db)
"""

import asyncio
import logging
import os

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Collection definitions: (name, indexes)
# Each index is a tuple: (keys_dict, options_dict)
EVALUATION_COLLECTIONS = {
    "strategy_test_cases": [
        ({"id": 1, "version": -1}, {"unique": True}),
        ({"dataset_id": 1, "deprecated_at": 1}, {}),
        ({"is_stale": 1}, {}),
    ],
    "evaluation_results": [
        ({"test_case_id": 1, "test_case_version": 1, "strategy_id": 1}, {}),
        ({"strategy_id": 1, "created_at": -1}, {}),
        ({"run_id": 1}, {"unique": True}),
    ],
    "judge_calibrations": [
        ({"profile_id": 1, "judge_model": 1}, {}),
        ({"calibration_id": 1}, {"unique": True}),
    ],
    "contradiction_logs": [
        ({"session_id": 1, "turn": 1}, {}),
        ({"checked_at": 1}, {"expireAfterSeconds": 2592000}),  # 30 day TTL
    ],
}


async def migrate_evaluation_collections(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """
    Create evaluation collections and indexes. Idempotent.

    Args:
        db: AsyncIOMotorDatabase instance

    Returns:
        Dict mapping collection_name -> number of indexes created
    """
    results = {}
    existing_collections = await db.list_collection_names()

    for collection_name, indexes in EVALUATION_COLLECTIONS.items():
        # Create collection if it doesn't exist
        if collection_name not in existing_collections:
            await db.create_collection(collection_name)
            logger.info(f"Created collection: {collection_name}")
        else:
            logger.debug(f"Collection already exists: {collection_name}")

        # Create indexes
        collection = db[collection_name]
        indexes_created = 0

        for keys, options in indexes:
            try:
                index_keys = list(keys.items())
                await collection.create_index(index_keys, **options)
                indexes_created += 1
            except Exception as e:
                # Index may already exist with same definition - that's OK
                if "already exists" in str(e).lower() or "IndexOptionsConflict" in str(e):
                    logger.debug(f"Index already exists on {collection_name}: {keys}")
                else:
                    logger.warning(f"Failed to create index on {collection_name}: {e}")

        results[collection_name] = indexes_created

    total_collections = len(results)
    total_indexes = sum(results.values())
    logger.info(
        f"Evaluation system migration complete: {total_collections} collections, {total_indexes} indexes"
    )
    return results


async def main():
    """Standalone execution entry point."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    mongodb_uri = os.environ.get("MONGODB_URI", "mongodb://localhost:11017")
    database_name = os.environ.get("MONGODB_DATABASE", "rag_db")

    logger.info(f"Connecting to MongoDB: {mongodb_uri}, database: {database_name}")

    client = AsyncIOMotorClient(mongodb_uri)
    db = client[database_name]

    try:
        # Test connection
        await client.admin.command("ping")
        logger.info("MongoDB connection successful")

        # Run migration
        results = await migrate_evaluation_collections(db)

        print(f"\nMigration complete!")
        print(f"Collections: {len(results)}")
        for name, idx_count in results.items():
            print(f"  - {name}: {idx_count} indexes")

    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
