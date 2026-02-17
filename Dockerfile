# RecallHub - Docker Image
# Multi-stage build for optimized production image

FROM python:3.11-slim as base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install minimal system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files first for better caching
COPY pyproject.toml ./

# Install dependencies with optimizations
# Exclude heavy ML dependencies for CLI-only usage
RUN uv venv && \
    uv sync --no-dev --no-editable --compile-bytecode --resolution=lowest-direct && \
    # Remove heavy ML packages that aren't needed for basic CLI operations
    . .venv/bin/activate && \
    pip uninstall -y torch torchvision torchaudio transformers openai-whisper 2>/dev/null || true && \
    # Clean up caches
    find .venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find .venv -type f -name "*.pyc" -delete 2>/dev/null || true && \
    rm -rf /root/.cache/uv /root/.cache/pip ~/.cache

# Copy application code
COPY src/ ./src/
COPY profiles.yaml ./
# NOTE: Skipping documents/ copy for smaller CLI image - mount as volume if needed
# COPY documents/ ./documents/

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
