# RecallHub - Docker Image
# Multi-stage build for optimized production image

FROM python:3.11-slim as base

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
COPY profiles.yaml ./
COPY documents/ ./documents/

# Create directories for profile data
RUN mkdir -p /app/projects /app/data

# Set default environment variables
ENV MONGODB_URI=mongodb://mongodb:27017/?directConnection=true \
    MONGODB_DATABASE=rag_db \
    PROFILES_PATH=/app/profiles.yaml

# Expose port for potential web interface (future)
EXPOSE 8000

# Default command - run CLI
CMD ["uv", "run", "python", "-m", "src.cli"]
