"""
FastAPI Backend for RecallHub.

Production-ready API server with endpoints for:
- Chat/Query with RAG agent
- Search (semantic, text, hybrid)
- Profile management
- Document ingestion
- System health and stats
"""

import logging
import traceback
import uuid
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.exceptions import RequestValidationError

from backend.routers import chat, search, profiles, ingestion, system, sessions, auth
from backend.routers import status, indexes, ingestion_queue, local_llm, prompts, model_versions
from backend.routers import strategies, backup, embedding_benchmark, file_registry, tenant, support, debug
from backend.routers import strategy_specs as strategy_specs_router_module
from backend.routers import evaluation as evaluation_router_module
from backend.routers import scheduler as scheduler_router_module
from backend.routers import telemetry as telemetry_router_module
from backend.routers.cloud_sources import (
    connections_router as cloud_connections,
    oauth_router as cloud_oauth,
    sync_router as cloud_sync,
    providers_router as cloud_providers,
    cache_router as cloud_cache,
)
from backend.routers.system import load_config_from_db, load_llm_config_from_db
from backend.routers.ingestion import check_and_resume_interrupted_jobs, graceful_shutdown_handler
from backend.routers.prompts import initialize_default_templates
from backend.services.backup_service import BackupService
from backend.services.telemetry_service import TelemetryService
from backend.core.config import settings
from backend.core.database import DatabaseManager
from backend.core.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    validate_jwt_secret,
    get_docs_urls,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Increase default thread pool size for async operations
# This prevents blocking when ingestion uses many threads
def configure_thread_pool():
    """Configure a larger default thread pool for asyncio."""
    loop = asyncio.get_event_loop()
    # Use 32 workers instead of default min(32, cpu_count + 4)
    executor = ThreadPoolExecutor(max_workers=32, thread_name_prefix="asyncio_")
    loop.set_default_executor(executor)
    logger.info("Configured asyncio thread pool with 32 workers")

# Configure at module load
try:
    configure_thread_pool()
except RuntimeError:
    # Event loop not running yet, will be configured later
    pass

# Validate JWT secret on startup
try:
    _jwt_secret = validate_jwt_secret()
    logger.info("JWT secret key validated")
except ValueError as e:
    logger.critical(f"Security configuration error: {e}")
    # Don't raise here - let the app start but log the critical issue
    # In production, this will be caught by health checks


# Request timeout middleware to prevent blocking requests
REQUEST_TIMEOUT_SECONDS = 30  # Default timeout for API requests
CHAT_TIMEOUT_SECONDS = 300  # Extended timeout for chat/agent endpoints (5 minutes)
MODEL_TEST_TIMEOUT_SECONDS = 180  # Extended timeout for model testing (3 minutes, Ollama cold-start)
HEALTH_CHECK_PATHS = {"/health", "/api/v1/system/health", "/"}

# Paths that need extended timeout (agent operations can take a while)
EXTENDED_TIMEOUT_PATHS = {
    "/api/v1/sessions/",  # Chat messages
    "/api/v1/chat/",      # Chat completions
}

# Paths that need model-test timeout (Ollama models can be slow on first load)
MODEL_TEST_TIMEOUT_PATHS = {
    "/api/v1/system/models/",      # Model capability testing
    "/api/v1/local-llm/test-model",  # LLM connection testing
    "/api/v1/local-llm/custom-endpoints/",  # Custom endpoint testing
}


class RequestTimeoutMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce request timeouts and prevent blocking.
    
    - Health check endpoints get a short timeout (5s)
    - Chat/agent endpoints get extended timeout (300s)
    - Regular endpoints get a standard timeout (30s)
    - Streaming endpoints are excluded from timeout
    """
    
    async def dispatch(self, request: Request, call_next):
        # Skip timeout for SSE/streaming endpoints
        if request.url.path.endswith("/stream") or "logs/stream" in request.url.path:
            return await call_next(request)
        
        # Short timeout for health checks
        if request.url.path in HEALTH_CHECK_PATHS:
            timeout = 5
        # Extended timeout for chat/agent endpoints
        elif any(request.url.path.startswith(p) for p in EXTENDED_TIMEOUT_PATHS):
            timeout = CHAT_TIMEOUT_SECONDS
        # Model testing timeout (Ollama cold-start can take 30-120s)
        elif any(request.url.path.startswith(p) for p in MODEL_TEST_TIMEOUT_PATHS):
            timeout = MODEL_TEST_TIMEOUT_SECONDS
        else:
            timeout = REQUEST_TIMEOUT_SECONDS
        
        start_time = time.time()
        
        try:
            # Wrap the request in a timeout
            response = await asyncio.wait_for(
                call_next(request),
                timeout=timeout
            )
            
            # Log slow requests (>5s)
            elapsed = time.time() - start_time
            if elapsed > 5 and request.url.path not in HEALTH_CHECK_PATHS:
                logger.warning(
                    f"Slow request: {request.method} {request.url.path} "
                    f"took {elapsed:.2f}s"
                )
            
            return response
            
        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            logger.error(
                f"Request timeout after {elapsed:.2f}s: "
                f"{request.method} {request.url.path}"
            )
            return JSONResponse(
                status_code=504,
                content={
                    "error": "gateway_timeout",
                    "message": f"Request timed out after {timeout}s. "
                               "The server may be busy processing other requests.",
                    "path": request.url.path
                }
            )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan manager - startup and shutdown."""
    # Startup
    logger.info("Starting RecallHub API...")
    
    # Initialize database connection
    db_manager = DatabaseManager()
    await db_manager.connect()
    app.state.db = db_manager
    
    logger.info(f"Connected to database: {settings.mongodb_database}")
    
    # Load persisted configuration from database
    try:
        config_loaded = await load_config_from_db(db_manager)
        if config_loaded:
            logger.info("Loaded saved configuration from database")
        else:
            logger.info("No saved configuration found, using defaults")
    except Exception as e:
        logger.warning(f"Failed to load saved config: {e}")
    
    # Load LLM provider configuration from database
    try:
        llm_config_loaded = await load_llm_config_from_db(db_manager)
        if llm_config_loaded:
            logger.info("Loaded LLM provider configuration from database")
    except Exception as e:
        logger.warning(f"Failed to load LLM provider config: {e}")
    
    # Check for and resume interrupted ingestion jobs
    try:
        resumed_job = await check_and_resume_interrupted_jobs(db_manager)
        if resumed_job:
            logger.info(f"Resumed interrupted ingestion job: {resumed_job}")
    except Exception as e:
        logger.warning(f"Failed to check for interrupted jobs: {e}")
    
    # Initialize default prompt templates
    try:
        await initialize_default_templates(db_manager)
    except Exception as e:
        logger.warning(f"Failed to initialize default prompts: {e}")
    
    # Initialize backup configuration and directories
    try:
        backup_service = BackupService(db_manager)
        await backup_service.initialize_config()
    except Exception as e:
        logger.warning(f"Failed to initialize backup config: {e}")
    
    # Create TTL index for activity logs (auto-expire after 7 days)
    try:
        await db_manager.db["agent_activity_log"].create_index(
            "started_at", expireAfterSeconds=7*24*3600
        )
        logger.info("Ensured TTL index on agent_activity_log")
    except Exception as e:
        logger.warning(f"Failed to create TTL index: {e}")
    
    # Initialize telemetry service
    try:
        telemetry_service = TelemetryService(
            storage_path=settings.telemetry_storage_path,
            enabled=settings.telemetry_enabled,
            mode=settings.telemetry_mode,
            pii_mode=settings.telemetry_pii_mode,
            retention_days=settings.telemetry_retention_days,
            pii_markers=settings.telemetry_pii_markers,
        )
        app.state.telemetry = telemetry_service
        # Set module-level reference for coordinator telemetry emission
        from backend.agent.coordinator import set_telemetry_service
        set_telemetry_service(telemetry_service)
    except Exception as e:
        logger.warning(f"Failed to initialize telemetry service: {e}")
        app.state.telemetry = None
    
    # Chat-activity tracker + resource snapshot collector wiring
    # (Task 86 / F9 — bridges chat endpoints into Jimmy's P2
    # ResourceSnapshotCollector so ``interactive_users_detected``
    # reflects real user-visible chat traffic across all Uvicorn
    # workers via the shared Mongo ``runtime_signals`` collection).
    try:
        from backend.services.activity_tracker import ChatActivityTracker
        from backend.services.resource_snapshot import ResourceSnapshotCollector

        activity_tracker = ChatActivityTracker(
            db=db_manager.db,
            mode="mongo",
        )
        await activity_tracker.ensure_indexes()
        app.state.activity_tracker = activity_tracker

        # Instantiate the strategy resource snapshot collector with the
        # live activity tracker so ``_detect_interactive_users`` flips
        # to True on recent chat traffic. Safe to re-bind here even if
        # other Phase 6 follow-ups also touch app.state — we own this
        # field per the F9 brief.
        resource_snapshot_collector = ResourceSnapshotCollector(
            db=db_manager.db,
            activity_tracker=activity_tracker,
        )
        await resource_snapshot_collector.ensure_indexes()
        app.state.resource_snapshot_collector = resource_snapshot_collector

        logger.info(
            "ChatActivityTracker wired (mode=mongo, host_id=%s) and "
            "ResourceSnapshotCollector activated",
            activity_tracker.host_id,
        )
    except Exception as e:
        logger.warning(
            "ChatActivityTracker / ResourceSnapshotCollector wiring failed "
            "(non-fatal): %s",
            e,
        )
        # Preserve legacy attributes so getattr(..., None) callers and
        # the stub-fallback path in ResourceSnapshotCollector continue
        # to work without surprises.
        if not hasattr(app.state, "activity_tracker"):
            app.state.activity_tracker = None
        if not hasattr(app.state, "resource_snapshot_collector"):
            app.state.resource_snapshot_collector = None

    # Strategy OS initialization (Phase 0)
    if settings.strategy_os_enabled:
        try:
            from backend.core.model_roles import ModelRoleRegistry
            from scripts.migrate_strategy_os_collections import migrate_strategy_os_collections

            # Run idempotent collection migration
            migration_result = await migrate_strategy_os_collections(db_manager.db)

            # Initialize model role registry
            app.state.model_role_registry = ModelRoleRegistry(settings)

            logger.info(
                f"Strategy OS v0 initialized: "
                f"{len(migration_result)} collections, "
                f"{len(app.state.model_role_registry)} model roles"
            )
        except Exception as e:
            logger.warning(f"Strategy OS initialization failed (non-fatal): {e}")
            app.state.model_role_registry = None
    else:
        app.state.model_role_registry = None

    # Strategy spec store: a single shared MongoSpecStore instance is
    # built here and reused by:
    #   * the strategy-specs CRUD router (DI),
    #   * the promotion manager (via the store's snapshots view),
    #   * the strategy spec selector (Phase 5 / Task 65), and
    #   * any future seeding/maintenance flows that need the same view.
    if settings.strategy_os_enabled:
        try:
            from backend.agent.strategy.spec_store import MongoSpecStore
            from backend.agent.strategy.adaptive_decision_store import (
                MongoAdaptiveDecisionStore,
            )
            from backend.agent.coordinator import set_spec_store

            spec_store = MongoSpecStore(db_manager.db)
            await spec_store.ensure_loaded()
            app.state.spec_store = spec_store

            # Phase 6 / Task 78: pass the shared P1 runtime profile store
            # and P2 resource collector through to the coordinator so
            # adaptive routing is enabled when both signals are
            # available. Backward-compatible: missing extras fall back
            # to the basic StrategySpecSelector.
            adaptive_runtime_profile_store = getattr(
                app.state, "runtime_profile_store", None
            )
            adaptive_resource_collector = getattr(
                app.state, "resource_snapshot_collector", None
            )

            # F8 / Task 85: persist every adaptive routing decision into
            # the ``adaptive_selection_decisions`` collection so
            # operators can analyze selection patterns post-hoc. The
            # TTL retention window is configurable via
            # ``settings.adaptive_decisions_ttl_days``.
            try:
                adaptive_decision_store = MongoAdaptiveDecisionStore(
                    db_manager.db,
                    ttl_days=settings.adaptive_decisions_ttl_days,
                )
                await adaptive_decision_store.ensure_indexes()
                app.state.adaptive_decision_store = adaptive_decision_store
            except Exception as e:
                logger.warning(
                    f"Failed to initialize MongoAdaptiveDecisionStore (non-fatal): {e}"
                )
                adaptive_decision_store = None
                app.state.adaptive_decision_store = None

            set_spec_store(
                spec_store,
                runtime_profile_store=adaptive_runtime_profile_store,
                resource_collector=adaptive_resource_collector,
                evaluation_results_db=db_manager.db,
                decision_store=adaptive_decision_store,
            )
            logger.info(
                "Shared MongoSpecStore initialized and wired to coordinator + router DI "
                "(adaptive=%s, decision_store=%s)",
                bool(
                    adaptive_runtime_profile_store
                    or adaptive_resource_collector
                ),
                bool(adaptive_decision_store),
            )
        except Exception as e:
            logger.warning(f"Failed to initialize shared MongoSpecStore: {e}")
            app.state.spec_store = None
    else:
        app.state.spec_store = None

    # Strategy run-trace store (Phase 5 / Task 68). Persists per-run
    # trace docs into ``strategy_runs`` so the nightly report generator
    # consumes real run data instead of an empty placeholder.
    if settings.strategy_os_enabled:
        try:
            from backend.agent.strategy.run_trace_store import MongoRunTraceStore
            from backend.agent.coordinator import set_run_trace_store

            run_trace_store = MongoRunTraceStore(
                db_manager.db,
                ttl_seconds=settings.strategy_runs_ttl_days * 86400,
            )
            await run_trace_store.ensure_indexes()
            app.state.run_trace_store = run_trace_store
            set_run_trace_store(run_trace_store)
            logger.info(
                "Shared MongoRunTraceStore initialized and wired to coordinator"
            )
        except Exception as e:
            logger.warning(f"Failed to initialize shared MongoRunTraceStore: {e}")
            app.state.run_trace_store = None
    else:
        app.state.run_trace_store = None

    # Strategy spec auto-seed (Phase 5 / Task 60)
    if settings.strategy_os_enabled and settings.strategy_spec_auto_seed:
        try:
            from backend.scripts.seed_strategy_specs import seed_strategy_specs

            seed_summary = await seed_strategy_specs(
                db_manager.db,
                spec_dir=settings.strategy_spec_dir,
            )
            logger.info(
                "Strategy spec seed: loaded=%d inserted=%d updated=%d "
                "skipped=%d activated=%s errors=%d",
                seed_summary.get("loaded", 0),
                seed_summary.get("inserted", 0),
                seed_summary.get("updated", 0),
                seed_summary.get("skipped", 0),
                seed_summary.get("activated", []),
                len(seed_summary.get("errors", [])),
            )
        except Exception as e:
            logger.warning(f"Strategy spec auto-seed failed (non-fatal): {e}")

    # Strategy scheduler persistence + auto-resume (Phase 5 / Task 61)
    if settings.strategy_os_enabled:
        try:
            from backend.agent.strategy.scheduler_store import MongoSchedulerStore
            from backend.scheduler.scheduler_daemon import SchedulerDaemon

            scheduler_store = MongoSchedulerStore(db_manager.db)
            await scheduler_store.ensure_indexes()
            app.state.scheduler_store = scheduler_store

            scheduler_daemon = SchedulerDaemon(
                db=db_manager.db,
                ollama_base_url=settings.ollama_base_url
                or "http://host.docker.internal:11434",
                store=scheduler_store,
                tick_interval_seconds=settings.strategy_scheduler_tick_interval_seconds,
                nightly_report_enabled=settings.strategy_nightly_report_enabled,
                nightly_report_hour_local=settings.strategy_nightly_report_hour_local,
                nightly_report_timezone=settings.strategy_nightly_report_timezone,
                nightly_report_dir=settings.strategy_nightly_report_dir,
                nightly_report_regression_threshold=settings.strategy_nightly_report_regression_threshold,
                nightly_report_lookback_hours=settings.strategy_nightly_report_lookback_hours,
            )
            app.state.scheduler_daemon = scheduler_daemon

            if settings.strategy_scheduler_auto_resume:
                resume_summary = await scheduler_daemon.resume_from_store()
                logger.info(
                    "Scheduler resumed: %d schedules loaded, %d paused, %d with open circuit breakers",
                    resume_summary.get("loaded", 0),
                    resume_summary.get("paused", 0),
                    resume_summary.get("open_breakers", 0),
                )
            else:
                logger.info("Scheduler auto-resume disabled by configuration")
        except Exception as e:
            logger.warning(f"Scheduler persistence init failed (non-fatal): {e}")
            app.state.scheduler_store = None
            app.state.scheduler_daemon = None
    else:
        app.state.scheduler_store = None
        app.state.scheduler_daemon = None

    # Log resolved LLM configuration
    logger.info("LLM Configuration:")
    logger.info(f"  Orchestrator: provider={settings.orchestrator_provider}, model={settings.orchestrator_model}")
    logger.info(f"  Worker: provider={settings.worker_provider}, model={settings.worker_model}")
    logger.info(f"  Embedding: provider={settings.embedding_provider}, model={settings.embedding_model}")
    logger.info(f"  Ollama Base URL: {settings.ollama_base_url}")

    # Non-blocking LLM health check at startup
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            if "ollama" in [
                settings.orchestrator_provider,
                settings.worker_provider,
                settings.embedding_provider,
            ]:
                ollama_url = settings.ollama_base_url or "http://host.docker.internal:11434"
                try:
                    resp = await client.get(f"{ollama_url}/api/tags")
                    if resp.status_code == 200:
                        models = [m["name"] for m in resp.json().get("models", [])]
                        logger.info(f"  Ollama reachable, available models: {models}")
                    else:
                        logger.warning(f"  Ollama responded with status {resp.status_code}")
                except Exception as e:
                    logger.warning(f"  Ollama unreachable at {ollama_url}: {e}")
    except Exception as e:
        logger.warning(f"  Startup LLM health check failed (non-blocking): {e}")

    logger.info(f"API ready at http://0.0.0.0:{settings.api_port}")
    
    yield
    
    # Shutdown - gracefully handle running ingestion jobs
    logger.info("Shutting down RecallHub API...")
    
    try:
        await graceful_shutdown_handler(db_manager)
    except Exception as e:
        logger.warning(f"Error during graceful shutdown: {e}")
    
    # Persist any final scheduler state before closing the DB connection
    try:
        scheduler_daemon = getattr(app.state, "scheduler_daemon", None)
        if scheduler_daemon is not None and scheduler_daemon._current_run is not None:
            run = scheduler_daemon._current_run
            await scheduler_daemon.store.update_runtime_state(
                run.schedule_id,
                last_run_status=run.status.value,
                circuit_breaker=scheduler_daemon.circuit_breaker.state.value,
            )
    except Exception as e:
        logger.warning(f"Error persisting final scheduler state: {e}")
    
    await db_manager.disconnect()
    logger.info("Database connection closed")


