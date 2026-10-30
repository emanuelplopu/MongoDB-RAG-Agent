"""Search router - Direct search endpoints."""

import logging
import time
from fastapi import APIRouter, HTTPException, Request

from backend.models.schemas import (
    SearchRequest, SearchResponse, SearchResultItem, SearchType
)
from backend.core.config import settings
from backend.services.identifier_search import (
    COPY_FIELDS_PROJECTION,
    DEFAULT_NUM_CANDIDATES,
    detect_identifiers,
    fetch_exact_hits,
    identifier_fetch_count,
    merge_identifier_results,
    vector_num_candidates,
)
from backend.services.query_embedding import embed_query
from backend.services.text_query import QueryTooLongError, run_text_search, text_search_stage

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_embedding(text: str) -> list:
    """Embed a search query with the configured model (query prefix applied)."""
    return await embed_query(text)


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
    
    Uses keyword and fuzzy matching to find content. Long queries are bounded
    by ``backend/services/text_query`` (fuzzy on the first 15 distinct terms,
    at most 60 terms) and retried once without fuzziness when Atlas Search
    still rejects them with ``maxClauseCount``; a query that fails both ways
    gets 400 instead of 500.
    """
    start_time = time.time()

    db = request.app.state.db
    collection = db.chunks_collection

    try:
        # Text search pipeline
        def text_pipeline(fuzzy: bool) -> list:
            return [
                text_search_stage(search_request.query, index=settings.mongodb_text_index, fuzzy=fuzzy),
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
        for doc in await run_text_search(collection, text_pipeline, query=search_request.query):
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

    except QueryTooLongError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Text search failed: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/hybrid", response_model=SearchResponse)
async def hybrid_search(request: Request, search_request: SearchRequest):
    """
    Perform hybrid search combining semantic and text search.
    
    Uses Reciprocal Rank Fusion (RRF) to merge results from both methods.

    A query that names a legal identifier (``FN 404945h``, ``EZ 52 KG 11029``,
    ``13 U 39/16v``) takes the identifier path of
    ``backend/services/identifier_search``: wider candidate windows, one exact
    phrase query pinned ahead of the fused list, copies collapsed. Ordinary
    queries are unaffected.
    """
    start_time = time.time()

    db = request.app.state.db
    collection = db.chunks_collection

    try:
        identifiers = detect_identifiers(search_request.query)
        if identifiers:
            fetch_count = identifier_fetch_count(search_request.match_count)
            num_candidates = vector_num_candidates(fetch_count)
            copy_fields = dict(COPY_FIELDS_PROJECTION)
        else:
            # Fetch more results for RRF
            fetch_count = search_request.match_count * 2
            num_candidates = DEFAULT_NUM_CANDIDATES
            copy_fields = {}

        # Run semantic search
        query_embedding = await get_embedding(search_request.query)

        vector_pipeline = [
            {
                "$vectorSearch": {
                    "index": settings.mongodb_vector_index,
                    "queryVector": query_embedding,
                    "path": "embedding",
                    "numCandidates": num_candidates,
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
                    "document_source": "$doc_info.source",
                    **copy_fields
                }
            }
        ]
        
        semantic_results = []
        cursor = collection.aggregate(vector_pipeline)
        async for doc in cursor:
            semantic_results.append(doc)
        
        # Run text search (bounded fuzzy operator, one retry without fuzziness
        # on maxClauseCount; see backend/services/text_query.py)
        def text_pipeline(fuzzy: bool) -> list:
            return [
                text_search_stage(search_request.query, index=settings.mongodb_text_index, fuzzy=fuzzy),
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
                        "document_source": "$doc_info.source",
                        **copy_fields
                    }
                }
            ]

        text_results = await run_text_search(
            collection, text_pipeline, query=search_request.query, label="Hybrid text search"
        )

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

        fused = []
        for chunk_id, score in sorted_results:
            doc = result_map[chunk_id]
            doc["similarity"] = score
            fused.append(doc)

        if identifiers:
            exact_hits = await fetch_exact_hits(
                collection,
                identifiers,
                text_index=settings.mongodb_text_index,
                docs_collection=settings.mongodb_collection_documents,
                documents=db.documents_collection,
            )
            final_results = merge_identifier_results(exact_hits, fused, search_request.match_count)
            logger.info(
                "Identifier query %s: %d exact hit(s), %d fused candidates",
                [identifier.canonical for identifier in identifiers], len(exact_hits), len(fused),
            )
        else:
            final_results = fused[:search_request.match_count]

        results = []
        for doc in final_results:
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
            search_type="hybrid",
            results=results,
            total_results=len(results),
            processing_time_ms=processing_time
        )

    except QueryTooLongError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
