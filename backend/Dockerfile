# MongoDB RAG Backend - Docker Image
# FastAPI backend with UV package manager

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml ./

# Install dependencies
RUN uv venv && uv sync --no-dev

# Copy application code
COPY src/ ./src/
COPY backend/ ./backend/
COPY profiles.yaml ./

# Create directories for profile data
RUN mkdir -p /app/projects /app/data /app/documents

# Set default environment variables
ENV MONGODB_URI=mongodb://mongodb:27017/?directConnection=true \
    MONGODB_DATABASE=rag_db \
    PROFILES_PATH=/app/profiles.yaml

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/system/health || exit 1

# Run FastAPI with uvicorn
CMD ["uv", "run", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
