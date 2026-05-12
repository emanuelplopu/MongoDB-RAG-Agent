#Requires -Version 5.1
<#
.SYNOPSIS
    Generates deployment configuration files from config.json.
.DESCRIPTION
    Reads installer/config.json and produces:
    - docker-compose.hardened.yml (complete, ready to use)
    - hardened.env (all environment variables)
    - setup-defines.iss (Inno Setup version defines)
    - nginx.hardened.conf (nginx reverse proxy config)
    - models-manifest.json (Ollama models manifest)
    All files are written to the specified output directory.
    This script NEVER modifies source files.
.PARAMETER ConfigPath
    Path to config.json. Defaults to installer/config.json
.PARAMETER OutputDir
    Directory to write generated files. Required.
.EXAMPLE
    .\New-BuildConfig.ps1 -OutputDir ".\build-temp"
.EXAMPLE
    .\New-BuildConfig.ps1 -ConfigPath ".\custom-config.json" -OutputDir "C:\output"
#>
param(
    [string]$ConfigPath,
    [Parameter(Mandatory=$true)]
    [string]$OutputDir
)

# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InstallerDir = Split-Path $ScriptDir -Parent

if (-not $ConfigPath) {
    $ConfigPath = Join-Path $InstallerDir "config.json"
}

if (-not (Test-Path $ConfigPath)) {
    throw "Config file not found: $ConfigPath"
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

# Validate required sections exist
$requiredSections = @("app", "models", "docker", "ports", "containers", "llm", "embedding", "mongodb", "security", "features", "healthChecks")
foreach ($section in $requiredSections) {
    if (-not $config.$section) {
        throw "Missing required config section: $section"
    }
}

# Create output directory
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

# ---------------------------------------------------------------------------
# Helper: Write file with UTF-8 no BOM
# ---------------------------------------------------------------------------
function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Content
    )
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ---------------------------------------------------------------------------
# Generator: docker-compose.hardened.yml
# ---------------------------------------------------------------------------
function New-DockerCompose {
    param($Config, $OutputDir)

    $version = $Config.app.version

    # Build volumes block
    $volumeLines = @()
    foreach ($vol in $Config.docker.volumes) {
        $volumeLines += "  ${vol}:"
        $volumeLines += "    driver: local"
    }
    $volumesBlock = $volumeLines -join "`n"

    $yaml = @"
# =============================================================================
# RecallHub Hardened Docker Compose Configuration
# Generated from config.json v$version - DO NOT EDIT DIRECTLY
# =============================================================================

services:
  # MongoDB - Internal Only
  mongodb:
    image: $($Config.docker.images.mongodb.name):$($Config.docker.images.mongodb.tag)
    container_name: $($Config.containers.mongodb)
    volumes:
      - mongodb_data:/data/db
      - mongodb_config:/data/configdb
    networks:
      - $($Config.docker.network.internal.name)
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mongosh", "--eval", "db.adminCommand('ping')"]
      interval: $($Config.healthChecks.mongodb.interval)
      timeout: $($Config.healthChecks.mongodb.timeout)
      retries: $($Config.healthChecks.mongodb.retries)
      start_period: $($Config.healthChecks.mongodb.startPeriod)
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # Ollama - Local LLM Service (Internal Only)
  ollama:
    image: $($Config.docker.images.ollama.name):$($Config.docker.images.ollama.tag)
    container_name: $($Config.containers.ollama)
    volumes:
      - ollama_models:/root/.ollama
    networks:
      - $($Config.docker.network.internal.name)
    restart: unless-stopped
    environment:
      - OLLAMA_HOST=0.0.0.0
    deploy:
      resources:
        reservations:
          devices:
            - driver: $($Config.resources.ollama.gpu.driver)
              count: $($Config.resources.ollama.gpu.count)
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:$($Config.ports.ollama)/api/tags"]
      interval: $($Config.healthChecks.ollama.interval)
      timeout: $($Config.healthChecks.ollama.timeout)
      retries: $($Config.healthChecks.ollama.retries)
      start_period: $($Config.healthChecks.ollama.startPeriod)
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # Backend API - Internal Only
  backend:
    image: $($Config.docker.images.backend.name):`${VERSION:-$version}
    container_name: $($Config.containers.backend)
    env_file:
      - ./.env
    volumes:
      - documents_data:/app/documents:ro
      - projects_data:/app/projects:ro
      - config_data:/app/config
      - logs_data:/app/logs
    networks:
      - $($Config.docker.network.internal.name)
    depends_on:
      mongodb:
        condition: service_healthy
      ollama:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:$($Config.ports.backend)/health"]
      interval: $($Config.healthChecks.backend.interval)
      timeout: $($Config.healthChecks.backend.timeout)
      retries: $($Config.healthChecks.backend.retries)
      start_period: $($Config.healthChecks.backend.startPeriod)
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # Frontend - Localhost Only Exposure
  frontend:
    image: $($Config.docker.images.frontend.name):`${VERSION:-$version}
    container_name: $($Config.containers.frontend)
    networks:
      - $($Config.docker.network.internal.name)
      - $($Config.docker.network.frontend.name)
    ports:
      - "$($Config.security.bindHost):$($Config.ports.frontend):80"
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:80/"]
      interval: $($Config.healthChecks.frontend.interval)
      timeout: $($Config.healthChecks.frontend.timeout)
      retries: $($Config.healthChecks.frontend.retries)
      start_period: $($Config.healthChecks.frontend.startPeriod)
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

networks:
  $($Config.docker.network.internal.name):
    driver: $($Config.docker.network.internal.driver)
    internal: true
    ipam:
      config:
        - subnet: $($Config.docker.network.internal.subnet)
  $($Config.docker.network.frontend.name):
    driver: $($Config.docker.network.frontend.driver)

volumes:
$volumesBlock
"@

    $outPath = Join-Path $OutputDir "docker-compose.hardened.yml"
    Write-Utf8NoBom -Path $outPath -Content $yaml
    Write-Host "Generated: $outPath"
}

