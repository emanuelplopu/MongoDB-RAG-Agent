"""Chat router - Conversational AI with RAG."""

import logging
import time
import uuid
from typing import Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ChatResponse, SearchType
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Simple in-memory conversation store (use Redis in production)
_conversations: dict = {}


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


from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional


@dataclass
class SearchOperation:
    """Details of a single search operation."""
    index_type: str  # "vector" or "text"
    index_name: str
    query: str
    results_count: int
    duration_ms: float
    top_score: Optional[float] = None


@dataclass 
class SearchThinking:
    """Captures the agent's search thought process."""
    search_type: str  # "hybrid", "semantic", "text"
    query: str
    total_results: int
    operations: List[SearchOperation] = field(default_factory=list)
    total_duration_ms: float = 0.0


async def perform_search(db, query: str, search_type: SearchType, match_count: int) -> tuple:
    """Perform search on the knowledge base and return results with thinking details.
    
    Returns:
        tuple: (results_list, SearchThinking object with operation details)
    """
    collection = db.chunks_collection
    
    results = []
    operations = []
    total_start = time.time()
    
    try:
        if search_type in [SearchType.SEMANTIC, SearchType.HYBRID]:
            # Vector search
            vector_start = time.time()
            query_embedding = await get_embedding(query)
            
            vector_pipeline = [
                {
                    "$vectorSearch": {
                        "index": settings.mongodb_vector_index,
                        "queryVector": query_embedding,
                        "path": "embedding",
                        "numCandidates": 100,
                        "limit": match_count * 2
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
                        "document_title": "$doc_info.title",
                        "document_source": "$doc_info.source"
                    }
                }
            ]
            
            cursor = collection.aggregate(vector_pipeline)
            vector_results = []
            async for doc in cursor:
                vector_results.append({
                    "chunk_id": str(doc["chunk_id"]),
                    "document_id": str(doc["document_id"]),
                    "content": doc["content"],
                    "similarity": doc["similarity"],
                    "document_title": doc["document_title"],
                    "document_source": doc["document_source"]
                })
            
            vector_duration = (time.time() - vector_start) * 1000
            operations.append(SearchOperation(
                index_type="vector",
                index_name=settings.mongodb_vector_index,
                query=query,
                results_count=len(vector_results),
                duration_ms=round(vector_duration, 2),
                top_score=vector_results[0]["similarity"] if vector_results else None
            ))
            results.extend(vector_results)
        
        if search_type in [SearchType.TEXT, SearchType.HYBRID]:
            # Text search
            text_start = time.time()
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
                {"$limit": match_count * 2},
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
                        "document_title": "$doc_info.title",
                        "document_source": "$doc_info.source"
                    }
                }
            ]
            
            cursor = collection.aggregate(text_pipeline)
            text_results = []
            async for doc in cursor:
                text_results.append({
                    "chunk_id": str(doc["chunk_id"]),
                    "document_id": str(doc["document_id"]),
                    "content": doc["content"],
                    "similarity": doc["similarity"],
                    "document_title": doc["document_title"],
                    "document_source": doc["document_source"]
                })
            
            text_duration = (time.time() - text_start) * 1000
            operations.append(SearchOperation(
                index_type="text",
                index_name=settings.mongodb_text_index,
                query=query,
                results_count=len(text_results),
                duration_ms=round(text_duration, 2),
                top_score=text_results[0]["similarity"] if text_results else None
            ))
            results.extend(text_results)
        
        # Deduplicate and limit results
        seen = set()
        unique_results = []
        for r in results:
            if r["chunk_id"] not in seen:
                seen.add(r["chunk_id"])
                unique_results.append(r)
        
        final_results = unique_results[:match_count]
        total_duration = (time.time() - total_start) * 1000
        
        thinking = SearchThinking(
            search_type=search_type.value,
            query=query,
            total_results=len(final_results),
            operations=operations,
            total_duration_ms=round(total_duration, 2)
        )
        
        return final_results, thinking
        
    except Exception as e:
        logger.error(f"Search failed: {e}")
        # Return empty results with error in thinking
        thinking = SearchThinking(
            search_type=search_type.value,
            query=query,
            total_results=0,
            operations=[],
            total_duration_ms=0.0
        )
        return [], thinking


