"""Chat router - Conversational AI with RAG and web browsing."""

import logging
import time
import uuid
import json
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from backend.models.schemas import ChatRequest, ChatResponse, SearchType
from backend.core.config import settings
from backend.tools.browser_tool import browse_url, BrowserToolResult

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
    top_results: Optional[List[dict]] = None  # Top result excerpts [{title, excerpt}]


@dataclass
class ToolOperation:
    """Details of a tool operation (browser, etc)."""
    tool_name: str
    tool_input: Dict[str, Any]
    success: bool
    result_summary: str = ""
    duration_ms: float = 0.0
    error: Optional[str] = None


@dataclass 
class SearchThinking:
    """Captures the agent's search thought process."""
    search_type: str  # "hybrid", "semantic", "text"
    query: str
    total_results: int
    operations: List[SearchOperation] = field(default_factory=list)
    total_duration_ms: float = 0.0


# Tool schemas for LLM function calling
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the internal knowledge base for information from ingested documents. Use this for questions about company data, uploaded documents, or internal information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant documents"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": "Fetch and read content from a web page URL. Use this when you need current information from the internet, to look up company registries, check websites, verify facts online, or find information not in the knowledge base.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to fetch (must start with http:// or https://)"
                    },
                    "extract_type": {
                        "type": "string",
                        "enum": ["text", "markdown", "links"],
                        "description": "Type of content to extract: 'text' for plain text, 'markdown' for formatted content, 'links' for page links. Default is 'text'."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web using a search engine to find relevant URLs. Use this when you need to find websites or pages about a topic but don't have a specific URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to find relevant web pages"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


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
            # Extract top result excerpts (first 3, max 150 chars each)
            top_vector_results = []
            for r in vector_results[:3]:
                excerpt = r["content"][:150].strip()
                if len(r["content"]) > 150:
                    excerpt += "..."
                top_vector_results.append({
                    "title": r["document_title"],
                    "excerpt": excerpt,
                    "score": round(r["similarity"], 3) if r["similarity"] else None
                })
            
            operations.append(SearchOperation(
                index_type="vector",
                index_name=settings.mongodb_vector_index,
                query=query,
                results_count=len(vector_results),
                duration_ms=round(vector_duration, 2),
                top_score=vector_results[0]["similarity"] if vector_results else None,
                top_results=top_vector_results if top_vector_results else None
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
            # Extract top result excerpts (first 3, max 150 chars each)
            top_text_results = []
            for r in text_results[:3]:
                excerpt = r["content"][:150].strip()
                if len(r["content"]) > 150:
                    excerpt += "..."
                top_text_results.append({
                    "title": r["document_title"],
                    "excerpt": excerpt,
                    "score": round(r["similarity"], 3) if r["similarity"] else None
                })
            
            operations.append(SearchOperation(
                index_type="text",
                index_name=settings.mongodb_text_index,
                query=query,
                results_count=len(text_results),
                duration_ms=round(text_duration, 2),
                top_score=text_results[0]["similarity"] if text_results else None,
                top_results=top_text_results if top_text_results else None
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


async def generate_response(message: str, context: str, conversation_history: list, db=None, tool_operations: List[ToolOperation] = None, model: str = None) -> tuple:
    """Generate LLM response using LiteLLM with tool calling support.
    
    Args:
        message: User's message
        context: Context from knowledge base search (can be empty)
        conversation_history: Previous conversation messages
        db: Database connection for knowledge base searches
        tool_operations: List to append tool operations to (for UI tracking)
        model: LLM model to use (defaults to settings.llm_model)
    
    Returns:
        tuple: (response_text, tokens_used, search_thinking, tool_operations)
    """
    import litellm
    
    if tool_operations is None:
        tool_operations = []
    
    # Use provided model or default
    llm_model = model or settings.llm_model
    
    search_thinking = None
    all_context_parts = []
    
    # Add any pre-existing context
    if context and context != "No relevant documents found.":
        all_context_parts.append(context)
    
    # System prompt with tool instructions
    system_prompt = """You are a helpful AI assistant with access to tools. You MUST use these tools to answer questions - NEVER respond without using tools first.

## Available Tools:

### 1. search_knowledge_base
Search internal documents using hybrid search (vector + text). The knowledge base contains documents about the user's company, projects, and internal information.
- **ALWAYS call this multiple times** with different queries (at least 2-3 searches per question)
- Start with broad context queries, then get specific
- If a search returns no results, TRY DIFFERENT TERMS - don't give up!
- When user says "my company" - search for company info, organization, business, etc.

### 2. browse_web  
Fetch and read content from a web URL. Use when you have a specific URL to visit.

### 3. web_search
Search the web using Brave Search to find URLs.

## CRITICAL RULES:

### Rule 1: NEVER give advice without searching first
**WRONG**: Explaining to user what they should do or look for
**RIGHT**: Actually searching and finding the information

### Rule 2: When user references "my company" or "our organization"
You MUST search the knowledge base first:
1. search_knowledge_base("company name organization")
2. search_knowledge_base("business overview about us")
3. Then search for the specific topic they asked about

### Rule 3: Don't give up after one search
- If search returns empty, try 2-3 MORE searches with different terms
- Try synonyms: "accounting" → "finance", "invoices", "bookkeeping"
- Try broader terms: "vendor" → "supplier", "partner", "company"

### Rule 4: Step-by-step questions require step-by-step tool usage
When user asks "go step by step":
1. Search for each piece of information separately
2. Use multiple tool calls in sequence
3. Gather all info before responding

## Example - User asks: "find the owner of our accounting company"
DO THIS:
1. search_knowledge_base("company organization name") - find company name
2. search_knowledge_base("accounting finance invoices") - find accounting docs
3. search_knowledge_base("accounting firm vendor partner") - find accounting vendor
4. web_search("[accounting company name] owner") - search web for owner
5. browse_web("[company website]/about") - check their website

DO NOT: Explain what the user should do. Actually DO the searches.

Remember: You have access to the user's company documents. Search them! Multiple times! With different queries!"""
    
    # Build messages
    messages = [{"role": "system", "content": system_prompt}]
    
    # Add conversation history
    for msg in conversation_history[-10:]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    
    # Add current message
    messages.append({"role": "user", "content": message})
    
    # Maximum tool call iterations to prevent infinite loops
    max_iterations = settings.agent_max_tool_iterations
    iteration = 0
    total_tokens = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        try:
            # Call LLM with tools
            logger.info(f"Calling LLM: model={llm_model}, iteration={iteration}, tools={len(TOOLS_SCHEMA)} defined")
            
            # Handle newer OpenAI models that require max_completion_tokens
            llm_params = {
                "model": llm_model,
                "messages": messages,
                "tools": TOOLS_SCHEMA,
                "tool_choice": "auto",
                "temperature": 0.7,
                "api_key": settings.llm_api_key,
                "api_base": settings.llm_base_url if settings.llm_base_url else None,
            }
            
            # Check if this is a newer OpenAI model
            if "gpt-5" in llm_model.lower() or "gpt-4o" in llm_model.lower():
                llm_params["max_completion_tokens"] = 2000
            else:
                llm_params["max_tokens"] = 2000
            
            response = await litellm.acompletion(**llm_params)
            
            if response.usage:
                total_tokens += response.usage.total_tokens
            
            assistant_message = response.choices[0].message
            
            # Log whether tools were called
            if assistant_message.tool_calls:
                logger.info(f"LLM requested {len(assistant_message.tool_calls)} tool calls")
                # Add assistant message with tool calls to conversation
                messages.append({
                    "role": "assistant",
                    "content": assistant_message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        }
                        for tc in assistant_message.tool_calls
                    ]
                })
                
                # Process each tool call
                for tool_call in assistant_message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}
                    
                    tool_start = time.time()
                    tool_result = ""
                    tool_success = True
                    tool_error = None
                    
                    logger.info(f"Tool call: {tool_name} with args: {tool_args}")
                    
                    if tool_name == "search_knowledge_base":
                        # Perform knowledge base search
                        if db:
                            query = tool_args.get("query", message)
                            results, thinking = await perform_search(
                                db, query, SearchType.HYBRID, 10
                            )
                            search_thinking = thinking
                            
                            if results:
                                context_parts = []
                                for r in results[:5]:
                                    context_parts.append(f"[{r['document_title']}]: {r['content'][:500]}")
                                tool_result = "\n\n".join(context_parts)
                                all_context_parts.append(tool_result)
                            else:
                                tool_result = "No relevant documents found in the knowledge base."
                        else:
                            tool_result = "Knowledge base not available."
                            tool_success = False
                    
                    elif tool_name == "browse_web":
                        # Use browser tool
                        url = tool_args.get("url", "")
                        extract_type = tool_args.get("extract_type", "text")
                        
                        if url:
                            browser_result: BrowserToolResult = await browse_url(url, extract_type)
                            if browser_result.success:
                                tool_result = f"Title: {browser_result.title}\n\nContent:\n{browser_result.content[:3000]}"
                                if browser_result.links:
                                    links_text = "\n".join([f"- {l['text']}: {l['href']}" for l in browser_result.links[:10]])
                                    tool_result += f"\n\nLinks found:\n{links_text}"
                            else:
                                tool_result = f"Failed to fetch page: {browser_result.error}"
                                tool_success = False
                                tool_error = browser_result.error
                        else:
                            tool_result = "No URL provided."
                            tool_success = False
                            tool_error = "No URL provided"
                    
                    elif tool_name == "web_search":
                        # Web search using Brave Search API
                        query = tool_args.get("query", "")
                        if query:
                            try:
                                import httpx
                                brave_api_key = settings.brave_search_api_key
                                
                                async with httpx.AsyncClient() as http_client:
                                    search_response = await http_client.get(
                                        "https://api.search.brave.com/res/v1/web/search",
                                        params={"q": query, "count": 10},
                                        headers={
                                            "X-Subscription-Token": brave_api_key,
                                            "Accept": "application/json"
                                        },
                                        timeout=15.0
                                    )
                                    
                                    if search_response.status_code == 200:
                                        data = search_response.json()
                                        web_results = data.get("web", {}).get("results", [])
                                        
                                        if web_results:
                                            formatted_results = []
                                            for i, result in enumerate(web_results[:8], 1):
                                                title = result.get("title", "No title")
                                                result_url = result.get("url", "")
                                                description = result.get("description", "No description")
                                                formatted_results.append(
                                                    f"{i}. **{title}**\n   URL: {result_url}\n   {description}"
                                                )
                                            
                                            tool_result = f"Web search results for '{query}':\n\n" + "\n\n".join(formatted_results)
                                            tool_result += "\n\nYou can use browse_web to visit any of these URLs for more details."
                                        else:
                                            tool_result = f"No results found for '{query}'. Try different keywords."
                                    else:
                                        tool_result = f"Search API error (status {search_response.status_code}). Try browsing specific URLs instead."
                                        tool_success = False
                                        tool_error = f"Brave API returned status {search_response.status_code}"
                                        
                            except Exception as e:
                                logger.error(f"Brave Search error: {e}")
                                tool_result = f"Search failed: {str(e)}. Try browsing specific URLs instead."
                                tool_success = False
                                tool_error = str(e)
                        else:
                            tool_result = "No search query provided."
                            tool_success = False
                            tool_error = "No query provided"
                    
                    else:
                        tool_result = f"Unknown tool: {tool_name}"
                        tool_success = False
                        tool_error = f"Unknown tool: {tool_name}"
                    
                    tool_duration = (time.time() - tool_start) * 1000
                    
                    # Record tool operation
                    result_summary = tool_result[:200] + "..." if len(tool_result) > 200 else tool_result
                    tool_operations.append(ToolOperation(
                        tool_name=tool_name,
                        tool_input=tool_args,
                        success=tool_success,
                        result_summary=result_summary,
                        duration_ms=round(tool_duration, 2),
                        error=tool_error
                    ))
                    
                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": tool_result
                    })
                
                # Continue loop to get final response after tool calls
                continue
            
            else:
                # No tool calls - we have a final response
                logger.info(f"LLM returned direct response without tool calls")
                return (
                    assistant_message.content or "",
                    total_tokens,
                    search_thinking,
                    tool_operations
                )
        
        except Exception as e:
            logger.error(f"Error in generate_response: {e}")
            return (
                f"I encountered an error while processing your request: {str(e)}",
                total_tokens,
                search_thinking,
                tool_operations
            )
    
    # Max iterations reached
    return (
        "I apologize, but I couldn't complete the request within the allowed number of steps.",
        total_tokens,
        search_thinking,
        tool_operations
    )


