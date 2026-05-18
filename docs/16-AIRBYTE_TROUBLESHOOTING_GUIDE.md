# Airbyte Deployment Troubleshooting Guide

This guide provides comprehensive troubleshooting procedures for Airbyte deployment issues in the RecallHub system.

## Table of Contents
1. [Common Issues and Solutions](#common-issues-and-solutions)
2. [Diagnostic Commands](#diagnostic-commands)
3. [Log Analysis](#log-analysis)
4. [Automated Recovery Procedures](#automated-recovery-procedures)
5. [Performance Optimization](#performance-optimization)
6. [Security Considerations](#security-considerations)

## Common Issues and Solutions

### 1. Containers Fail to Start

**Symptoms:**
- Containers show "Exited" status
- Services not responding on expected ports
- Error messages about missing dependencies

**Diagnosis:**
```powershell
# Check container status
.\start-airbyte.ps1 -Status

# Check detailed logs
.\start-airbyte.ps1 -Logs

# Check Docker resources
docker info
```

**Solutions:**

#### Version Compatibility Issues
```powershell
# Update to stable Airbyte version (already fixed in docker-compose.airbyte.yml)
# Current version: 0.60.27 (was 0.50.33)

# Clean restart
.\start-airbyte.ps1 -Stop
.\start-airbyte.ps1 -Cleanup  # Confirm with YES
.\start-airbyte.ps1
```

#### Resource Constraints
```powershell
# Check Docker Desktop resources
# Increase allocated RAM (minimum 4GB recommended)
# Increase allocated CPU cores (minimum 2 cores recommended)

# Restart Docker Desktop after resource changes
```

#### Port Conflicts
```powershell
# Check if ports are in use
netstat -an | Select-String "11020\|11021\|5432\|7233"

# Kill conflicting processes if needed
Get-Process -Id (Get-NetTCPConnection -LocalPort 11020).OwningProcess | Stop-Process -Force
```

### 2. Database Connection Issues

**Symptoms:**
- `airbyte-db` container keeps restarting
- "Connection refused" errors
- PostgreSQL authentication failures

**Solutions:**
```powershell
# Check database container logs
docker logs rag-airbyte-db

# Verify database directory permissions
Get-ChildItem -Path "data/airbyte/db" -Recurse | Where-Object {$_.Mode -match "d"}

# Reset database (last resort)
.\start-airbyte.ps1 -Stop
Remove-Item -Path "data/airbyte/db" -Recurse -Force
mkdir "data/airbyte/db"
.\start-airbyte.ps1
```

### 3. API Service Not Responding

**Symptoms:**
- `airbyte-server` container exits with errors
- `/api/v1/health` endpoint returns 503 or connection timeout
- Bean injection errors in logs

**Root Cause Analysis:**
The error `NoSuchBeanException: No bean of type [SecretPersistence] exists` indicates a configuration issue in older Airbyte versions.

**Solution:**
```powershell
# The fix is already implemented in docker-compose.airbyte.yml
# Added environment variables:
# - SECRET_PERSISTENCE=NONE
# - Updated to version 0.60.27

# Restart with clean state
.\start-airbyte.ps1 -Restart
```

### 4. Worker Service Failures

**Symptoms:**
- `airbyte-worker` crashes during sync operations
- "Docker socket permission denied" errors
- Memory allocation failures

**Solutions:**
```powershell
# Check Docker socket permissions
Get-Acl C:\ProgramData\docker\run\docker.sock | Format-List

# Restart worker service specifically
docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml restart airbyte-worker

# Check resource limits
docker stats rag-airbyte-worker
```

## Diagnostic Commands

### PowerShell Script Diagnostics
```powershell
# Comprehensive health check
.\start-airbyte.ps1 -HealthCheck

# Real-time log monitoring
.\start-airbyte.ps1 -Logs

# Status overview
.\start-airbyte.ps1 -Status

# Clean restart
.\start-airbyte.ps1 -Restart
```

### Manual Docker Commands
```powershell
# List all Airbyte containers
docker ps -a | Select-String "rag-airbyte"

# Check container resource usage
docker stats rag-airbyte-db rag-airbyte-server rag-airbyte-worker

# Inspect container configuration
docker inspect rag-airbyte-server

# Check container logs
docker logs rag-airbyte-server --tail 100
docker logs rag-airbyte-worker --tail 100
docker logs rag-airbyte-db --tail 100
```

### Network Diagnostics
```powershell
# Check port availability
Test-NetConnection -ComputerName localhost -Port 11020
Test-NetConnection -ComputerName localhost -Port 11021
Test-NetConnection -ComputerName localhost -Port 5432
Test-NetConnection -ComputerName localhost -Port 7233

# Check Docker network
docker network ls | Select-String "rag"
docker network inspect recallhub_rag-network
```

## Log Analysis

### Key Log Locations

1. **Container Logs:**
   ```powershell
   # Server logs (most important for API issues)
   docker logs rag-airbyte-server
   
   # Worker logs (for sync job issues)
   docker logs rag-airbyte-worker
   
   # Database logs (for connection issues)
   docker logs rag-airbyte-db
   
   # Webapp logs (for UI issues)
   docker logs rag-airbyte-webapp
   ```

2. **Application Logs:**
   ```powershell
   # Backend logs
   Get-Content backend-logs.txt -Tail 100
   
   # Monitor logs in real-time
   Get-Content backend-logs.txt -Wait
   ```

### Common Error Patterns

#### Bean Injection Errors
```
NoSuchBeanException: No bean of type [SecretPersistence] exists
```
**Solution:** Update to Airbyte 0.60.27+ with `SECRET_PERSISTENCE=NONE` environment variable.

#### Database Migration Issues
```
Flyway migration failed
Database schema mismatch
```
**Solution:** 
```powershell
# Stop services
.\start-airbyte.ps1 -Stop

# Clear database (will lose data)
Remove-Item -Path "data/airbyte/db" -Recurse -Force
mkdir "data/airbyte/db"

# Restart
.\start-airbyte.ps1
```

#### Docker Socket Permissions
```
permission denied while trying to connect to the Docker daemon socket
```
**Solution:**
```powershell
# Restart Docker Desktop as Administrator
# Or ensure user has Docker group permissions
```

## Automated Recovery Procedures

### Using the Enhanced Startup Script
The `start-airbyte.ps1` script now includes built-in recovery features:

```powershell
# Automatic pre-flight checks
.\start-airbyte.ps1  # Will perform checks automatically

# Force restart with cleanup
.\start-airbyte.ps1 -Restart

# Complete cleanup and redeploy
.\start-airbyte.ps1 -Cleanup  # Interactive confirmation
```

### Python Monitoring Script
```powershell
# Run one-time health check
python scripts\airbyte-monitor.py --once

# Continuous monitoring
python scripts\airbyte-monitor.py --continuous --interval 30

# Generate detailed report
python scripts\airbyte-monitor.py --once --output health-report.json
```

### Automated Container Restart Policy
Docker-compose already includes:
```yaml
restart: unless-stopped
```

For manual watchdog script:
```powershell
# Create watchdog.ps1
while ($true) {
    $unhealthy = docker ps -q -f "name=rag-airbyte" -f "status=exited"
    if ($unhealthy) {
        Write-Host "Restarting unhealthy containers..."
        .\start-airbyte.ps1 -Restart
    }
    Start-Sleep 60
}
```

## Performance Optimization

### Resource Allocation Recommendations

**Minimum Requirements:**
- RAM: 4GB
- CPU: 2 cores
- Disk: 10GB free space

**Recommended for Production:**
- RAM: 8GB+
- CPU: 4 cores+
- Disk: 50GB+ SSD

### Docker Configuration Tuning
```powershell
# In Docker Desktop Settings:
# Resources → Advanced
# - CPUs: 4
# - Memory: 8GB
# - Swap: 2GB
# - Disk image size: 64GB
```

### Airbyte Configuration Tuning
```yaml
# In docker-compose.airbyte.yml
airbyte-worker:
  environment:
    - MAX_SYNC_WORKERS=3  # Reduce if resource constrained
    - JOB_MAIN_CONTAINER_MEMORY_LIMIT=2GB  # Adjust based on available RAM
```

### Database Performance
```powershell
# Check PostgreSQL performance
docker exec rag-airbyte-db psql -U airbyte -c "SELECT * FROM pg_stat_activity;"

# Optimize database (advanced)
docker exec rag-airbyte-db psql -U airbyte -c "ANALYZE;"
```

## Security Considerations

### Credential Management
```powershell
# Never hardcode credentials in docker-compose files
# Use environment variables or Docker secrets

# Check for hardcoded credentials
Select-String -Path "docker-compose.airbyte.yml" -Pattern "password|secret|key" -CaseSensitive:$false
```

### Network Security
```powershell
# Verify network isolation
docker network inspect recallhub_rag-network

# Check exposed ports
docker port rag-airbyte-webapp
docker port rag-airbyte-server
```

### Data Protection
```powershell
# Regular backups
Copy-Item -Path "data/airbyte" -Destination "backup/airbyte-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Recurse

# Verify backup integrity
Get-ChildItem -Path "backup" -Recurse | Measure-Object -Property Length -Sum
```

## Testing and Validation

### Unit Tests
```powershell
# Run Airbyte deployment tests
python -m pytest tests/test_airbyte_deployment.py -v

# Run integration tests (requires running services)
python -m pytest tests/test_airbyte_integration.py -v --quick
```

### Manual Verification Steps
```powershell
# 1. Check all containers are running
.\start-airbyte.ps1 -Status

# 2. Verify API health
curl http://localhost:11021/api/v1/health

# 3. Access web interface
Start-Process "http://localhost:11020"

# 4. Test backend integration
curl http://localhost:11000/system/airbyte/status
```

## Emergency Procedures

### Complete System Reset
```powershell
# 1. Stop all services
.\start-airbyte.ps1 -Stop

# 2. Backup current data (optional)
Copy-Item -Path "data/airbyte" -Destination "emergency-backup-$(Get-Date -Format 'yyyyMMdd')" -Recurse

# 3. Remove all containers
docker-compose -f docker-compose.yml -f docker-compose.airbyte.yml down --remove-orphans

# 4. Clear data directories
Remove-Item -Path "data/airbyte" -Recurse -Force

# 5. Reinitialize
mkdir "data/airbyte"
mkdir "data/airbyte/db"
mkdir "data/airbyte/config"
mkdir "data/airbyte/workspace"
mkdir "data/airbyte/local"

# 6. Restart
.\start-airbyte.ps1
```

### Quick Recovery Checklist
- [ ] Check Docker Desktop is running
- [ ] Verify sufficient system resources
- [ ] Check for port conflicts
- [ ] Review recent error logs
- [ ] Try `.\start-airbyte.ps1 -Restart`
- [ ] If persistent issues, run `.\start-airbyte.ps1 -Cleanup`
- [ ] As last resort, perform complete system reset

## Contact and Support

For persistent issues not resolved by this guide:
1. Check GitHub issues for similar problems
2. Review Airbyte official documentation
3. Consult Docker community forums
4. Contact system administrator

---

*This document is maintained as part of the RecallHub project. Last updated: February 2026*
