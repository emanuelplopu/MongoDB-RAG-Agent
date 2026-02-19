# Qoder Project Rules

## CRITICAL: Docker Build Commands

When rebuilding backend or ingestion-worker after code changes:

### For Code-Only Changes (ALWAYS use this)
```powershell
# Build and deploy both services (~5 seconds)
.\build-backend.ps1 -All

# Or individually:
.\build-backend.ps1 -Fast     # Backend only (~3 sec)
.\build-backend.ps1 -Worker   # Ingestion worker only (~1 sec)
```

### Only When Dependencies Change (requirements*.txt)
```powershell
.\build-backend.ps1 -Base     # ~15 min (one-time)
```
Then use `-All` to rebuild both services.

### Check Base Image Status
```powershell
.\build-backend.ps1 -Check
```

### NEVER DO THIS FOR CODE CHANGES
```powershell
# ❌ WRONG - Takes 14+ minutes, rebuilds ALL ML dependencies
docker-compose build --no-cache backend
docker-compose build --no-cache ingestion-worker
```

## Why This Matters

The project uses a two-tier Docker build with a **shared base image**:

```
recallhub-backend-base:latest (~5GB, built once)
          │
    ┌─────┴─────┐
    ▼           ▼
 Backend    Worker
 (~3 sec)   (~1 sec)
```

1. **Base image** (`Dockerfile.base`): Contains all ML dependencies - takes ~15 min
2. **Backend** (`Dockerfile.fast`): Copies app code on base - takes ~3 sec
3. **Worker** (`Dockerfile.worker`): Copies app code on base - takes ~1 sec

**Always use `.\build-backend.ps1 -All` for code changes!**
