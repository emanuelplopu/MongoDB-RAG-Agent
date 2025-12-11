"""Search router - Direct search endpoints."""

import logging
import time
from fastapi import APIRouter, HTTPException, Request

from backend.models.schemas import (
    SearchRequest, SearchResponse, SearchResultItem, SearchType
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_embedding(text: str) -> list:
    """Generate embedding for text."""
    from openai import AsyncOpenAI
    
    client = AsyncOpenAI(
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url
    )
    
    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=text
    )
    
    return response.data[0].embedding


@router.post("/semantic", response_model=SearchResponse)
async def semantic_search(request: Request, search_request: SearchRequest):
    """
    Perform semantic (vector) search.
    
    Uses embedding similarity to find conceptually related content.
    """
    start_time = time.time()
    
    db = request.app.state.db
    collection = db.chunks_collection
    
    try:
        # Generate query embedding
        query_embedding = await get_embedding(search_request.query)
        
        # Vector search pipeline
        pipeline = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index,
                    "queryVector": query_embedding,
                    "path": "embedding",
                    "numCandidates": 100,
                    "limit": search_request.match_count
                }
            },
            {
                "$lookup": {
                    "from": settings.mongodb_collection_documents,
                    "localField": "document_id",
                    "foreignField": "_id",
                    "as": "doc_info"
                }
            },
            {"$unwind": "$doc_info"},
            {
                "$project": {
                    "chunk_id": "$_id",
                    "document_id": 1,
                    "content": 1,
                    "similarity": {"$meta": "vectorSearchScore"},
                    "metadata": 1,
                    "document_title": "$doc_info.title",
                    "document_source": "$doc_info.source"
                }
            }
        ]
        
        results = []
        cursor = collection.aggregate(pipeline)
        async for doc in cursor:
            results.append(SearchResultItem(
                chunk_id=str(doc["chunk_id"]),
                document_id=str(doc["document_id"]),
                document_title=doc["document_title"],
                document_source=doc["document_source"],
                content=doc["content"],
                similarity=doc["similarity"],
                metadata=doc.get("metadata", {})
            ))
        
        processing_time = (time.time() - start_time) * 1000
        
        return SearchResponse(
            query=search_request.query,
            search_type="semantic",
            results=results,
            total_results=len(results),
            processing_time_ms=processing_time
        )
        
    except Exception as e:
        logger.error(f"Semantic search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/text", response_model=SearchResponse)
async def text_search(request: Request, search_request: SearchRequest):
    """
    Perform full-text search.
    
    Uses keyword and fuzzy matching to find content.
    """
    start_time = time.time()
    
    db = request.app.state.db
    collection = db.chunks_collection
    
    try:
        # Text search pipeline
        pipeline = [
            {
                "$search": {
                    "index": settings.mongodb_text_index,
                    "text": {
                        "query": search_request.query,
                        "path": "content",
                        "fuzzy": {
                            "maxEdits": 2,
                            "prefixLength": 3
                        }
                    }
                }
            },
            {"$limit": search_request.match_count},
            {
                "$lookup": {
                    "from": settings.mongodb_collection_documents,
                    "localField": "document_id",
                    "foreignField": "_id",
                    "as": "doc_info"
                }
            },
            {"$unwind": "$doc_info"},
            {
                "$project": {
                    "chunk_id": "$_id",
                    "document_id": 1,
                    "content": 1,
                    "similarity": {"$meta": "searchScore"},
                    "metadata": 1,
                    "document_title": "$doc_info.title",
                    "document_source": "$doc_info.source"
                }
            }
        ]
        
        results = []
        cursor = collection.aggregate(pipeline)
        async for doc in cursor:
            results.append(SearchResultItem(
                chunk_id=str(doc["chunk_id"]),
                document_id=str(doc["document_id"]),
                document_title=doc["document_title"],
                document_source=doc["document_source"],
                content=doc["content"],
                similarity=doc["similarity"],
                metadata=doc.get("metadata", {})
            ))
        
        processing_time = (time.time() - start_time) * 1000
        
        return SearchResponse(
            query=search_request.query,
            search_type="text",
            results=results,
            total_results=len(results),
            processing_time_ms=processing_time
        )
        
    except Exception as e:
        logger.error(f"Text search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/hybrid", response_model=SearchResponse)
