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
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.routers import chat, search, profiles, ingestion, system
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
    logger.info(f"API ready at http://0.0.0.0:{settings.api_port}")
    
    yield
    
    # Shutdown
    logger.info("Shutting down MongoDB RAG Agent API...")
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


# Exception handlers
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    logger.exception(f"Unhandled error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "message": "An unexpected error occurred",
            "detail": str(exc) if settings.debug else None
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
