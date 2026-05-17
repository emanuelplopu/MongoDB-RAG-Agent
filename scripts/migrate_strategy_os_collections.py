"""
Strategy OS MongoDB Collection Migration Script.

Creates all collections and indexes required by the Strategy Operating System.
This script is idempotent - safe to run multiple times.

Usage:
    Standalone: python scripts/migrate_strategy_os_collections.py
    From code:  await migrate_strategy_os_collections(db)
"""
import asyncio
import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

# Collection definitions: (name, indexes)
# Each index is a tuple: (keys_dict, options_dict)
STRATEGY_OS_COLLECTIONS = {
    "strategy_specs": [
        ({"status": 1, "tenant_scope": 1}, {}),
        ({"capability_id": 1, "status": 1}, {}),
        ({"spec_hash": 1}, {}),
        ({"created_at": -1}, {}),
    ],
    "business_capabilities": [
        ({"tenant_scope": 1, "capability_id": 1}, {"unique": True}),
    ],
    "answer_contracts": [
        ({"format_id": 1, "version": -1}, {}),
    ],
    "strategy_test_cases": [
        ({"dataset_id": 1, "capability_id": 1}, {}),
        ({"deprecated_at": 1}, {}),
    ],
    "strategy_experiments": [
        ({"status": 1, "created_at": -1}, {}),
        ({"schedule_id": 1, "created_at": -1}, {}),
    ],
    "strategy_evaluation_results": [
        ({"strategy_id": 1, "dataset_id": 1, "created_at": -1}, {}),
        ({"experiment_id": 1, "test_case_id": 1}, {}),
        ({"created_at": -1}, {}),
    ],
    "strategy_schedules": [
        ({"enabled": 1, "next_run_at": 1}, {}),
        ({"tenant_scope": 1}, {}),
    ],
    "runtime_model_profiles": [
        ({"model": 1, "created_at": -1}, {}),
        ({"hardware_id": 1, "model": 1}, {}),
    ],
    "strategy_run_checkpoints": [
        ({"run_id": 1}, {"unique": True}),
        ({"created_at": 1}, {"expireAfterSeconds": 3600}),  # TTL: 1 hour
    ],
}


async def migrate_strategy_os_collections(db: AsyncIOMotorDatabase) -> dict[str, int]:
    """
    Create Strategy OS collections and indexes. Idempotent.

    Args:
        db: AsyncIOMotorDatabase instance

    Returns:
        Dict mapping collection_name -> number of indexes created
    """
    results = {}
    existing_collections = await db.list_collection_names()

    for collection_name, indexes in STRATEGY_OS_COLLECTIONS.items():
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
    logger.info(f"Strategy OS migration complete: {total_collections} collections, {total_indexes} indexes")
    return results


async def main():
    """Standalone execution entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

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
        results = await migrate_strategy_os_collections(db)

        print(f"\nMigration complete!")
        print(f"Collections: {len(results)}")
        for name, idx_count in results.items():
            print(f"  - {name}: {idx_count} indexes")

    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main())
