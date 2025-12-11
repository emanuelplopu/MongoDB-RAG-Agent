"""Database connection manager."""

import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from typing import Optional

from backend.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async MongoDB connection manager."""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self._sync_client: Optional[MongoClient] = None
    
    async def connect(self):
        """Establish database connection."""
        try:
            self.client = AsyncIOMotorClient(settings.mongodb_uri)
            self.db = self.client[settings.mongodb_database]
            
            # Test connection
            await self.client.admin.command('ping')
            logger.info(f"Connected to MongoDB: {settings.mongodb_database}")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
    
    async def disconnect(self):
        """Close database connection."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")
    
    def get_sync_client(self) -> MongoClient:
        """Get synchronous MongoDB client for operations that require it."""
        if not self._sync_client:
            self._sync_client = MongoClient(settings.mongodb_uri)
        return self._sync_client
    
    @property
    def documents_collection(self):
        """Get documents collection."""
        return self.db[settings.mongodb_collection_documents]
    
    @property
    def chunks_collection(self):
        """Get chunks collection."""
        return self.db[settings.mongodb_collection_chunks]
    
    async def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            doc_count = await self.documents_collection.count_documents({})
            chunk_count = await self.chunks_collection.count_documents({})
            
            # Get collection stats
            doc_stats = await self.db.command("collStats", settings.mongodb_collection_documents)
            chunk_stats = await self.db.command("collStats", settings.mongodb_collection_chunks)
            
            return {
                "documents": {
                    "count": doc_count,
                    "size_bytes": doc_stats.get("size", 0),
                    "avg_doc_size": doc_stats.get("avgObjSize", 0)
                },
                "chunks": {
                    "count": chunk_count,
                    "size_bytes": chunk_stats.get("size", 0),
                    "avg_doc_size": chunk_stats.get("avgObjSize", 0)
                },
                "database": settings.mongodb_database
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "error": str(e),
                "database": settings.mongodb_database
            }
    
    async def check_indexes(self) -> dict:
        """Check search index status."""
        try:
            sync_client = self.get_sync_client()
            db = sync_client[settings.mongodb_database]
            
            result = db.command({
                "listSearchIndexes": settings.mongodb_collection_chunks
            })
            
            indexes = []
            if "cursor" in result and "firstBatch" in result["cursor"]:
                for idx in result["cursor"]["firstBatch"]:
                    indexes.append({
                        "name": idx.get("name"),
                        "status": idx.get("status"),
                        "type": idx.get("type", "search")
                    })
            
            return {
                "indexes": indexes,
                "vector_index": settings.mongodb_vector_index,
                "text_index": settings.mongodb_text_index
            }
        except Exception as e:
            logger.error(f"Failed to check indexes: {e}")
            return {"error": str(e)}