# ---------------------------------------------------------------------------
# Generator: hardened.env
# ---------------------------------------------------------------------------
function New-HardenedEnv {
    param($Config, $OutputDir)

    $corsOrigins = ($Config.security.corsOrigins | ForEach-Object { $_ }) -join ","

    # Normalize booleans to lowercase strings
    $appDebug = "$($Config.security.appDebug)".ToLower()
    $exposeApiDocs = "$($Config.security.exposeApiDocs)".ToLower()
    $enableDebugRoutes = "$($Config.security.enableDebugRoutes)".ToLower()
    $sessionCookieSecure = "$($Config.security.sessionCookieSecure)".ToLower()
    $sessionCookieHttpOnly = "$($Config.security.sessionCookieHttpOnly)".ToLower()
    $enableCloudSources = "$($Config.features.enableCloudSources)".ToLower()
    $enableExternalWebhooks = "$($Config.features.enableExternalWebhooks)".ToLower()
    $enableTelemetry = "$($Config.features.enableTelemetry)".ToLower()
    $enableRemoteAccess = "$($Config.features.enableRemoteAccess)".ToLower()
    $backupEncryption = "$($Config.backup.encryptionEnabled)".ToLower()
    $autoUpdateCheck = "$($Config.update.autoUpdateCheck)".ToLower()

    $env = @"
# =============================================================================
# RecallHub Hardened Production Configuration
# Generated from config.json v$($Config.app.version) - DO NOT EDIT DIRECTLY
# =============================================================================

# Application Mode
APP_ENV=$($Config.security.appEnv)
APP_DEBUG=$appDebug
APP_LOG_LEVEL=$($Config.security.logLevel)

# Security Settings
EXPOSE_API_DOCS=$exposeApiDocs
ENABLE_DEBUG_ROUTES=$enableDebugRoutes
BIND_HOST=$($Config.security.bindHost)
CORS_ORIGINS=$corsOrigins
SESSION_COOKIE_SECURE=$sessionCookieSecure
SESSION_COOKIE_HTTPONLY=$sessionCookieHttpOnly
SESSION_COOKIE_SAMESITE=$($Config.security.sessionCookieSameSite)

# LLM Configuration (Local Ollama)
LLM_PROVIDER=$($Config.llm.provider)
LLM_BASE_URL=$($Config.llm.baseUrl)
LLM_MODEL=$($Config.llm.model)
LLM_TEMPERATURE=$($Config.llm.temperature)
LLM_MAX_TOKENS=$($Config.llm.maxTokens)

# Disable external LLM providers (hardened mode)
OPENAI_API_KEY=
OPENROUTER_API_KEY=
ANTHROPIC_API_KEY=

# Embedding Configuration (Local Ollama)
EMBEDDING_PROVIDER=$($Config.embedding.provider)
EMBEDDING_MODEL=$($Config.embedding.model)
EMBEDDING_DIMENSION=$($Config.embedding.dimension)
EMBEDDING_BASE_URL=$($Config.embedding.baseUrl)

# MongoDB Configuration
MONGODB_URI=$($Config.mongodb.uri)
MONGODB_DATABASE=$($Config.mongodb.database)

# Ports
BACKEND_PORT=$($Config.ports.backend)
FRONTEND_PORT=$($Config.ports.frontend)
OLLAMA_PORT=$($Config.ports.ollama)

# Feature Flags
ENABLE_CLOUD_SOURCES=$enableCloudSources
ENABLE_EXTERNAL_WEBHOOKS=$enableExternalWebhooks
ENABLE_TELEMETRY=$enableTelemetry
ENABLE_REMOTE_ACCESS=$enableRemoteAccess

# Backup Configuration
BACKUP_ENCRYPTION_ENABLED=$backupEncryption
BACKUP_RETENTION_DAYS=$($Config.backup.retentionDays)
BACKUP_MAX_COUNT=$($Config.backup.maxCount)

# Update Configuration
AUTO_UPDATE_CHECK=$autoUpdateCheck
UPDATE_CHANNEL=$($Config.update.updateChannel)
"@

    $outPath = Join-Path $OutputDir "hardened.env"
    Write-Utf8NoBom -Path $outPath -Content $env
    Write-Host "Generated: $outPath"
}

