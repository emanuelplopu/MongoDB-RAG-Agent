# RecallHub Backend - Docker Image
# Multi-stage build for optimized production image

# ============================================
# Stage 1: Builder - Install dependencies
# ============================================
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install minimal build dependencies (only in builder stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files for caching
COPY pyproject.toml ./

# Copy requirements file
COPY requirements.txt ./

# Create venv and install dependencies with optimizations
# Using standard python venv instead of uv to ensure packages install correctly
RUN python -m venv /app/.venv && \
    . /app/.venv/bin/activate && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    # Clean up caches to reduce image size (but preserve installed packages)
    find /app/.venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /app/.venv -type f -name "*.pyc" -delete 2>/dev/null || true && \
    # Only clean pip cache, not the installed packages
    rm -rf /root/.cache/pip

# ============================================
# Stage 2: Playwright - Install browser (optional)
# ============================================
FROM python:3.11-slim AS playwright

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install playwright WITHOUT browser to save space
# Browser installation adds ~300MB+ and is rarely needed
RUN python -m ensurepip --upgrade && \
    python -m pip install --no-cache-dir playwright && \
    rm -rf /root/.cache/pip

# ============================================
# Stage 3: Runtime - Final minimal image
# ============================================
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

# Install only essential runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    antiword \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Copy virtual environment from builder stage (has all main dependencies)
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src/ ./src/
COPY backend/ ./backend/
COPY profiles.yaml ./

# Create directories for data
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

# Run FastAPI with uvicorn (using venv python directly)
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
