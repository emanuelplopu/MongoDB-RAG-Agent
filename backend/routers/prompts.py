"""Prompt template management router.

Provides CRUD operations for system prompts and tools with versioning,
testing capabilities, and version comparison.
"""

import logging
import time
import difflib
from datetime import datetime
from typing import Optional
from bson import ObjectId

from fastapi import APIRouter, Request, HTTPException, Depends
import litellm

from backend.models.schemas import (
    PromptTemplate, PromptTemplateCreate, PromptTemplateUpdate,
    PromptVersion, PromptVersionCreate,
    PromptTestRequest, PromptTestResponse,
    PromptCompareRequest, PromptCompareResponse,
    PromptTemplateListResponse, ToolSchema,
    SuccessResponse
)
from backend.core.config import get_settings
from backend.routers.auth import get_current_user, UserResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prompts"])
settings = get_settings()


def get_prompts_collection(request: Request):
    """Get the prompt_templates collection."""
    db = request.app.state.db
    return db.db.prompt_templates


def tool_schema_to_openai_format(tool: ToolSchema) -> dict:
    """Convert ToolSchema to OpenAI function calling format."""
    properties = {}
    required = []
    
    for param in tool.parameters:
        properties[param.name] = {
            "type": param.type,
            "description": param.description
        }
        if param.required:
            required.append(param.name)
    
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required
            }
        }
    }


def openai_format_to_tool_schema(tool_dict: dict) -> ToolSchema:
    """Convert OpenAI function format to ToolSchema."""
    func = tool_dict.get("function", {})
    params = func.get("parameters", {})
    properties = params.get("properties", {})
    required = params.get("required", [])
    
    from backend.models.schemas import ToolParameterSchema
    
    parameters = []
    for name, prop in properties.items():
        parameters.append(ToolParameterSchema(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required
        ))
    
    return ToolSchema(
        name=func.get("name", ""),
        description=func.get("description", ""),
        parameters=parameters,
        enabled=True
    )


# ============== CRUD Operations ==============

