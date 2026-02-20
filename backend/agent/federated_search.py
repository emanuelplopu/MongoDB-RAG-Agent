"""Federated Search - Multi-database search with access control.

This module provides unified search across multiple data sources:
- Profile documents (shared with profile members)
- Cloud storage (shared or private)
- Personal data (user's emails, etc.)

Access control is enforced based on user permissions.
Supports strategy-specific scoring through the strategy pattern.
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any, Tuple, TYPE_CHECKING
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from backend.agent.schemas import (
    DataSource, DataSourceType, AccessType,
    DocumentReference, ResultQuality
)
from backend.core.config import settings

if TYPE_CHECKING:
    from backend.agent.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class FederatedSearch:
    """Federated search across multiple databases with access control."""
    
    def __init__(self, mongo_client: Optional[AsyncIOMotorClient] = None):
        """Initialize federated search.
        
        Args:
            mongo_client: Optional MongoDB client. If not provided, creates one.
        """
        self._client = mongo_client
        self._embedding_client = None
    
    @property
    def client(self) -> AsyncIOMotorClient:
        """Get or create MongoDB client."""
        if self._client is None:
            self._client = AsyncIOMotorClient(settings.mongodb_uri)
        return self._client
    
    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text using configured embedding model."""
        from openai import AsyncOpenAI
        
        if self._embedding_client is None:
            self._embedding_client = AsyncOpenAI(
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url
            )
        
        response = await self._embedding_client.embeddings.create(
            model=settings.embedding_model,
            input=text
        )
        return response.data[0].embedding
    
    def get_accessible_sources(
        self,
        user_id: str,
        user_email: str,
        active_profile_key: Optional[str] = None,
        active_profile_database: Optional[str] = None,
        accessible_profile_keys: Optional[List[str]] = None
    ) -> List[DataSource]:
        """Get list of data sources accessible to the user.
        
        Args:
            user_id: Current user's ID
            user_email: Current user's email
            active_profile_key: Currently active profile key
            active_profile_database: Database of the active profile
            accessible_profile_keys: List of profile keys user has access to
        
        Returns:
            List of DataSource objects the user can search
        """
        sources = []
        
        # 1. Profile database - if user has active profile access
        if active_profile_key and active_profile_database:
            sources.append(DataSource(
                id=f"profile_{active_profile_key}",
                type=DataSourceType.PROFILE,
                database=active_profile_database,
                collection_documents="documents",
                collection_chunks="chunks",
                access_type=AccessType.PROFILE,
                profile_key=active_profile_key,
                display_name=f"Profile: {active_profile_key}"
            ))
        
        # 2. User's personal database (emails, private data)
        # Format: user_rag_{first}_{last} derived from email
        email_parts = user_email.lower().split("@")[0]
        user_db_name = f"user_rag_{email_parts.replace('.', '_').replace('-', '_')}"
        
        sources.append(DataSource(
            id=f"personal_{user_id}",
            type=DataSourceType.PERSONAL,
            database=user_db_name,
            collection_documents="documents",
            collection_chunks="chunks",
            access_type=AccessType.PRIVATE,
            owner_id=user_id,
            display_name="Personal Data"
        ))
        
        # 3. User's private cloud storage
        user_cloud_db = f"cloud_{email_parts.replace('.', '_').replace('-', '_')}"
        sources.append(DataSource(
            id=f"cloud_private_{user_id}",
            type=DataSourceType.CLOUD_PRIVATE,
            database=user_cloud_db,
            collection_documents="documents",
            collection_chunks="chunks",
            access_type=AccessType.PRIVATE,
            owner_id=user_id,
            display_name="Private Cloud Storage"
        ))
        
        # 4. Shared cloud storage (if exists)
        # This could be configured per-profile or globally
        if active_profile_key:
            sources.append(DataSource(
                id=f"cloud_shared_{active_profile_key}",
                type=DataSourceType.CLOUD_SHARED,
                database=f"cloud_shared_{active_profile_key}",
                collection_documents="documents",
                collection_chunks="chunks",
                access_type=AccessType.SHARED,
                profile_key=active_profile_key,
                display_name=f"Shared Cloud: {active_profile_key}"
            ))
        
        return sources
    
    async def _search_database(
        self,
        database_name: str,
        chunks_collection: str,
        docs_collection: str,
        query: str,
        query_embedding: List[float],
        match_count: int = 10,
        search_type: str = "hybrid",
        strategy: "BaseStrategy" = None
    ) -> List[Dict[str, Any]]:
        """Search a single database.
        
        Args:
            database_name: Name of the MongoDB database
            chunks_collection: Name of chunks collection
            docs_collection: Name of documents collection
            query: Search query text
            query_embedding: Query embedding vector
            match_count: Maximum results to return
            search_type: "vector", "text", or "hybrid"
            strategy: Optional strategy for custom RRF scoring
        
        Returns:
            List of search result dicts
        """
        try:
            db = self.client[database_name]
            collection = db[chunks_collection]
            
            # Check if collection exists and has data
            count = await collection.count_documents({})
            if count == 0:
                logger.debug(f"Database {database_name}.{chunks_collection} is empty")
                return []
            
            results = []
            
            if search_type in ["vector", "hybrid"]:
                # Vector search
                try:
                    vector_pipeline = [
                        {
                            "$vectorSearch": {
                                "index": settings.mongodb_vector_index,
                                "queryVector": query_embedding,
                                "path": "embedding",
                                "numCandidates": 100,
                                "limit": match_count * 2 if search_type == "hybrid" else match_count
                            }
                        },
                        {
                            "$lookup": {
                                "from": docs_collection,
                                "localField": "document_id",
                                "foreignField": "_id",
                                "as": "doc_info"
                            }
                        },
                        {"$unwind": {"path": "$doc_info", "preserveNullAndEmptyArrays": True}},
                        {
                            "$project": {
                                "chunk_id": "$_id",
                                "document_id": 1,
                                "content": 1,
                                "similarity": {"$meta": "vectorSearchScore"},
                                "metadata": 1,
                                "document_title": {"$ifNull": ["$doc_info.title", "Unknown"]},
                                "document_source": {"$ifNull": ["$doc_info.source", ""]}
                            }
                        }
                    ]
                    
                    cursor = collection.aggregate(vector_pipeline)
                    async for doc in cursor:
                        doc["search_type"] = "vector"
                        results.append(doc)
                except Exception as e:
                    logger.warning(f"Vector search failed for {database_name}: {e}")
            
            if search_type in ["text", "hybrid"]:
                # Text search
                try:
                    text_pipeline = [
                        {
                            "$search": {
                                "index": settings.mongodb_text_index,
                                "text": {
                                    "query": query,
                                    "path": "content",
                                    "fuzzy": {"maxEdits": 2, "prefixLength": 3}
                                }
                            }
                        },
                        {"$limit": match_count * 2 if search_type == "hybrid" else match_count},
                        {
                            "$lookup": {
                                "from": docs_collection,
                                "localField": "document_id",
                                "foreignField": "_id",
                                "as": "doc_info"
                            }
                        },
                        {"$unwind": {"path": "$doc_info", "preserveNullAndEmptyArrays": True}},
                        {
                            "$project": {
                                "chunk_id": "$_id",
                                "document_id": 1,
                                "content": 1,
                                "similarity": {"$meta": "searchScore"},
                                "metadata": 1,
                                "document_title": {"$ifNull": ["$doc_info.title", "Unknown"]},
                                "document_source": {"$ifNull": ["$doc_info.source", ""]}
                            }
                        }
                    ]
                    
                    cursor = collection.aggregate(text_pipeline)
                    async for doc in cursor:
                        doc["search_type"] = "text"
                        results.append(doc)
                except Exception as e:
                    logger.warning(f"Text search failed for {database_name}: {e}")
            
            # For hybrid, apply RRF
            if search_type == "hybrid" and results:
                results = self._apply_rrf(results, match_count, strategy=strategy)
            
            return results[:match_count]
            
        except Exception as e:
            logger.error(f"Search failed for database {database_name}: {e}")
            return []
    
    def _apply_rrf(
        self,
        results: List[Dict],
        limit: int,
        k: int = 60,
        strategy: "BaseStrategy" = None
    ) -> List[Dict]:
        """Apply Reciprocal Rank Fusion to combine vector and text results.
        
        If a strategy is provided, delegates to the strategy's calculate_rrf_scores
        method for custom scoring logic. Otherwise, uses the default enhanced RRF.
        
        Enhanced with:
        - Cross-search boosting (documents found by both searches get higher scores)
        - Content quality penalties (very short content penalized)
        - Original vector similarity preservation for reference
        
        Args:
            results: Combined results from vector and text search
            limit: Maximum results to return
            k: RRF constant (default 60)
            strategy: Optional strategy for custom scoring
        
        Returns:
            Deduplicated and ranked results with normalized scores (0-1 scale)
        """
        # If a strategy is provided, use its custom RRF scoring
        if strategy is not None:
            try:
                return strategy.calculate_rrf_scores(results, limit)
            except Exception as e:
                logger.warning(f"Strategy RRF scoring failed, falling back to default: {e}")
        # Separate by search type
        vector_results = [r for r in results if r.get("search_type") == "vector"]
        text_results = [r for r in results if r.get("search_type") == "text"]
        
        # Create ID sets for cross-search detection
        vector_ids = {str(r["chunk_id"]) for r in vector_results}
        text_ids = {str(r["chunk_id"]) for r in text_results}
        cross_match_ids = vector_ids & text_ids  # Documents found in both searches
        
        rrf_scores = {}
        result_map = {}
        original_similarity = {}  # Preserve original vector similarity
        
        for rank, doc in enumerate(vector_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            result_map[chunk_id] = doc
            # Store the original vector similarity score (0-1 scale)
            if "similarity" in doc:
                original_similarity[chunk_id] = doc["similarity"]
        
        for rank, doc in enumerate(text_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            if chunk_id not in result_map:
                result_map[chunk_id] = doc
        
        # Apply quality adjustments before normalization
        for chunk_id in rrf_scores:
            doc = result_map[chunk_id]
            content = doc.get("content", "")
            
            # Cross-search boost: 15% increase for documents found in both vector and text search
            if chunk_id in cross_match_ids:
                rrf_scores[chunk_id] *= 1.15
            
            # Content length penalty: penalize very short chunks (likely incomplete)
            content_len = len(content)
            if content_len < 50:
                rrf_scores[chunk_id] *= 0.5  # Heavy penalty for very short
            elif content_len < 100:
                rrf_scores[chunk_id] *= 0.7  # Moderate penalty
            elif content_len < 200:
                rrf_scores[chunk_id] *= 0.85  # Light penalty
            
            # Bonus for substantial content
            if content_len > 500:
                rrf_scores[chunk_id] *= 1.05  # Small bonus for rich content
        
        # Normalize RRF scores to 0-1 range
        # Max possible raw RRF: 2/k (when doc is rank 0 in both searches)
        # We use min-max normalization for better relative scoring
        if rrf_scores:
            min_score = min(rrf_scores.values())
            max_score = max(rrf_scores.values())
            score_range = max_score - min_score
            
            if score_range > 0:
                # Normalize to 0.5-1.0 range (all results are somewhat relevant)
                for chunk_id in rrf_scores:
                    normalized = (rrf_scores[chunk_id] - min_score) / score_range
                    rrf_scores[chunk_id] = 0.5 + (normalized * 0.5)
            else:
                # All same score, use original similarity or default to 0.75
                for chunk_id in rrf_scores:
                    rrf_scores[chunk_id] = original_similarity.get(chunk_id, 0.75)
        
        # Sort by RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        merged = []
        for chunk_id in sorted_ids[:limit]:
            doc = result_map[chunk_id]
            doc["rrf_score"] = rrf_scores[chunk_id]
            # Also preserve original similarity for reference
            if chunk_id in original_similarity:
                doc["vector_similarity"] = original_similarity[chunk_id]
            # Mark cross-matches for debugging
            doc["cross_match"] = chunk_id in cross_match_ids
            merged.append(doc)
        
        # Log cross-match stats for debugging
        cross_match_count = sum(1 for doc in merged if doc.get("cross_match"))
        logger.debug(f"RRF merged {len(merged)} results, {cross_match_count} cross-matches")
        
        return merged
    
    async def search(
        self,
        query: str,
        user_id: str,
        user_email: str,
        sources: Optional[List[str]] = None,
        active_profile_key: Optional[str] = None,
        active_profile_database: Optional[str] = None,
        accessible_profile_keys: Optional[List[str]] = None,
        match_count: int = 10,
        search_type: str = "hybrid",
        strategy: "BaseStrategy" = None
    ) -> Tuple[List[DocumentReference], Dict[str, Any]]:
        """Perform federated search across all accessible sources.
        
        Args:
            query: Search query
            user_id: Current user's ID
            user_email: Current user's email
            sources: Optional list of source IDs to search (None = all)
            active_profile_key: Currently active profile key
            active_profile_database: Database of the active profile
            accessible_profile_keys: List of profile keys user has access to
            match_count: Maximum results per source
            search_type: "vector", "text", or "hybrid"
            strategy: Optional strategy for custom RRF scoring
        
        Returns:
            Tuple of (list of DocumentReference, search metadata dict)
        """
        start_time = time.time()
        
        # Get accessible sources
        all_sources = self.get_accessible_sources(
            user_id=user_id,
            user_email=user_email,
            active_profile_key=active_profile_key,
            active_profile_database=active_profile_database,
            accessible_profile_keys=accessible_profile_keys
        )
        
        # Filter to requested sources
        if sources:
            # Handle special "all" source
            if "all" not in sources:
                # Map source type names to actual source IDs
                source_type_map = {
                    "profile": DataSourceType.PROFILE,
                    "cloud": [DataSourceType.CLOUD_SHARED, DataSourceType.CLOUD_PRIVATE],
                    "cloud_shared": DataSourceType.CLOUD_SHARED,
                    "cloud_private": DataSourceType.CLOUD_PRIVATE,
                    "personal": DataSourceType.PERSONAL,
                }
                
                filtered = []
                for s in all_sources:
                    # Match by source ID
                    if s.id in sources:
                        filtered.append(s)
                        continue
                    # Match by source type
                    for req_source in sources:
                        if req_source in source_type_map:
                            mapped = source_type_map[req_source]
                            if isinstance(mapped, list):
                                if s.type in mapped:
                                    filtered.append(s)
                                    break
                            elif s.type == mapped:
                                filtered.append(s)
                                break
                all_sources = filtered
        
        if not all_sources:
            logger.warning("No accessible sources found for user")
            return [], {"sources_searched": 0, "duration_ms": 0}
        
        # Generate query embedding once
        query_embedding = await self.get_embedding(query)
        
        # Search all sources in parallel
        search_tasks = []
        source_info = []
        
        for source in all_sources:
            task = self._search_database(
                database_name=source.database,
                chunks_collection=source.collection_chunks,
                docs_collection=source.collection_documents,
                query=query,
                query_embedding=query_embedding,
                match_count=match_count,
                search_type=search_type,
                strategy=strategy
            )
            search_tasks.append(task)
            source_info.append(source)
        
        # Execute parallel searches
        all_results = await asyncio.gather(*search_tasks, return_exceptions=True)
        
        # Process results
        documents = []
        sources_with_results = 0
        total_raw_results = 0
        
        for source, results in zip(source_info, all_results):
            if isinstance(results, Exception):
                logger.error(f"Search failed for source {source.id}: {results}")
                continue
            
            if results:
                sources_with_results += 1
                total_raw_results += len(results)
            
            for result in results:
                doc_ref = DocumentReference(
                    id=str(result["chunk_id"]),
                    document_id=str(result["document_id"]),
                    title=result.get("document_title", "Unknown"),
                    source_type=source.type,
                    source_database=source.database,
                    excerpt=result.get("content", "")[:500],
                    full_content=result.get("content", ""),
                    similarity_score=result.get("rrf_score", result.get("similarity", 0)),
                    metadata={
                        "source_id": source.id,
                        "source_display_name": source.display_name,
                        "document_source": result.get("document_source", ""),
                        **(result.get("metadata", {}))
                    }
                )
                documents.append(doc_ref)
        
        # Sort by similarity and deduplicate
        documents = self._deduplicate_and_rank(documents, match_count)
        
        duration_ms = (time.time() - start_time) * 1000
        
        metadata = {
            "sources_searched": len(all_sources),
            "sources_with_results": sources_with_results,
            "total_raw_results": total_raw_results,
            "final_results": len(documents),
            "duration_ms": duration_ms,
            "sources": [{"id": s.id, "type": s.type, "database": s.database} for s in all_sources]
        }
        
        logger.info(
            f"Federated search for '{query[:50]}...' returned {len(documents)} results "
            f"from {sources_with_results}/{len(all_sources)} sources in {duration_ms:.0f}ms"
        )
        
        return documents, metadata
    
    def _deduplicate_and_rank(
        self,
        documents: List[DocumentReference],
        limit: int
    ) -> List[DocumentReference]:
        """Deduplicate documents and rank by relevance.
        
        Args:
            documents: List of document references (may have duplicates)
            limit: Maximum documents to return
        
        Returns:
            Deduplicated and sorted list
        """
        # Deduplicate by chunk ID (same chunk might appear from different sources)
        seen = set()
        unique = []
        
        for doc in documents:
            if doc.id not in seen:
                seen.add(doc.id)
                unique.append(doc)
        
        # Sort by similarity score
        unique.sort(key=lambda x: x.similarity_score, reverse=True)
        
        return unique[:limit]
    
    def assess_result_quality(self, documents: List[DocumentReference]) -> ResultQuality:
        """Assess the quality of search results.
        
        Args:
            documents: List of document references
        
        Returns:
            ResultQuality enum value
        """
        if not documents:
            return ResultQuality.EMPTY
        
        # Check average similarity
        avg_similarity = sum(d.similarity_score for d in documents) / len(documents)
        
        if len(documents) >= 5 and avg_similarity > 0.8:
            return ResultQuality.EXCELLENT
        elif len(documents) >= 3 and avg_similarity > 0.5:
            return ResultQuality.GOOD
        elif len(documents) >= 1:
            return ResultQuality.PARTIAL
        else:
            return ResultQuality.EMPTY


# Singleton instance for reuse
_federated_search: Optional[FederatedSearch] = None


def get_federated_search(mongo_client: Optional[AsyncIOMotorClient] = None) -> FederatedSearch:
    """Get or create federated search instance.
    
    Args:
        mongo_client: Optional MongoDB client
    
    Returns:
        FederatedSearch instance
    """
    global _federated_search
    if _federated_search is None:
        _federated_search = FederatedSearch(mongo_client)
    return _federated_search
