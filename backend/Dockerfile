# MongoDB RAG Backend - Docker Image
# Multi-stage build for optimized production image

# ============================================
# Stage 1: Builder - Install dependencies
# ============================================
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install build dependencies (only in builder stage)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy dependency files for caching
COPY pyproject.toml ./

# Create venv and install dependencies
# Use --compile-bytecode for faster startup, --no-cache for smaller size
RUN uv venv /app/.venv && \
    . /app/.venv/bin/activate && \
    uv sync --no-dev --compile-bytecode && \
    rm -rf /root/.cache/uv /root/.cache/pip

# ============================================
# Stage 2: Playwright - Install browser
# ============================================
FROM python:3.11-slim AS playwright

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Install playwright and chromium (cached separately)
RUN pip install --no-cache-dir playwright && \
    playwright install chromium --with-deps && \
    rm -rf /root/.cache/pip /var/lib/apt/lists/*

# ============================================
# Stage 3: Runtime - Final minimal image
# ============================================
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV="/app/.venv"

# Install only runtime dependencies (no build-essential!)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    # Playwright runtime dependencies only
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libdbus-1-3 \
    libatspi2.0-0 \
    libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Copy virtual environment from playwright stage (includes browser)
COPY --from=playwright /app/.venv /app/.venv
COPY --from=playwright /root/.cache/ms-playwright /root/.cache/ms-playwright

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
