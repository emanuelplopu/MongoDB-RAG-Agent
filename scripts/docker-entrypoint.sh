#!/bin/bash
# Docker entrypoint script for RecallHub
# Handles initialization and command routing

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}RecallHub - Docker Container${NC}"
echo "========================================"

# Wait for MongoDB to be available
echo -e "${YELLOW}Waiting for MongoDB...${NC}"
max_attempts=30
attempt=1
while [ $attempt -le $max_attempts ]; do
    if mongosh --eval "db.adminCommand('ping')" "$MONGODB_URI" &>/dev/null; then
        echo -e "${GREEN}MongoDB is ready!${NC}"
        break
    fi
    echo "Attempt $attempt/$max_attempts - MongoDB not ready yet..."
    sleep 2
    attempt=$((attempt + 1))
done

if [ $attempt -gt $max_attempts ]; then
    echo -e "${RED}Failed to connect to MongoDB after $max_attempts attempts${NC}"
    exit 1
fi

# Handle different commands
case "$1" in
    "ingest")
        echo -e "${YELLOW}Running document ingestion...${NC}"
        shift
        exec uv run python -m src.ingestion.ingest "$@"
        ;;
    "cli")
        echo -e "${YELLOW}Starting CLI...${NC}"
        exec uv run python -m src.cli
        ;;
    "shell")
        echo -e "${YELLOW}Starting shell...${NC}"
        exec /bin/bash
        ;;
    *)
        # Default: run CLI
        echo -e "${YELLOW}Starting CLI (default)...${NC}"
        exec uv run python -m src.cli
        ;;
esac