@router.post("/reinitialize", response_model=SuccessResponse)
async def reinitialize_prompts(
    request: Request,
    force_update: bool = False,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Re-initialize prompt templates with defaults.
    
    Args:
        force_update: If True, updates existing prompts to latest defaults
                     (creates new versions, preserving history)
    """
    db = request.app.state.db
    
    await initialize_default_templates(db, force_update=force_update)
    
    if force_update:
        return SuccessResponse(message="Prompts updated to latest defaults")
    return SuccessResponse(message="Missing prompts initialized")


@router.get("", response_model=PromptTemplateListResponse)
async def list_templates(
    request: Request,
    category: Optional[str] = None,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """List all prompt templates."""
    collection = get_prompts_collection(request)
    
    query = {}
    if category:
        query["category"] = category
    
    cursor = collection.find(query).sort("updated_at", -1)
    templates = []
    
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        templates.append(PromptTemplate(**doc))
    
    return PromptTemplateListResponse(templates=templates, total=len(templates))


@router.get("/{template_id}", response_model=PromptTemplate)
async def get_template(
    template_id: str,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Get a specific prompt template."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    doc["id"] = str(doc.pop("_id"))
    return PromptTemplate(**doc)


@router.post("", response_model=PromptTemplate)
async def create_template(
    template_data: PromptTemplateCreate,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Create a new prompt template with initial version."""
    collection = get_prompts_collection(request)
    
    # Create initial version
    initial_version = PromptVersion(
        version=1,
        system_prompt=template_data.system_prompt,
        tools=template_data.tools,
        created_at=datetime.now(),
        created_by=current_user.email if current_user else None,
        notes=template_data.notes,
        is_active=True
    )
    
    template = PromptTemplate(
        name=template_data.name,
        description=template_data.description,
        category=template_data.category,
        versions=[initial_version],
        active_version=1,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        created_by=current_user.email if current_user else None
    )
    
    doc = template.model_dump(exclude={"id"})
    result = await collection.insert_one(doc)
    
    template.id = str(result.inserted_id)
    logger.info(f"Created prompt template: {template.name} (id={template.id})")
    
    return template


@router.patch("/{template_id}", response_model=PromptTemplate)
async def update_template(
    template_id: str,
    update_data: PromptTemplateUpdate,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Update prompt template metadata (not versions)."""
    collection = get_prompts_collection(request)
    
    update_fields = {"updated_at": datetime.now()}
    if update_data.name is not None:
        update_fields["name"] = update_data.name
    if update_data.description is not None:
        update_fields["description"] = update_data.description
    if update_data.category is not None:
        update_fields["category"] = update_data.category
    
    result = await collection.update_one(
        {"_id": ObjectId(template_id)},
        {"$set": update_fields}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    doc["id"] = str(doc.pop("_id"))
    return PromptTemplate(**doc)


@router.delete("/{template_id}", response_model=SuccessResponse)
async def delete_template(
    template_id: str,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Delete a prompt template."""
    collection = get_prompts_collection(request)
    
    result = await collection.delete_one({"_id": ObjectId(template_id)})
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Template not found")
    
    logger.info(f"Deleted prompt template: {template_id}")
    return SuccessResponse(message=f"Template {template_id} deleted")


# ============== Version Management ==============

@router.post("/{template_id}/versions", response_model=PromptTemplate)
async def create_version(
    template_id: str,
    version_data: PromptVersionCreate,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Create a new version of a prompt template."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Calculate next version number
    versions = doc.get("versions", [])
    next_version = max([v.get("version", 0) for v in versions], default=0) + 1
    
    new_version = PromptVersion(
        version=next_version,
        system_prompt=version_data.system_prompt,
        tools=version_data.tools,
        created_at=datetime.now(),
        created_by=current_user.email if current_user else None,
        notes=version_data.notes,
        is_active=False
    )
    
    await collection.update_one(
        {"_id": ObjectId(template_id)},
        {
            "$push": {"versions": new_version.model_dump()},
            "$set": {"updated_at": datetime.now()}
        }
    )
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    doc["id"] = str(doc.pop("_id"))
    
    logger.info(f"Created version {next_version} for template {template_id}")
    return PromptTemplate(**doc)


@router.post("/{template_id}/versions/{version}/activate", response_model=PromptTemplate)
async def activate_version(
    template_id: str,
    version: int,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Activate a specific version of a prompt template."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    versions = doc.get("versions", [])
    version_exists = any(v.get("version") == version for v in versions)
    if not version_exists:
        raise HTTPException(status_code=404, detail=f"Version {version} not found")
    
    # Deactivate all versions and activate the specified one
    for v in versions:
        v["is_active"] = (v.get("version") == version)
    
    await collection.update_one(
        {"_id": ObjectId(template_id)},
        {
            "$set": {
                "versions": versions,
                "active_version": version,
                "updated_at": datetime.now()
            }
        }
    )
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    doc["id"] = str(doc.pop("_id"))
    
    logger.info(f"Activated version {version} for template {template_id}")
    return PromptTemplate(**doc)


@router.get("/{template_id}/versions/{version}")
async def get_version(
    template_id: str,
    version: int,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Get a specific version of a prompt template."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    versions = doc.get("versions", [])
    for v in versions:
        if v.get("version") == version:
            return PromptVersion(**v)
    
    raise HTTPException(status_code=404, detail=f"Version {version} not found")


# ============== Testing ==============

@router.post("/test", response_model=PromptTestResponse)
async def test_prompt(
    test_data: PromptTestRequest,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Test a prompt template with a sample message."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(test_data.template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    # Get the version to test
    versions = doc.get("versions", [])
    target_version = test_data.version or doc.get("active_version", 1)
    
    version_data = None
    for v in versions:
        if v.get("version") == target_version:
            version_data = v
            break
    
    if not version_data:
        raise HTTPException(status_code=404, detail=f"Version {target_version} not found")
    
    # Build tools in OpenAI format
    tools = []
    for tool_data in version_data.get("tools", []):
        tool = ToolSchema(**tool_data) if isinstance(tool_data, dict) else tool_data
        if tool.enabled if hasattr(tool, 'enabled') else tool_data.get('enabled', True):
            tools.append(tool_schema_to_openai_format(
                tool if isinstance(tool, ToolSchema) else ToolSchema(**tool_data)
            ))
    
    # Build messages
    messages = [
        {"role": "system", "content": version_data.get("system_prompt", "")},
        {"role": "user", "content": test_data.test_message}
    ]
    
    start_time = time.time()
    
    try:
        # Handle newer OpenAI models that require max_completion_tokens
        llm_params = {
            "model": settings.llm_model,
            "messages": messages,
            "tools": tools if tools else None,
            "tool_choice": "auto" if tools else None,
            "temperature": 0.7,
            "api_key": settings.llm_api_key,
            "api_base": settings.llm_base_url if settings.llm_base_url else None,
        }
        
        # Check if this is a newer OpenAI model
        if "gpt-5" in settings.llm_model.lower() or "gpt-4o" in settings.llm_model.lower():
            llm_params["max_completion_tokens"] = 1000
        else:
            llm_params["max_tokens"] = 1000
        
        # Call LLM
        response = await litellm.acompletion(**llm_params)
        
        duration_ms = (time.time() - start_time) * 1000
        tokens_used = response.usage.total_tokens if response.usage else 0
        
        assistant_message = response.choices[0].message
        tool_calls = []
        
        if assistant_message.tool_calls:
            for tc in assistant_message.tool_calls:
                tool_calls.append({
                    "name": tc.function.name,
                    "arguments": tc.function.arguments
                })
        
        return PromptTestResponse(
            success=True,
            response=assistant_message.content or "",
            tool_calls=tool_calls,
            tokens_used=tokens_used,
            duration_ms=duration_ms
        )
    
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"Prompt test failed: {e}")
        return PromptTestResponse(
            success=False,
            error=str(e),
            duration_ms=duration_ms
        )


# ============== Comparison ==============

@router.post("/compare", response_model=PromptCompareResponse)
async def compare_versions(
    compare_data: PromptCompareRequest,
    request: Request,
    current_user: Optional[UserResponse] = Depends(get_current_user)
):
    """Compare two versions of a prompt template."""
    collection = get_prompts_collection(request)
    
    doc = await collection.find_one({"_id": ObjectId(compare_data.template_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Template not found")
    
    versions = doc.get("versions", [])
    version_a_data = None
    version_b_data = None
    
    for v in versions:
        if v.get("version") == compare_data.version_a:
            version_a_data = v
        if v.get("version") == compare_data.version_b:
            version_b_data = v
    
    if not version_a_data:
        raise HTTPException(status_code=404, detail=f"Version {compare_data.version_a} not found")
    if not version_b_data:
        raise HTTPException(status_code=404, detail=f"Version {compare_data.version_b} not found")
    
    # Generate diff
    prompt_a = version_a_data.get("system_prompt", "")
    prompt_b = version_b_data.get("system_prompt", "")
    
    diff_lines = list(difflib.unified_diff(
        prompt_a.splitlines(keepends=True),
        prompt_b.splitlines(keepends=True),
        fromfile=f"v{compare_data.version_a}",
        tofile=f"v{compare_data.version_b}",
        lineterm=""
    ))
    prompt_diff = "".join(diff_lines)
    
    # Compare tools
    tools_a = {t.get("name") for t in version_a_data.get("tools", [])}
    tools_b = {t.get("name") for t in version_b_data.get("tools", [])}
    
    tools_added = list(tools_b - tools_a)
    tools_removed = list(tools_a - tools_b)
    
    # Check for modified tools (same name, different content)
    tools_modified = []
    common_tools = tools_a & tools_b
    for tool_name in common_tools:
        tool_a = next((t for t in version_a_data.get("tools", []) if t.get("name") == tool_name), None)
        tool_b = next((t for t in version_b_data.get("tools", []) if t.get("name") == tool_name), None)
        if tool_a != tool_b:
            tools_modified.append(tool_name)
    
    return PromptCompareResponse(
        version_a=PromptVersion(**version_a_data),
        version_b=PromptVersion(**version_b_data),
        prompt_diff=prompt_diff,
        tools_added=tools_added,
        tools_removed=tools_removed,
        tools_modified=tools_modified
    )


# ============== Default Template Initialization ==============

# ============== Agent Prompt Definitions ==============

# These are the default prompts for the federated agent system
DEFAULT_AGENT_PROMPTS = {
    "agent_analyze": {
        "name": "Agent Analyze",
        "description": "Orchestrator prompt for deep intent analysis and search strategy",
        "system_prompt": """You are a search strategist analyzing a user's request with deep understanding to determine the optimal search approach.

**User Message:**
{user_message}

**Conversation History:**
{conversation_history}

**Available Data Sources:**
- **profile**: Shared company/organization documents (policies, handbooks, internal docs)
- **cloud**: Cloud storage documents (Google Drive, Dropbox, WebDAV files)
- **personal**: User's private data (emails, personal documents, private files)
- **web**: Internet search via Brave Search + browse specific URLs

**CRITICAL ANALYSIS TASKS:**

1. **Intent Classification** - What type of question is this?
   - FACTUAL: Looking for specific facts ("Who is X?", "What is the address?")
   - EXPLORATORY: Looking for understanding ("How does X work?", "Explain Y")
   - COMPARATIVE: Looking for differences ("Compare A and B")
   - PROCEDURAL: Looking for steps ("How to do X?")
   - AGGREGATE: Looking for summaries ("Summarize all X")

2. **Entity Extraction** - Extract ALL named entities:
   - People: names, roles, titles, relationships
   - Organizations: companies, teams, departments
   - Documents: file names, document types
   - Dates: specific dates, time periods, deadlines
   - Technical terms: jargon, product names, acronyms

3. **Search Query Generation** - Create optimized search queries:
   - Use extracted entities as exact match terms (quotes)
   - Generate synonym variations for key concepts
   - Create both broad and narrow query variants

**Respond with JSON:**
{{
    "intent_summary": "Clear statement of what user wants to know/achieve",
    "query_type": "FACTUAL|EXPLORATORY|COMPARATIVE|PROCEDURAL|AGGREGATE",
    "named_entities": {{
        "people": ["person1", "person2"],
        "organizations": ["org1", "org2"],
        "documents": ["doc type or name"],
        "dates": ["date or period"],
        "technical_terms": ["term1", "term2"]
    }},
    "search_queries": {{
        "primary": "Main search query with key entities",
        "alternatives": ["synonym variation 1", "broader query", "narrower query"]
    }},
    "sources_needed": ["profile", "cloud", "personal", "web"],
    "source_priority": "Which source is most likely to have the answer",
    "must_find": ["Critical piece of information required"],
    "nice_to_have": ["Additional context that would help"],
    "complexity": 1-5,
    "requires_multi_hop": true/false,
    "reasoning": "Your strategic analysis of how to best answer this"
}}

**Source Selection Guidelines:**
- Identity/personal questions ("who am I", "my role", "my emails"): personal first
- Company/organization questions ("our policy", "team handbook"): profile first
- Specific file lookups ("the budget spreadsheet"): cloud first
- Current events, external entities, general knowledge: web search
- Complex questions: use multiple sources in priority order

**Query Optimization Tips:**
- Put names and specific terms in quotes for exact match
- Include role/title when searching for people
- Use document type keywords (policy, handbook, report, email)
- For dates, include both numeric and written formats"""
    },
    "agent_plan": {
        "name": "Agent Plan",
        "description": "Orchestrator prompt for creating optimized execution plans with targeted tasks",
        "system_prompt": """You are creating an execution plan based on the analysis to find the best answer.

**Analysis Results:**
{analysis}

**Available Sources:**
{available_sources}

**PLANNING STRATEGY:**

1. **Query-Type Specific Approach:**
   - FACTUAL: Use exact entity matches, 2-3 targeted searches
   - EXPLORATORY: Broader searches, multiple perspectives
   - COMPARATIVE: Parallel searches for each item being compared
   - PROCEDURAL: Search for guides, documentation, how-to content
   - AGGREGATE: Multiple searches to gather comprehensive data

2. **Task Types Available:**
   - search_profile: Company docs (policies, handbooks, internal)
   - search_cloud: Cloud storage (Drive, Dropbox files)
   - search_personal: Private data (emails, personal docs)
   - search_all: All accessible sources simultaneously
   - web_search: Internet search for external info
   - browse_web: Fetch specific URL content

3. **Query Optimization Rules:**
   - Use quotes around names and specific terms: "John Smith"
   - Include entity types: "John Smith accountant"
   - Create variations: one exact, one broader, one with synonyms
   - For multi-hop questions, plan dependent searches

**Respond with JSON:**
{{
    "intent_summary": "What the user wants",
    "reasoning": "Why this plan will find the answer",
    "strategy": "parallel" | "sequential" | "iterative",
    "tasks": [
        {{
            "id": "task_1",
            "type": "search_profile|search_cloud|search_personal|search_all|web_search|browse_web",
            "query": "optimized search query with entities",
            "sources": ["specific_source_id"],
            "priority": 1,
            "depends_on": [],
            "max_results": 10,
            "context_hint": "What specifically to look for in results"
        }},
        {{
            "id": "task_2",
            "type": "...",
            "query": "alternative/broader query",
            "priority": 2,
            "depends_on": [],
            "max_results": 5,
            "context_hint": "..."
        }}
    ],
    "success_criteria": "What specific information would make this answer complete",
    "max_iterations": 2
}}

**Planning Best Practices:**
- Create 2-4 focused tasks, not one broad task
- Priority 1 tasks run first; if they succeed, lower priority may be skipped
- Use extracted entities from analysis in queries
- search_all is good for unknown document locations
- web_search first to find URLs, then browse_web for content
- For "who" questions about internal people, search personal/profile first
- For external entities (companies, public figures), use web search
- Set realistic max_iterations (1-2 for simple, 3 for complex)"""
    },
    "agent_evaluate": {
        "name": "Agent Evaluate",
        "description": "Orchestrator prompt for evaluating search results and deciding next steps",
        "system_prompt": """You are evaluating search results to decide if we have enough information to answer the user's question.

**Original Intent:**
{intent}

**Success Criteria:**
{success_criteria}

**Results from Workers:**
{results_summary}

**EVALUATION DECISION FRAMEWORK:**

1. **"sufficient" (confidence 0.8-1.0)**: Use when:
   - The main question can be answered with found information
   - Key entities/facts mentioned in intent are found
   - Sources are credible and relevant
   - Even if not perfect, we have enough to provide value

2. **"need_refinement" (confidence 0.4-0.7)**: Use when:
   - Partial information found but key gaps exist
   - Found related documents but not the specific answer
   - Query terms might need adjustment (synonyms, different phrasing)
   - Only use if refinement is likely to help (not already tried)

3. **"need_expansion" (confidence 0.3-0.6)**: Use when:
   - Few or no results from primary sources
   - Should try different source types (e.g., web if internal failed)
   - Need broader search scope

4. **"cannot_answer" (confidence 0.0-0.3)**: Use when:
   - Multiple searches returned nothing relevant
   - Topic is clearly outside available data
   - Already tried refinements without success

**CRITICAL: Bias toward "sufficient"**
- If you found 3+ relevant documents, prefer "sufficient"
- Don't chase perfection - good answers are better than endless searching
- Users prefer faster responses with good info over slow responses with perfect info

**Respond with JSON:**
{{
    "phase": "initial" | "refinement" | "final",
    "findings_summary": "What key information was found",
    "answer_possible": true/false,
    "gaps_identified": ["specific missing info if any"],
    "decision": "sufficient" | "need_refinement" | "need_expansion" | "cannot_answer",
    "follow_up_tasks": [
        // ONLY if decision is need_refinement or need_expansion
        // Max 2 follow-up tasks
        {{
            "id": "followup_1",
            "type": "search type",
            "query": "refined query - different from original",
            "priority": 1,
            "max_results": 5,
            "context_hint": "specific thing to look for"
        }}
    ],
    "reasoning": "Why this decision - be specific about what was found/missing",
    "confidence": 0.0-1.0
}}

**Remember:** 
- Empty results after 2 iterations = stop searching
- High-quality results (score > 0.7) = sufficient
- Don't create follow-up tasks similar to already-executed tasks"""
    },
    "agent_synthesize": {
        "name": "Agent Synthesize",
        "description": "Orchestrator prompt for generating high-quality answers with proper citations",
        "system_prompt": """You are synthesizing a comprehensive, well-cited answer from search results.

**User's Question:**
{user_message}

**All Retrieved Information:**
{all_results}

**SYNTHESIS FRAMEWORK:**

1. **Answer Structure** (Follow this order):
   - **Direct Answer**: Start with a clear, direct response to the question (1-2 sentences)
   - **Supporting Details**: Evidence and context from sources
   - **Additional Context**: Related information that adds value
   - **Source Summary**: Brief note on which sources were used

2. **Citation Rules** (MANDATORY):
   - Every factual claim MUST have a citation
   - Citation format: [Source: "Document Title"]
   - For web sources: [Source: URL]
   - Place citation immediately after the claim, not at the end
   - If sources conflict, note the discrepancy: "Document A states X, while Document B indicates Y"

3. **Query-Type Response Guidelines:**

   **For FACTUAL questions** (Who, What, When, Where):
   - Lead with the specific answer
   - Provide the source immediately
   - Add relevant context
   Example: "The project manager is John Smith [Source: "Team Directory"]. He joined the company in 2022..."

   **For EXPLORATORY questions** (How, Why, Explain):
   - Structure with clear sections/headers if complex
   - Build explanation logically
   - Connect concepts with transitions
   - Include examples from documents

   **For COMPARATIVE questions**:
   - Use parallel structure or comparison format
   - Address each item being compared
   - Highlight key similarities and differences
   - Cite sources for each comparison point

   **For PROCEDURAL questions** (How to):
   - Number the steps clearly
   - Include prerequisites and requirements
   - Note any warnings or important considerations
   - Reference detailed documentation

   **For AGGREGATE questions** (Summarize, List all):
   - Organize by category or theme
   - Include counts where relevant
   - Ensure comprehensive coverage
   - Note if information might be incomplete

4. **Quality Checks:**
   - Does the answer directly address the question?
   - Is every claim supported by a citation?
   - Is the response complete or are there acknowledged gaps?
   - Is the language clear and professional?

**If no relevant information was found:**
Say so clearly: "I searched [sources searched] but did not find information about [specific topic]. You may want to [suggest alternative approaches]."

**Generate your response now, following the framework above:**"""
    },
    "agent_fast_response": {
        "name": "Agent Fast Response",
        "description": "Worker prompt for generating quick, well-cited responses from search results",
        "system_prompt": """Based on the following information, answer the user's question directly.

**User Question:**
{user_message}

**Available Information:**
{context}

**Response Requirements:**
1. **Start with the answer** - Don't begin with "Based on..." or "According to..."
2. **Cite every fact** - Format: [Source: "Document Title"]
3. **Be concise but complete** - Include key details, skip fluff
4. **Acknowledge gaps** - If information is incomplete, say what's missing

**Example Good Response:**
"John Smith is the project manager for the Alpha initiative [Source: "Team Directory"]. He reports to Sarah Jones and has been in this role since January 2024 [Source: "Org Chart Q1"]."

**Example Bad Response:**
"Based on the search results, I found that the project manager appears to be John Smith according to some documents."

**Generate your response now:**"""
    },
    "worker_summarize": {
        "name": "Worker Summarize",
        "description": "Worker prompt for summarizing search results",
        "system_prompt": """Summarize the following search results in relation to: {context_hint}

{content}

Provide a concise summary of the key findings."""
    },
    "worker_refine_query": {
        "name": "Worker Refine Query",
        "description": "Worker prompt for refining search queries based on prior results",
        "system_prompt": """The original query was: {query}

Previous search results:
{results_summary}

Based on these results, suggest a refined search query that might find better results.
Just respond with the refined query, nothing else."""
    }
}


async def initialize_default_templates(db, force_update: bool = False):
    """Initialize default prompt templates if none exist.
    
    Args:
        db: Database instance
        force_update: If True, update existing prompts with new defaults
    """
    collection = db.db.prompt_templates
    
    # Check for chat template
    chat_count = await collection.count_documents({"category": "chat"})
    
    if chat_count == 0:
        # Import the current system prompt and tools from chat.py
        from backend.routers.chat import TOOLS_SCHEMA
        
        default_system_prompt = """You are a helpful AI assistant with access to powerful search and browsing tools. You MUST use these tools to answer questions - NEVER respond without using tools first.

## Available Tools:

### 1. search_knowledge_base
Search internal documents using hybrid search (vector + text). The knowledge base contains:
- **Profile Documents**: Shared company/organization documents
- **Cloud Storage**: Documents from Google Drive, Dropbox, WebDAV, etc.
- **Personal Data**: User's private emails, documents, and files

Usage:
- **ALWAYS call this multiple times** with different queries (at least 2-3 searches per question)
- Start with broad context queries, then get specific
- If a search returns no results, TRY DIFFERENT TERMS - don't give up!
- When user says "my company" - search for company info, organization, business, etc.

### 2. browse_web  
Fetch and read content from a specific web URL.
- Use when you have a specific URL to visit
- Can extract text, markdown, or links from pages
- Good for reading company websites, documentation, articles

### 3. web_search
Search the internet using Brave Search.
- Use to find URLs when you don't have a specific address
- Returns titles, snippets, and URLs from search results
- Good for finding current information, company registries, external data

## CRITICAL RULES:

### Rule 1: ALWAYS search before responding
**WRONG**: Explaining what the user should do or look for
**RIGHT**: Actually calling the tools and finding the information

### Rule 2: When user references "my company" or "our organization"
You MUST search the knowledge base first:
1. search_knowledge_base("company name organization")
2. search_knowledge_base("business overview about us")
3. Then search for the specific topic they asked about

### Rule 3: Don't give up after one search
- If search returns empty, try 2-3 MORE searches with different terms
- Try synonyms: "accounting" → "finance", "invoices", "bookkeeping"
- Try broader terms: "vendor" → "supplier", "partner", "company"
- Try different source types: internal docs vs cloud storage vs web

### Rule 4: Combine tools effectively
- Use search_knowledge_base for internal data
- Use web_search to find relevant URLs
- Use browse_web to fetch content from those URLs
- Chain tools together for comprehensive answers

### Rule 5: Cite your sources
- Always mention where information came from
- Include document titles for internal sources
- Include URLs for web sources

## Example - User asks: "find the owner of our accounting firm"

1. search_knowledge_base("company organization name") - find company name
2. search_knowledge_base("accounting finance vendor") - find accounting docs
3. web_search("[accounting company name] owner CEO") - search web
4. browse_web("[company website]/about") - check their website
5. Synthesize findings with citations

Remember: You have access to the user's company documents AND the internet. Use both! Search multiple times with different queries!"""

        # Convert TOOLS_SCHEMA to ToolSchema format
        tools = [openai_format_to_tool_schema(t) for t in TOOLS_SCHEMA]
        
        initial_version = {
            "version": 1,
            "system_prompt": default_system_prompt,
            "tools": [t.model_dump() for t in tools],
            "created_at": datetime.now(),
            "created_by": "system",
            "notes": "Default system prompt with RAG tools",
            "is_active": True
        }
        
        default_template = {
            "name": "Default Chat Agent",
            "description": "Main system prompt for the RAG chat agent with search and browsing tools",
            "category": "chat",
            "versions": [initial_version],
            "active_version": 1,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "created_by": "system"
        }
        
        await collection.insert_one(default_template)
        logger.info("Initialized default chat prompt template")
    
    # Initialize agent prompts
    for prompt_key, prompt_data in DEFAULT_AGENT_PROMPTS.items():
        existing = await collection.find_one({"category": prompt_key})
        if existing and not force_update:
            continue
        
        if existing and force_update:
            # Update existing prompt with new default as a new version
            versions = existing.get("versions", [])
            next_version = max([v.get("version", 0) for v in versions], default=0) + 1
            
            new_version = {
                "version": next_version,
                "system_prompt": prompt_data["system_prompt"],
                "tools": [],
                "created_at": datetime.now(),
                "created_by": "system",
                "notes": "Updated from defaults",
                "is_active": True
            }
            
            # Mark all old versions as inactive
            for v in versions:
                v["is_active"] = False
            versions.append(new_version)
            
            await collection.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "versions": versions,
                        "active_version": next_version,
                        "updated_at": datetime.now()
                    }
                }
            )
            logger.info(f"Updated agent prompt template: {prompt_key} to v{next_version}")
            continue
        
        initial_version = {
            "version": 1,
            "system_prompt": prompt_data["system_prompt"],
            "tools": [],  # Agent prompts don't have tools
            "created_at": datetime.now(),
            "created_by": "system",
            "notes": "Default agent prompt",
            "is_active": True
        }
        
        template = {
            "name": prompt_data["name"],
            "description": prompt_data["description"],
            "category": prompt_key,
            "versions": [initial_version],
            "active_version": 1,
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "created_by": "system"
        }
        
        await collection.insert_one(template)
        logger.info(f"Initialized agent prompt template: {prompt_key}")


# ============== Get Active Prompt ==============

async def get_active_prompt(db, category: str = "chat") -> tuple:
    """Get the active system prompt and tools for a category.
    
    Returns:
        tuple: (system_prompt, tools_schema_list)
    """
    collection = db.db.prompt_templates
    
    doc = await collection.find_one({"category": category})
    if not doc:
        return None, None
    
    active_version = doc.get("active_version", 1)
    versions = doc.get("versions", [])
    
    for v in versions:
        if v.get("version") == active_version:
            system_prompt = v.get("system_prompt", "")
            tools = []
            for tool_data in v.get("tools", []):
                tool = ToolSchema(**tool_data) if isinstance(tool_data, dict) else tool_data
                if tool.enabled if hasattr(tool, 'enabled') else tool_data.get('enabled', True):
                    tools.append(tool_schema_to_openai_format(
                        tool if isinstance(tool, ToolSchema) else ToolSchema(**tool_data)
                    ))
            return system_prompt, tools
    
    return None, None


async def get_agent_prompt(db, prompt_key: str) -> str:
    """Get an agent prompt from the database.
    
    Args:
        db: Database instance
        prompt_key: The prompt category key (e.g., 'agent_analyze', 'agent_plan')
    
    Returns:
        The system prompt string, or the default if not found
    """
    collection = db.db.prompt_templates
    
    doc = await collection.find_one({"category": prompt_key})
    if doc:
        active_version = doc.get("active_version", 1)
        for v in doc.get("versions", []):
            if v.get("version") == active_version:
                return v.get("system_prompt", "")
    
    # Fall back to default prompts
    if prompt_key in DEFAULT_AGENT_PROMPTS:
        return DEFAULT_AGENT_PROMPTS[prompt_key]["system_prompt"]
    
    return ""


def get_agent_prompt_sync(prompt_key: str) -> str:
    """Get an agent prompt synchronously (returns default).
    
    This is used when database is not available or for initialization.
    
    Args:
        prompt_key: The prompt category key
    
    Returns:
        The default system prompt string
    """
    if prompt_key in DEFAULT_AGENT_PROMPTS:
        return DEFAULT_AGENT_PROMPTS[prompt_key]["system_prompt"]
    return ""