# Get documentation URLs based on environment
docs_config = get_docs_urls()

# Create FastAPI app
app = FastAPI(
    title="RecallHub API",
    description="""
    Production-ready API for the RecallHub system.
    
    ## Features
    - **Chat**: Conversational AI with RAG-powered responses
    - **Search**: Semantic, text, and hybrid search capabilities
    - **Profiles**: Multi-project profile management
    - **Ingestion**: Document ingestion control
    - **System**: Health checks and statistics
    """,
    version="1.0.0",
    docs_url=docs_config["docs_url"],
    redoc_url=docs_config["redoc_url"],
    openapi_url=docs_config["openapi_url"],
    lifespan=lifespan
)

# Configure CORS - include production domains
cors_origins = settings.cors_origins.copy()
# Add production domains if not already present
production_origins = [
    "https://recallhub.app",
    "https://www.recallhub.app",
    "https://api.recallhub.app",
]
for origin in production_origins:
    if origin not in cors_origins:
        cors_origins.append(origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Add request timeout middleware (after CORS)
app.add_middleware(RequestTimeoutMiddleware)


# ============== Exception Handlers ==============

def _is_admin_request(request: Request) -> bool:
    """Check if the request is from an admin user.
    
    Looks at the Authorization header and decodes the JWT to check is_admin.
    Returns False if unable to determine (safer default).
    """
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        
        token = auth_header[7:]  # Remove "Bearer " prefix
        from jose import jwt
        import os
        secret_key = os.getenv("JWT_SECRET_KEY", "recallhub-secret-key-change-in-production")
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        
        # Look up user in database to check admin status
        # For performance, we just check if request came with valid token
        # The actual admin check happens in the error response handling
        return payload.get("is_admin", False)
    except Exception:
        return False


def _get_error_id() -> str:
    """Generate a unique error ID for tracking."""
    return str(uuid.uuid4())[:8]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors with user-friendly messages."""
    error_id = _get_error_id()
    
    # Log full details
    logger.error(
        f"Validation error [{error_id}] on {request.method} {request.url.path}: "
        f"{exc.errors()}"
    )
    
    # User-friendly message
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "Invalid request data. Please check your input and try again.",
            "error_id": error_id,
        }
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with appropriate detail levels."""
    error_id = _get_error_id()
    
    # Log the error
    logger.warning(
        f"HTTP {exc.status_code} [{error_id}] on {request.method} {request.url.path}: "
        f"{exc.detail}"
    )
    
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": f"http_{exc.status_code}",
            "message": exc.detail,
            "error_id": error_id,
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors.
    
    - Regular users: See a friendly message with error ID for support
    - Admin users: See technical details (but no stack trace in response)
    - All errors are extensively logged with full stack traces
    """
    error_id = _get_error_id()
    
    # Get request context for logging
    request_info = {
        "method": request.method,
        "path": request.url.path,
        "query": str(request.query_params),
        "client": request.client.host if request.client else "unknown",
    }
    
    # Log extensively with full stack trace
    logger.error(
        f"Unhandled exception [{error_id}]\n"
        f"  Request: {request_info['method']} {request_info['path']}\n"
        f"  Query: {request_info['query']}\n"
        f"  Client: {request_info['client']}\n"
        f"  Exception Type: {type(exc).__name__}\n"
        f"  Exception Message: {str(exc)}\n"
        f"  Stack Trace:\n{traceback.format_exc()}"
    )
    
    # Check if user is admin for detailed error response
    is_admin = _is_admin_request(request)
    
    # User-friendly message for everyone
    user_message = (
        "We encountered an issue processing your request. "
        "Please try again. If the problem persists, contact support with error ID: "
        f"{error_id}"
    )
    
    # Admin gets more details (but still no stack trace in response - that's only in logs)
    if is_admin or settings.debug:
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": user_message,
                "error_id": error_id,
                "technical_details": {
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                    "path": request_info["path"],
                    "method": request_info["method"],
                }
            }
        )
    else:
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "message": user_message,
                "error_id": error_id,
            }
        )


# Include routers
app.include_router(
    chat.router,
    prefix="/api/v1/chat",
    tags=["Chat"]
)

app.include_router(
    search.router,
    prefix="/api/v1/search",
    tags=["Search"]
)

app.include_router(
    profiles.router,
    prefix="/api/v1/profiles",
    tags=["Profiles"]
)

app.include_router(
    ingestion.router,
    prefix="/api/v1/ingestion",
    tags=["Ingestion"]
)

app.include_router(
    system.router,
    prefix="/api/v1/system",
    tags=["System"]
)

app.include_router(
    sessions.router,
    prefix="/api/v1/sessions",
    tags=["Chat Sessions"]
)

app.include_router(
    auth.router,
    prefix="/api/v1/auth",
    tags=["Authentication"]
)

app.include_router(
    status.router,
    prefix="/api/v1/status",
    tags=["Status Dashboard"]
)

app.include_router(
    indexes.router,
    prefix="/api/v1/indexes",
    tags=["Search Indexes"]
)

app.include_router(
    ingestion_queue.router,
    prefix="/api/v1/ingestion-queue",
    tags=["Ingestion Queue"]
)

app.include_router(
    file_registry.router,
    prefix="/api/v1/file-registry",
    tags=["File Registry"]
)

app.include_router(
    local_llm.router,
    prefix="/api/v1/local-llm",
    tags=["Local LLM"]
)

# Cloud Sources routers
app.include_router(
    cloud_providers,
    prefix="/api/v1/cloud-sources",
    tags=["Cloud Sources"]
)

app.include_router(
    cloud_connections,
    prefix="/api/v1/cloud-sources",
    tags=["Cloud Sources"]
)

app.include_router(
    cloud_oauth,
    prefix="/api/v1/cloud-sources",
    tags=["Cloud Sources"]
)

app.include_router(
    cloud_sync,
    prefix="/api/v1/cloud-sources",
    tags=["Cloud Sources"]
)

app.include_router(
    cloud_cache,
    prefix="/api/v1/cloud-sources",
    tags=["Cloud Sources Cache"]
)

app.include_router(
    prompts.router,
    prefix="/api/v1/prompts",
    tags=["Prompt Management"]
)

app.include_router(
    model_versions.router,
    prefix="/api/v1/model-versions",
    tags=["Model Versions"]
)

app.include_router(
    strategies.router,
    prefix="/api/v1/strategies",
    tags=["Agent Strategies"]
)

app.include_router(
    backup.router,
    prefix="/api/v1/backups",
    tags=["Backup & Restore"]
)

app.include_router(
    embedding_benchmark.router,
    prefix="/api/v1/benchmark",
    tags=["Embedding Benchmark"]
)

app.include_router(
    tenant.router,
    prefix="/api/v1",
    tags=["Tenant"]
)

app.include_router(
    support.router,
    prefix="/api/v1",
    tags=["Support"]
)

app.include_router(
    debug.router,
    prefix="/api/v1",
    tags=["Debug"]
)

app.include_router(
    evaluation_router_module.router,
    prefix="/api/v1/evaluation",
    tags=["Evaluation"]
)

app.include_router(
    scheduler_router_module.router,
    prefix="/api/v1/scheduler",
    tags=["Scheduler"]
)

app.include_router(telemetry_router_module.router)

app.include_router(
    strategy_specs_router_module.router,
    prefix="/api/v1",
    tags=["Strategy Specs"]
)


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "RecallHub API",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "health": "/api/v1/system/health"
    }


# Health check at root level for load balancers
@app.get("/health", tags=["Health"])
async def health():
    """Quick health check for load balancers."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.debug,
        workers=settings.api_workers if not settings.debug else 1
    )
