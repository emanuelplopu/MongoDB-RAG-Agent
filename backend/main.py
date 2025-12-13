"""
FastAPI Backend for MongoDB RAG Agent.

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
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exceptions import RequestValidationError

from backend.routers import chat, search, profiles, ingestion, system, sessions, auth
from backend.routers.system import load_config_from_db
from backend.routers.ingestion import check_and_resume_interrupted_jobs, graceful_shutdown_handler
from backend.core.config import settings
from backend.core.database import DatabaseManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Application lifespan manager - startup and shutdown."""
    # Startup
    logger.info("Starting MongoDB RAG Agent API...")
    
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
    
    # Check for and resume interrupted ingestion jobs
    try:
        resumed_job = await check_and_resume_interrupted_jobs(db_manager)
        if resumed_job:
            logger.info(f"Resumed interrupted ingestion job: {resumed_job}")
    except Exception as e:
        logger.warning(f"Failed to check for interrupted jobs: {e}")
    
    logger.info(f"API ready at http://0.0.0.0:{settings.api_port}")
    
    yield
    
    # Shutdown - gracefully handle running ingestion jobs
    logger.info("Shutting down MongoDB RAG Agent API...")
    
    try:
        await graceful_shutdown_handler(db_manager)
    except Exception as e:
        logger.warning(f"Error during graceful shutdown: {e}")
    
    await db_manager.disconnect()
    logger.info("Database connection closed")


# Create FastAPI app
app = FastAPI(
    title="MongoDB RAG Agent API",
    description="""
    Production-ready API for the MongoDB RAG Agent system.
    
    ## Features
    - **Chat**: Conversational AI with RAG-powered responses
    - **Search**: Semantic, text, and hybrid search capabilities
    - **Profiles**: Multi-project profile management
    - **Ingestion**: Document ingestion control
    - **System**: Health checks and statistics
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        secret_key = os.getenv("JWT_SECRET_KEY", "mongodb-rag-secret-key-change-in-production")
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


# Root endpoint
@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information."""
    return {
        "name": "MongoDB RAG Agent API",
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
