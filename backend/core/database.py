"""Database connection manager."""

import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from typing import Optional

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Dedicated thread pool for database operations (separate from ingestion)
_db_executor: Optional[ThreadPoolExecutor] = None

def get_db_executor() -> ThreadPoolExecutor:
    """Get or create dedicated thread pool for DB operations."""
    global _db_executor
    if _db_executor is None:
        # Use 4 workers for API DB operations, separate from ingestion
        _db_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db_api_")
        logger.info("Created dedicated DB executor with 4 workers")
    return _db_executor


class DatabaseManager:
    """Async MongoDB connection manager with profile switching support."""
    
    def __init__(self):
        self.client: Optional[AsyncIOMotorClient] = None
        self.db = None
        self._sync_client: Optional[MongoClient] = None
        self._sync_client_lock = threading.Lock()
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
        with self._sync_client_lock:
            if not self._sync_client:
                logger.info("Creating synchronous MongoDB client...")
                self._sync_client = MongoClient(
                    settings.mongodb_uri,
                    serverSelectionTimeoutMS=5000,  # 5 second timeout
                    connectTimeoutMS=5000
                )
                # Test the connection
                self._sync_client.admin.command('ping')
                logger.info("Synchronous MongoDB client created successfully")
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
        """Get database statistics using thread pool to avoid blocking."""
        try:
            # Run sync MongoDB operations in dedicated thread pool
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                get_db_executor(),
                self._get_stats_sync
            )
            return result
        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {
                "error": str(e),
                "database": self.current_database_name
            }
    
    def _get_stats_sync(self) -> dict:
        """Synchronous helper for getting stats - runs in thread pool."""
        try:
            logger.info("_get_stats_sync starting...")
            sync_client = self.get_sync_client()
            db = sync_client[self.current_database_name]
            
            docs_coll_name = self._current_docs_collection or settings.mongodb_collection_documents
            chunks_coll_name = self._current_chunks_collection or settings.mongodb_collection_chunks
            
            docs_coll = db[docs_coll_name]
            chunks_coll = db[chunks_coll_name]
            
            # Use estimated_document_count for faster results (doesn't need to scan all docs)
            logger.info("Getting estimated document counts...")
            doc_count = docs_coll.estimated_document_count()
            chunk_count = chunks_coll.estimated_document_count()
            
            logger.info(f"Got counts: {doc_count} docs, {chunk_count} chunks. Getting collection stats...")
            # Get collection stats
            doc_stats = db.command("collStats", docs_coll_name)
            chunk_stats = db.command("collStats", chunks_coll_name)
            
            logger.info(f"_get_stats_sync completed successfully")
            
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
            logger.error(f"Failed to get stats (sync): {e}")
            return {
                "error": str(e),
                "database": self.current_database_name
            }
    
    async def check_indexes(self) -> dict:
        """Check search index status using thread pool to avoid blocking."""
        try:
            # Run sync MongoDB operation in dedicated thread pool
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                get_db_executor(),
                self._check_indexes_sync
            )
            return result
        except Exception as e:
            logger.error(f"Failed to check indexes: {e}")
            return {"error": str(e)}
    
    def _check_indexes_sync(self) -> dict:
        """Synchronous helper for checking indexes - runs in thread pool."""
        try:
            logger.info("_check_indexes_sync starting...")
            sync_client = self.get_sync_client()
            db = sync_client[self.current_database_name]
            
            chunks_coll_name = self._current_chunks_collection or settings.mongodb_collection_chunks
            
            logger.info(f"Listing search indexes for {chunks_coll_name}...")
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
            
            logger.info(f"_check_indexes_sync completed, found {len(indexes)} indexes")
            
            return {
                "indexes": indexes,
                "vector_index": settings.mongodb_vector_index,
                "text_index": settings.mongodb_text_index
            }
        except Exception as e:
            logger.error(f"Failed to check indexes (sync): {e}")
            return {"error": str(e)}