async def generate_response(message: str, context: str, conversation_history: list) -> tuple:
    """Generate LLM response using LiteLLM for unified model handling."""
    import litellm
    
    # Build messages
    messages = [
        {
            "role": "system",
            "content": f"""You are a helpful assistant with access to a knowledge base. 
Use the following context to answer questions. If the context doesn't contain relevant information, 
say so and provide what help you can.

CONTEXT FROM KNOWLEDGE BASE:
{context}

Always cite the document source when referencing information from the context."""
        }
    ]
    
    # Add conversation history
    for msg in conversation_history[-10:]:  # Last 10 messages
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Add current message
    messages.append({"role": "user", "content": message})
    
    # Use LiteLLM - automatically handles max_tokens vs max_completion_tokens
    response = await litellm.acompletion(
        model=settings.llm_model,
        messages=messages,
        temperature=0.7,
        max_tokens=2000,
        api_key=settings.llm_api_key,
        api_base=settings.llm_base_url if settings.llm_base_url else None,
    )
    
    return (
        response.choices[0].message.content,
        response.usage.total_tokens if response.usage else None
    )


@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, chat_request: ChatRequest):
    """
    Chat with the RAG agent.
    
    Sends a message to the AI assistant which will search the knowledge base
    and generate a contextual response.
    """
    start_time = time.time()
    
    db = request.app.state.db
    
    # Get or create conversation
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())
    if conversation_id not in _conversations:
        _conversations[conversation_id] = []
    
    conversation_history = _conversations[conversation_id]
    
    # Perform search
    search_results, search_thinking = await perform_search(
        db,
        chat_request.message,
        chat_request.search_type,
        chat_request.match_count
    )
    
    # Build context from search results
    context_parts = []
    sources = []
    
    for result in search_results:
        context_parts.append(
            f"[Source: {result['document_title']}]\n{result['content']}"
        )
        if chat_request.include_sources:
            sources.append({
                "title": result["document_title"],
                "source": result["document_source"],
                "relevance": result["similarity"],
                "excerpt": result["content"][:200] + "..." if len(result["content"]) > 200 else result["content"]
            })
    
    context = "\n\n---\n\n".join(context_parts) if context_parts else "No relevant documents found."
    
    # Generate response
    response_text, tokens_used = await generate_response(
        chat_request.message,
        context,
        conversation_history
    )
    
    # Update conversation history
    conversation_history.append({"role": "user", "content": chat_request.message})
    conversation_history.append({"role": "assistant", "content": response_text})
    
    # Keep conversation history manageable
    if len(conversation_history) > 50:
        _conversations[conversation_id] = conversation_history[-50:]
    
    processing_time = (time.time() - start_time) * 1000
    
    return ChatResponse(
        message=response_text,
        conversation_id=conversation_id,
        sources=sources if chat_request.include_sources else None,
        search_performed=len(search_results) > 0,
        model=settings.llm_model,
        tokens_used=tokens_used,
        processing_time_ms=processing_time
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """Get conversation history."""
    if conversation_id not in _conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    return {
        "conversation_id": conversation_id,
        "messages": _conversations[conversation_id]
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """Delete a conversation."""
    if conversation_id in _conversations:
        del _conversations[conversation_id]
    
    return {"success": True, "message": "Conversation deleted"}


@router.get("/conversations")
async def list_conversations():
    """List all active conversations."""
    return {
        "conversations": [
            {
                "id": cid,
                "message_count": len(msgs)
            }
            for cid, msgs in _conversations.items()
        ]
    }
