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
        # Call LLM
        response = await litellm.acompletion(
            model=settings.llm_model,
            messages=messages,
            tools=tools if tools else None,
            tool_choice="auto" if tools else None,
            temperature=0.7,
            max_tokens=1000,
            api_key=settings.llm_api_key,
            api_base=settings.llm_base_url if settings.llm_base_url else None,
        )
        
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

async def initialize_default_templates(db):
    """Initialize default prompt templates if none exist."""
    collection = db.db.prompt_templates
    
    count = await collection.count_documents({})
    if count > 0:
        return
    
    # Import the current system prompt and tools from chat.py
    from backend.routers.chat import TOOLS_SCHEMA
    
    default_system_prompt = """You are a helpful AI assistant with access to tools. You MUST use these tools to answer questions - NEVER respond without using tools first.

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
    logger.info("Initialized default prompt template")


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
