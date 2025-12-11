"""Database connection manager."""

import logging
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from typing import Optional

from backend.core.config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Async MongoDB connection manager with profile switching support."""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self._sync_client: Optional[MongoClient] = None
        self._current_database: Optional[str] = None
        self._current_docs_collection: Optional[str] = None
        self._current_chunks_collection: Optional[str] = None
    
    async def connect(self, database: Optional[str] = None, 
                      docs_collection: Optional[str] = None,
                      chunks_collection: Optional[str] = None):
        """Establish database connection."""
        try:
            if self.client is None:
                self.client = AsyncIOMotorClient(settings.mongodb_uri)
                # Test connection
                await self.client.admin.command('ping')
            
            # Use provided values or fall back to settings
            self._current_database = database or settings.mongodb_database
            self._current_docs_collection = docs_collection or settings.mongodb_collection_documents
            self._current_chunks_collection = chunks_collection or settings.mongodb_collection_chunks
            
            self.db = self.client[self._current_database]
            
            logger.info(f"Connected to MongoDB: {self._current_database}")
            
        except Exception as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            raise
    
    async def switch_database(self, database: str, 
                               docs_collection: str = "documents",
                               chunks_collection: str = "chunks"):
        """Switch to a different database (for profile switching)."""
        self._current_database = database
        self._current_docs_collection = docs_collection
        self._current_chunks_collection = chunks_collection
        self.db = self.client[database]
        
        # Also update sync client if it exists
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
        
        logger.info(f"Switched to database: {database}")
    
    async def disconnect(self):
        """Close database connection."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed")
        if self._sync_client:
            self._sync_client.close()
    
    def get_sync_client(self) -> MongoClient:
        """Get synchronous MongoDB client for operations that require it."""
        if not self._sync_client:
            self._sync_client = MongoClient(settings.mongodb_uri)
        return self._sync_client
    
    @property
    def current_database_name(self) -> str:
        """Get current database name."""
        return self._current_database or settings.mongodb_database
    
    @property
    def documents_collection(self):
        """Get documents collection."""
        collection_name = self._current_docs_collection or settings.mongodb_collection_documents
        return self.db[collection_name]
    
    @property
    def chunks_collection(self):
        """Get chunks collection."""
        collection_name = self._current_chunks_collection or settings.mongodb_collection_chunks
        return self.db[collection_name]
    
    async def get_stats(self) -> dict:
        """Get database statistics."""
        try:
            doc_count = await self.documents_collection.count_documents({})
            chunk_count = await self.chunks_collection.count_documents({})
            
            docs_coll_name = self._current_docs_collection or settings.mongodb_collection_documents
            chunks_coll_name = self._current_chunks_collection or settings.mongodb_collection_chunks
            
            # Get collection stats
            doc_stats = await self.db.command("collStats", docs_coll_name)
            chunk_stats = await self.db.command("collStats", chunks_coll_name)
            
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
                "database": self.current_database_name
            }
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "error": str(e),
                "database": self.current_database_name
            }
    
    async def check_indexes(self) -> dict:
        """Check search index status."""
        try:
            sync_client = self.get_sync_client()
            db = sync_client[self.current_database_name]
            
            chunks_coll_name = self._current_chunks_collection or settings.mongodb_collection_chunks
            
            result = db.command({
                "listSearchIndexes": chunks_coll_name
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
