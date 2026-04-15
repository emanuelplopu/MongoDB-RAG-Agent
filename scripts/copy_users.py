"""Copy all users and related auth data from one database to another."""
import asyncio
import sys
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI = "mongodb://mongodb:27017/?directConnection=true"
SOURCE_DB = sys.argv[1] if len(sys.argv) > 1 else "rag_db"
TARGET_DB = sys.argv[2] if len(sys.argv) > 2 else "rag_test_law"

COLLECTIONS_TO_COPY = ["users", "api_keys", "profile_access"]

async def main():
    client = AsyncIOMotorClient(MONGODB_URI)
    src = client[SOURCE_DB]
    dst = client[TARGET_DB]
    
    for col_name in COLLECTIONS_TO_COPY:
        docs = await src[col_name].find().to_list(None)
        if not docs:
            print(f"  {col_name}: no documents in source, skipping")
            continue
        
        copied = 0
        for doc in docs:
            existing = await dst[col_name].find_one({"_id": doc["_id"]})
            if not existing:
                await dst[col_name].insert_one(doc)
                copied += 1
                label = doc.get("email", doc.get("name", doc["_id"]))
                print(f"  {col_name}: copied {label}")
            else:
                label = doc.get("email", doc.get("name", doc["_id"]))
                print(f"  {col_name}: already exists - {label}")
        
        print(f"  {col_name}: {copied}/{len(docs)} copied")
    
    print(f"\nDone! Auth data synced from {SOURCE_DB} -> {TARGET_DB}")

asyncio.run(main())