# ---------------------------------------------------------------------------
# Generator: setup-defines.iss
# ---------------------------------------------------------------------------
function New-SetupDefines {
    param($Config, $OutputDir)

    $iss = @"
; =============================================================================
; RecallHub Installer Defines
; Generated from config.json v$($Config.app.version) - DO NOT EDIT DIRECTLY
; =============================================================================
#define MyAppName "$($Config.app.name)"
#define MyAppVersion "$($Config.app.version)"
#define MyAppPublisher "$($Config.app.publisher)"
#define MyAppURL "$($Config.app.url)"
#define MyAppExeName "$($Config.app.exeName)"
#define MyAppId "$($Config.app.appId)"
"@

    $outPath = Join-Path $OutputDir "setup-defines.iss"
    Write-Utf8NoBom -Path $outPath -Content $iss
    Write-Host "Generated: $outPath"
}

# ---------------------------------------------------------------------------
# Generator: nginx.hardened.conf
# ---------------------------------------------------------------------------
function New-NginxConfig {
    param($Config, $OutputDir)

    $nginx = @"
# =============================================================================
# RecallHub Hardened Nginx Configuration
# Generated from config.json v$($Config.app.version) - DO NOT EDIT DIRECTLY
# =============================================================================

upstream backend {
    server backend:$($Config.ports.backend);
}

server {
    listen 80;
    server_name localhost;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Frontend static files
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files `$uri `$uri/ /index.html;
    }

    # API proxy
    location /api/ {
        proxy_pass http://backend;
        proxy_set_header Host `$host;
        proxy_set_header X-Real-IP `$remote_addr;
        proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto `$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Health check endpoint
    location /health {
        proxy_pass http://backend/health;
        access_log off;
    }

    # Deny access to hidden files
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
"@

    $outPath = Join-Path $OutputDir "nginx.hardened.conf"
    Write-Utf8NoBom -Path $outPath -Content $nginx
    Write-Host "Generated: $outPath"
}

# ---------------------------------------------------------------------------
# Generator: models-manifest.json
# ---------------------------------------------------------------------------
function New-ModelsManifest {
    param($Config, $OutputDir)

    $manifest = @{
        generatedAt = (Get-Date).ToString("o")
        configVersion = $Config.app.version
        models = $Config.models.ollama
    }

    $outPath = Join-Path $OutputDir "models-manifest.json"
    $json = $manifest | ConvertTo-Json -Depth 5
    Write-Utf8NoBom -Path $outPath -Content $json
    Write-Host "Generated: $outPath"
}

# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
Write-Host "Generating build configuration from: $ConfigPath"
Write-Host "Output directory: $OutputDir"
Write-Host ""

New-DockerCompose -Config $config -OutputDir $OutputDir
New-HardenedEnv -Config $config -OutputDir $OutputDir
New-SetupDefines -Config $config -OutputDir $OutputDir
New-NginxConfig -Config $config -OutputDir $OutputDir
New-ModelsManifest -Config $config -OutputDir $OutputDir

Write-Host ""
Write-Host "All configuration files generated successfully." -ForegroundColor Green
Write-Host "Config version: $($config.app.version)"