@router.post("", response_model=ChatResponse)
@router.post("/", response_model=ChatResponse)
async def chat(request: Request, chat_request: ChatRequest):
    """
    Chat with the RAG agent.
    
    The agent can:
    - Search the knowledge base for internal documents
    - Browse web pages to look up information online
    - Answer questions using its general knowledge
    """
    start_time = time.time()
    
    db = request.app.state.db
    
    # Get or create conversation
    conversation_id = chat_request.conversation_id or str(uuid.uuid4())
    if conversation_id not in _conversations:
        _conversations[conversation_id] = []
    
    conversation_history = _conversations[conversation_id]
    
    # Let the agent decide what tools to use
    # Pass empty context initially - agent will call search_knowledge_base if needed
    tool_operations = []
    response_text, tokens_used, search_thinking, tool_operations = await generate_response(
        chat_request.message,
        "",  # Empty context - agent will search if needed
        conversation_history,
        db=db,
        tool_operations=tool_operations
    )
    
    # Build sources from search_thinking if available
    sources = []
    if search_thinking and search_thinking.operations:
        for op in search_thinking.operations:
            if op.top_results:
                for r in op.top_results:
                    sources.append({
                        "title": r.get("title", "Unknown"),
                        "source": op.index_name,
                        "relevance": r.get("score", 0.0),
                        "excerpt": r.get("excerpt", "")
                    })
    
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
        sources=sources if chat_request.include_sources and sources else None,
        search_performed=search_thinking is not None and search_thinking.total_results > 0,
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
