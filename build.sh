#!/bin/bash
# Build Script for MongoDB-RAG-Agent Docker Images
# Usage: ./build.sh [option]
# Options:
#   light    - Build lightweight images (default) - excludes heavy ML deps
#   heavy    - Build full ML images - includes transformers, whisper, playwright
#   frontend - Build only frontend
#   backend  - Build only backend (lightweight)
#   all      - Build all images (lightweight versions)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BUILD_FRONTEND=true
BUILD_BACKEND=true
BUILD_APP=true
ML_HEAVY=false

# Parse arguments
case "${1:-light}" in
  light)
    echo "Building LIGHTWEIGHT images (excluding heavy ML dependencies)..."
    ML_HEAVY=false
    ;;
  heavy)
    echo "Building HEAVY images (including full ML stack)..."
    ML_HEAVY=true
    ;;
  frontend)
    BUILD_FRONTEND=true
    BUILD_BACKEND=false
    BUILD_APP=false
    ;;
  backend)
    BUILD_FRONTEND=false
    BUILD_BACKEND=true
    BUILD_APP=false
    ML_HEAVY=false
    ;;
  app)
    BUILD_FRONTEND=false
    BUILD_BACKEND=false
    BUILD_APP=true
    ML_HEAVY=false
    ;;
  all)
    echo "Building ALL lightweight images..."
    ML_HEAVY=false
    ;;
  *)
    echo "Usage: $0 [light|heavy|frontend|backend|app|all]"
    echo "  light    - Lightweight images (default)"
    echo "  heavy    - Full ML images"
    echo "  frontend - Frontend only"
    echo "  backend  - Backend only (light)"
    echo "  app      - App only (light)"
    echo "  all      - All images (light)"
    exit 1
    ;;
esac

START_TIME=$(date +%s)

# Function to build image
build_image() {
  local name=$1
  local dockerfile=$2
  local context=$3
  local tag=${4:-latest}
  
  echo "========================================"
  echo "Building $name..."
  echo "Dockerfile: $dockerfile"
  echo "Context: $context"
  echo "========================================"
  
  local start=$(date +%s)
  
  docker build -f "$dockerfile" -t "mongodb-rag-agent-$name:$tag" "$context"
  
  local end=$(date +%s)
  local duration=$((end - start))
  echo "✅ $name built in ${duration}s"
  echo ""
}

# Build Frontend
if [ "$BUILD_FRONTEND" = true ]; then
  build_image "frontend" "frontend/Dockerfile" "frontend"
fi

# Build Backend
if [ "$BUILD_BACKEND" = true ]; then
  if [ "$ML_HEAVY" = true ]; then
    build_image "backend" "backend/Dockerfile.ml-heavy" "."
  else
    build_image "backend" "backend/Dockerfile" "."
  fi
fi

# Build App (CLI)
if [ "$BUILD_APP" = true ]; then
  build_image "app" "Dockerfile" "."
fi

# Show final image sizes
echo "========================================"
echo "FINAL IMAGE SIZES:"
echo "========================================"
docker images mongodb-rag-agent-* --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}" | grep -v "<none>"

END_TIME=$(date +%s)
TOTAL_DURATION=$((END_TIME - START_TIME))

echo ""
echo "🎉 All images built successfully in ${TOTAL_DURATION}s!"
echo ""
echo "Lightweight images built. For full ML capabilities, run:"
echo "  ./build.sh heavy"