async def hybrid_search(request: Request, search_request: SearchRequest):
    """
    Perform hybrid search combining semantic and text search.
    
    Uses Reciprocal Rank Fusion (RRF) to merge results from both methods.
    """
    start_time = time.time()
    
    db = request.app.state.db
    collection = db.chunks_collection
    
    try:
        # Fetch more results for RRF
        fetch_count = search_request.match_count * 2
        
        # Run semantic search
        query_embedding = await get_embedding(search_request.query)
        
        vector_pipeline = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index,
                    "queryVector": query_embedding,
                    "path": "embedding",
                    "numCandidates": 100,
                    "limit": fetch_count
                }
            },
            {
                "$lookup": {
                    "from": settings.mongodb_collection_documents,
                    "localField": "document_id",
                    "foreignField": "_id",
                    "as": "doc_info"
                }
            },
            {"$unwind": "$doc_info"},
            {
                "$project": {
                    "chunk_id": "$_id",
                    "document_id": 1,
                    "content": 1,
                    "similarity": {"$meta": "vectorSearchScore"},
                    "metadata": 1,
                    "document_title": "$doc_info.title",
                    "document_source": "$doc_info.source"
                }
            }
        ]
        
        semantic_results = []
        cursor = collection.aggregate(vector_pipeline)
        async for doc in cursor:
            semantic_results.append(doc)
        
        # Run text search
        text_pipeline = [
            {
                "$search": {
                    "index": settings.mongodb_text_index,
                    "text": {
                        "query": search_request.query,
                        "path": "content",
                        "fuzzy": {"maxEdits": 2, "prefixLength": 3}
                    }
                }
            },
            {"$limit": fetch_count},
            {
                "$lookup": {
                    "from": settings.mongodb_collection_documents,
                    "localField": "document_id",
                    "foreignField": "_id",
                    "as": "doc_info"
                }
            },
            {"$unwind": "$doc_info"},
            {
                "$project": {
                    "chunk_id": "$_id",
                    "document_id": 1,
                    "content": 1,
                    "similarity": {"$meta": "searchScore"},
                    "metadata": 1,
                    "document_title": "$doc_info.title",
                    "document_source": "$doc_info.source"
                }
            }
        ]
        
        text_results = []
        cursor = collection.aggregate(text_pipeline)
        async for doc in cursor:
            text_results.append(doc)
        
        # Apply Reciprocal Rank Fusion
        k = 60  # RRF constant
        rrf_scores = {}
        result_map = {}
        
        for rank, doc in enumerate(semantic_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            result_map[chunk_id] = doc
        
        for rank, doc in enumerate(text_results):
            chunk_id = str(doc["chunk_id"])
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1.0 / (k + rank)
            if chunk_id not in result_map:
                result_map[chunk_id] = doc
        
        # Sort by RRF score
        sorted_results = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
        
        results = []
        for chunk_id, score in sorted_results[:search_request.match_count]:
            doc = result_map[chunk_id]
            results.append(SearchResultItem(
                chunk_id=chunk_id,
                document_id=str(doc["document_id"]),
                document_title=doc["document_title"],
                document_source=doc["document_source"],
                content=doc["content"],
                similarity=score,
                metadata=doc.get("metadata", {})
            ))
        
        processing_time = (time.time() - start_time) * 1000
        
        return SearchResponse(
            query=search_request.query,
            search_type="hybrid",
            results=results,
            total_results=len(results),
            processing_time_ms=processing_time
        )
        
    except Exception as e:
        logger.error(f"Hybrid search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("", response_model=SearchResponse)
@router.post("/", response_model=SearchResponse)
async def search(request: Request, search_request: SearchRequest):
    """
    Perform search with specified type.
    
    Unified search endpoint that routes to appropriate search method.
    """
    if search_request.search_type == SearchType.SEMANTIC:
        return await semantic_search(request, search_request)
    elif search_request.search_type == SearchType.TEXT:
        return await text_search(request, search_request)
    else:
        return await hybrid_search(request, search_request)
