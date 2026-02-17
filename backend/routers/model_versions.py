"""
Model Version Management API Router

Provides endpoints for managing and switching between different model versions,
including discovery, compatibility checking, and configuration management.
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel

from backend.core.model_versions import (
    ModelVersion, ModelCapability, ModelType,
    get_model_by_id, get_models_by_provider, get_models_by_capability,
    get_compatible_models, get_latest_models, get_cost_effective_models,
    get_model_parameter_mapping, is_model_compatible_with_parameter,
    ALL_MODELS
)
from backend.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Model Versions"])

# ==================== Pydantic Models ====================

class ModelVersionResponse(BaseModel):
    """Response model for a single model version."""
    id: str
    name: str
    provider: str
    type: str
    version: str
    release_date: Optional[str]
    context_window: int
    max_output_tokens: int
    capabilities: List[str]
    pricing_input: Optional[float]
    pricing_output: Optional[float]
    is_deprecated: bool
    is_experimental: bool
    parameter_mapping: Dict[str, str]
    default_parameters: Dict[str, Any]


class ModelListResponse(BaseModel):
    """Response model for list of models."""
    models: List[ModelVersionResponse]
    total: int
    provider_filter: Optional[str] = None
    capability_filter: Optional[str] = None
    type_filter: Optional[str] = None


class ModelSwitchRequest(BaseModel):
    """Request to switch model versions."""
    orchestrator_model: Optional[str] = None
    orchestrator_provider: Optional[str] = None
    worker_model: Optional[str] = None
    worker_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_provider: Optional[str] = None


class ModelCompatibilityCheck(BaseModel):
    """Model compatibility check request."""
    model_id: str
    parameters: Dict[str, Any]


class CompatibilityResult(BaseModel):
    """Model compatibility check result."""
    model_id: str
    is_compatible: bool
    incompatible_parameters: List[str]
    suggested_mappings: Dict[str, str]
    warnings: List[str]


class ModelRecommendation(BaseModel):
    """Model recommendation based on criteria."""
    model: ModelVersionResponse
    score: float
    reasons: List[str]
    estimated_cost_per_1k_tokens: Optional[float]


# ==================== Helper Functions ====================

def model_to_response(model: ModelVersion) -> ModelVersionResponse:
    """Convert ModelVersion to response model."""
    return ModelVersionResponse(
        id=model.id,
        name=model.name,
        provider=model.provider,
        type=model.type.value,
        version=model.version,
        release_date=model.release_date.isoformat() if model.release_date else None,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        capabilities=[cap.value for cap in model.capabilities],
        pricing_input=model.pricing_input,
        pricing_output=model.pricing_output,
        is_deprecated=model.is_deprecated,
        is_experimental=model.is_experimental,
        parameter_mapping=model.parameter_mapping,
        default_parameters=model.default_parameters
    )


# ==================== API Endpoints ====================

@router.get("/", response_model=ModelListResponse)
async def list_models(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    capability: Optional[str] = Query(None, description="Filter by capability"),
    model_type: Optional[str] = Query(None, description="Filter by model type"),
    show_deprecated: bool = Query(False, description="Include deprecated models"),
    show_experimental: bool = Query(True, description="Include experimental models"),
    sort_by: str = Query("release_date", description="Sort by: release_date, cost, name"),
    limit: int = Query(50, description="Maximum number of models to return")
):
    """
    List all available model versions with filtering and sorting options.
    """
    # Start with all models
    models = list(ALL_MODELS.values())
    
    # Apply filters
    if provider:
        models = [m for m in models if m.provider == provider]
    
    if capability:
        try:
            cap_enum = ModelCapability(capability)
            models = [m for m in models if cap_enum in m.capabilities]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid capability: {capability}")
    
    if model_type:
        try:
            type_enum = ModelType(model_type)
            models = [m for m in models if m.type == type_enum]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid model type: {model_type}")
    
    if not show_deprecated:
        models = [m for m in models if not m.is_deprecated]
    
    if not show_experimental:
        models = [m for m in models if not m.is_experimental]
    
    # Apply sorting
    if sort_by == "release_date":
        models.sort(key=lambda m: m.release_date or datetime.min, reverse=True)
    elif sort_by == "cost":
        models.sort(key=lambda m: (m.pricing_input or float('inf')) + (m.pricing_output or float('inf')))
    elif sort_by == "name":
        models.sort(key=lambda m: m.name.lower())
    
    # Apply limit
    models = models[:limit]
    
    return ModelListResponse(
        models=[model_to_response(m) for m in models],
        total=len(models),
        provider_filter=provider,
        capability_filter=capability,
        type_filter=model_type
    )


@router.get("/latest", response_model=ModelListResponse)
async def get_latest_models_endpoint(
    limit: int = Query(10, description="Number of latest models to return")
):
    """
    Get the latest released models across all providers.
    """
    models = get_latest_models(limit)
    return ModelListResponse(
        models=[model_to_response(m) for m in models],
        total=len(models)
    )


@router.get("/cost-effective", response_model=ModelListResponse)
async def get_cost_effective_models_endpoint(
    limit: int = Query(10, description="Number of cost-effective models to return")
):
    """
    Get the most cost-effective models based on pricing.
    """
    models = get_cost_effective_models(limit)
    return ModelListResponse(
        models=[model_to_response(m) for m in models],
        total=len(models)
    )


@router.get("/{model_id}", response_model=ModelVersionResponse)
async def get_model_details(model_id: str):
    """
    Get detailed information about a specific model version.
    """
    model = get_model_by_id(model_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    
    return model_to_response(model)


@router.post("/switch", response_model=dict)
async def switch_model_versions(
    request: Request,
    switch_request: ModelSwitchRequest
):
    """
    Switch to different model versions for orchestrator, worker, and embedding.
    
    Updates both runtime configuration and persists to database.
    """
    db = request.app.state.db
    updates = {}
    warnings = []
    
    # Update orchestrator
    if switch_request.orchestrator_model:
        model = get_model_by_id(switch_request.orchestrator_model)
        if not model:
            raise HTTPException(
                status_code=400, 
                detail=f"Orchestrator model not found: {switch_request.orchestrator_model}"
            )
        
        if model.is_deprecated:
            warnings.append(f"Warning: {model.name} is deprecated")
        
        settings.orchestrator_model = switch_request.orchestrator_model
        updates["orchestrator_model"] = switch_request.orchestrator_model
        
        if switch_request.orchestrator_provider:
            settings.orchestrator_provider = switch_request.orchestrator_provider
            updates["orchestrator_provider"] = switch_request.orchestrator_provider
    
    # Update worker
    if switch_request.worker_model:
        model = get_model_by_id(switch_request.worker_model)
        if not model:
            raise HTTPException(
                status_code=400, 
                detail=f"Worker model not found: {switch_request.worker_model}"
            )
        
        if model.is_deprecated:
            warnings.append(f"Warning: {model.name} is deprecated")
        
        settings.worker_model = switch_request.worker_model
        updates["worker_model"] = switch_request.worker_model
        
        if switch_request.worker_provider:
            settings.worker_provider = switch_request.worker_provider
            updates["worker_provider"] = switch_request.worker_provider
    
    # Update embedding
    if switch_request.embedding_model:
        model = get_model_by_id(switch_request.embedding_model)
        if not model:
            raise HTTPException(
                status_code=400, 
                detail=f"Embedding model not found: {switch_request.embedding_model}"
            )
        
        settings.embedding_model = switch_request.embedding_model
        updates["embedding_model"] = switch_request.embedding_model
        
        if switch_request.embedding_provider:
            settings.embedding_provider = switch_request.embedding_provider
            updates["embedding_provider"] = switch_request.embedding_provider
    
    # Persist to database
    try:
        collection = db.db["llm_config"]
        await collection.update_one(
            {"_id": "active_config"},
            {
                "$set": {
                    **updates,
                    "updated_at": datetime.now().isoformat()
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"Failed to persist model configuration: {e}")
        warnings.append("Configuration saved to runtime but failed to persist to database")
    
    logger.info(f"Model versions switched: {updates}")
    
    return {
        "success": True,
        "message": "Model versions updated successfully",
        "updates": updates,
        "warnings": warnings
    }


@router.post("/check-compatibility", response_model=CompatibilityResult)
async def check_model_compatibility(check: ModelCompatibilityCheck):
    """
    Check if a model is compatible with specific parameters.
    """
    model = get_model_by_id(check.model_id)
    if not model:
        raise HTTPException(status_code=404, detail=f"Model not found: {check.model_id}")
    
    incompatible_params = []
    suggested_mappings = {}
    warnings = []
    
    for param_name, param_value in check.parameters.items():
        if not is_model_compatible_with_parameter(check.model_id, param_name):
            incompatible_params.append(param_name)
            
            # Suggest mapping if available
            mapping = get_model_parameter_mapping(check.model_id)
            if param_name in mapping:
                suggested_mappings[param_name] = mapping[param_name]
    
    # Add warnings for deprecated/experimental models
    if model.is_deprecated:
        warnings.append("Model is deprecated and may be removed in the future")
    if model.is_experimental:
        warnings.append("Model is experimental and behavior may change")
    
    return CompatibilityResult(
        model_id=check.model_id,
        is_compatible=len(incompatible_params) == 0,
        incompatible_parameters=incompatible_params,
        suggested_mappings=suggested_mappings,
        warnings=warnings
    )


@router.get("/recommendations", response_model=List[ModelRecommendation])
async def get_model_recommendations(
    task_type: str = Query("general", description="Task type: general, reasoning, coding, multimodal"),
    budget_limit: Optional[float] = Query(None, description="Maximum cost per 1k tokens"),
    context_required: Optional[int] = Query(None, description="Minimum context window required"),
    capabilities_required: Optional[List[str]] = Query(None, description="Required capabilities")
):
    """
    Get model recommendations based on task requirements and constraints.
    """
    recommendations = []
    
    # Convert capabilities to enums
    required_caps = []
    if capabilities_required:
        for cap_str in capabilities_required:
            try:
                required_caps.append(ModelCapability(cap_str))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid capability: {cap_str}")
    
    # Score models based on criteria
    for model in ALL_MODELS.values():
        score = 0.0
        reasons = []
        
        # Skip deprecated models unless specifically requested
        if model.is_deprecated:
            continue
            
        # Task type scoring
        if task_type == "reasoning" and ModelCapability.REASONING in model.capabilities:
            score += 2.0
            reasons.append("Optimized for reasoning tasks")
        elif task_type == "coding" and ModelCapability.CODE_GENERATION in model.capabilities:
            score += 2.0
            reasons.append("Optimized for code generation")
        elif task_type == "multimodal" and ModelCapability.MULTIMODAL in model.capabilities:
            score += 2.0
            reasons.append("Supports multimodal inputs")
        
        # Budget constraint
        if budget_limit and model.pricing_input is not None and model.pricing_output is not None:
            avg_cost = ((model.pricing_input or 0) + (model.pricing_output or 0)) / 2
            if avg_cost <= budget_limit:
                score += 1.0
                reasons.append(f"Within budget (${avg_cost:.3f} per 1k tokens)")
            else:
                continue  # Skip models over budget
        
        # Context window requirement
        if context_required and model.context_window >= context_required:
            score += 1.0
            reasons.append(f"Sufficient context window ({model.context_window} tokens)")
        elif context_required and model.context_window < context_required:
            continue  # Skip models with insufficient context
        
        # Required capabilities
        if required_caps:
            missing_caps = [cap for cap in required_caps if cap not in model.capabilities]
            if missing_caps:
                continue  # Skip models missing required capabilities
            else:
                score += len(required_caps)
                reasons.append(f"Has all required capabilities: {[c.value for c in required_caps]}")
        
        # Base score for availability and recency
        score += 0.5  # Availability bonus
        if model.release_date:
            days_since_release = (datetime.now() - model.release_date).days
            if days_since_release < 30:  # Recent release bonus
                score += 0.5
                reasons.append("Recently released")
        
        if score > 0:
            estimated_cost = None
            if model.pricing_input is not None and model.pricing_output is not None:
                estimated_cost = ((model.pricing_input or 0) + (model.pricing_output or 0)) / 2
            
            recommendations.append(ModelRecommendation(
                model=model_to_response(model),
                score=score,
                reasons=reasons,
                estimated_cost_per_1k_tokens=estimated_cost
            ))
    
    # Sort by score and return top 10
    recommendations.sort(key=lambda r: r.score, reverse=True)
    return recommendations[:10]


@router.get("/current", response_model=dict)
async def get_current_models():
    """
    Get currently configured model versions.
    """
    return {
        "orchestrator": {
            "model": settings.orchestrator_model,
            "provider": settings.orchestrator_provider,
            "details": model_to_response(get_model_by_id(settings.orchestrator_model)) if get_model_by_id(settings.orchestrator_model) else None
        },
        "worker": {
            "model": settings.worker_model,
            "provider": settings.worker_provider,
            "details": model_to_response(get_model_by_id(settings.worker_model)) if get_model_by_id(settings.worker_model) else None
        },
        "embedding": {
            "model": settings.embedding_model,
            "provider": settings.embedding_provider,
            "details": model_to_response(get_model_by_id(settings.embedding_model)) if get_model_by_id(settings.embedding_model) else None
        }
    }