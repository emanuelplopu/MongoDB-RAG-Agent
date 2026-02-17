# RecallHub - Cloudflare Tunnel Setup Script
# This script sets up a Cloudflare Tunnel to expose your local app on recallhub.app
#
# Authentication Methods (in order of preference):
#   1. API Token via environment variable: CLOUDFLARE_API_TOKEN
#   2. API Token via .env file in project root
#   3. Tunnel Token via environment variable: CLOUDFLARE_TUNNEL_TOKEN (for run-only mode)
#   4. Interactive browser login (fallback)
#
# Required API Token Permissions:
#   - Account > Cloudflare Tunnel > Edit
#   - Zone > DNS > Edit (for recallhub.app zone)
#   - Zone > Zone > Read
#
# Optional Environment Variables:
#   - CLOUDFLARE_ACCOUNT_ID: Manually specify account ID (if auto-detection fails)
#
# Usage:
#   .\setup-cloudflare-tunnel.ps1                    # Interactive setup
#   .\setup-cloudflare-tunnel.ps1 -Run               # Run existing tunnel
#   .\setup-cloudflare-tunnel.ps1 -SetupToken        # Configure API token
#   .\setup-cloudflare-tunnel.ps1 -Install           # Install as Windows service
#   .\setup-cloudflare-tunnel.ps1 -Uninstall         # Remove Windows service
#   .\setup-cloudflare-tunnel.ps1 -Status            # Check tunnel status

param(
    [switch]$Run,
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$SetupToken,
    [switch]$Status,
    [switch]$Help,
    [string]$Token
)

# Configuration
$DOMAIN = "recallhub.app"
$TUNNEL_NAME = "recallhub-tunnel"
$FRONTEND_PORT = 11080
$BACKEND_PORT = 11000
$CONFIG_DIR = "$env:USERPROFILE\.cloudflared"
$CONFIG_FILE = "$CONFIG_DIR\config.yml"
$TOKEN_FILE = "$CONFIG_DIR\.cf_token"
$PROJECT_ROOT = $PSScriptRoot
$ENV_FILE = Join-Path $PROJECT_ROOT ".env"

# ============================================
# Helper Functions
# ============================================

function Write-Header {
    param([string]$Message)
    Write-Host ""
    Write-Host "=== $Message ===" -ForegroundColor Cyan
    Write-Host ""
}

function Write-Success {
    param([string]$Message)
    Write-Host "[OK] $Message" -ForegroundColor Green
}

function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] $Message" -ForegroundColor Yellow
}

function Write-ErrorMsg {
    param([string]$Message)
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Write-Step {
    param([int]$Number, [string]$Message)
    Write-Host ""
    Write-Host "Step $Number : $Message" -ForegroundColor Magenta
    Write-Host ("-" * 50) -ForegroundColor DarkGray
}

# ============================================
# Token Management
# ============================================

function Get-StoredToken {
    # Priority 1: Environment variable
    if ($env:CLOUDFLARE_API_TOKEN) {
        return @{
            Token = $env:CLOUDFLARE_API_TOKEN
            Source = "Environment variable (CLOUDFLARE_API_TOKEN)"
            Type = "api"
        }
    }
    
    # Priority 2: Tunnel token from env (for run-only)
    if ($env:CLOUDFLARE_TUNNEL_TOKEN) {
        return @{
            Token = $env:CLOUDFLARE_TUNNEL_TOKEN
            Source = "Environment variable (CLOUDFLARE_TUNNEL_TOKEN)"
            Type = "tunnel"
        }
    }
    
    # Priority 3: .env file in project root
    if (Test-Path $ENV_FILE) {
        $envContent = Get-Content $ENV_FILE -ErrorAction SilentlyContinue
        foreach ($line in $envContent) {
            if ($line -match "^CLOUDFLARE_API_TOKEN=(.+)$") {
                return @{
                    Token = $matches[1].Trim('"', "'", ' ')
                    Source = ".env file (CLOUDFLARE_API_TOKEN)"
                    Type = "api"
                }
            }
            if ($line -match "^CLOUDFLARE_TUNNEL_TOKEN=(.+)$") {
                return @{
                    Token = $matches[1].Trim('"', "'", ' ')
                    Source = ".env file (CLOUDFLARE_TUNNEL_TOKEN)"
                    Type = "tunnel"
                }
            }
        }
    }
    
    # Priority 4: Stored token file
    if (Test-Path $TOKEN_FILE) {
        $storedToken = Get-Content $TOKEN_FILE -Raw -ErrorAction SilentlyContinue
        if ($storedToken) {
            return @{
                Token = $storedToken.Trim()
                Source = "Stored token file"
                Type = "api"
            }
        }
    }
    
    return $null
}

function Save-Token {
    param([string]$ApiToken)
    
    # Ensure config directory exists
    if (-not (Test-Path $CONFIG_DIR)) {
        New-Item -ItemType Directory -Path $CONFIG_DIR -Force | Out-Null
    }
    
    # Save to token file (encrypted on Windows)
    $ApiToken | Out-File -FilePath $TOKEN_FILE -Encoding utf8 -Force -NoNewline
    
    # Set restrictive permissions
    $acl = Get-Acl $TOKEN_FILE
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        [System.Security.Principal.WindowsIdentity]::GetCurrent().Name,
        "FullControl",
        "Allow"
    )
    $acl.SetAccessRule($rule)
    Set-Acl -Path $TOKEN_FILE -AclObject $acl
    
    Write-Success "Token saved securely to $TOKEN_FILE"
    
    # Also add to .env if it exists
    if (Test-Path $ENV_FILE) {
        $envContent = Get-Content $ENV_FILE -Raw
        if ($envContent -notmatch "CLOUDFLARE_API_TOKEN") {
            Add-Content -Path $ENV_FILE -Value "`nCLOUDFLARE_API_TOKEN=$ApiToken"
            Write-Success "Token also added to .env file"
        }
    }
}

function Set-CloudflareEnv {
    param([string]$ApiToken)
    
    # Set environment variable for cloudflared to use
    $env:CLOUDFLARE_API_TOKEN = $ApiToken
}

function Show-TokenSetup {
    Write-Header "Cloudflare API Token Setup"
    
    Write-Host "To use token-based authentication, you need to create an API Token" -ForegroundColor White
    Write-Host "in your Cloudflare dashboard with the following permissions:" -ForegroundColor White
    Write-Host ""
    Write-Host "Required Permissions:" -ForegroundColor Cyan
    Write-Host "  1. Account > Cloudflare Tunnel > Edit" -ForegroundColor Yellow
    Write-Host "  2. Zone > DNS > Edit" -ForegroundColor Yellow
    Write-Host "  3. Zone > Zone > Read" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Zone Resources:" -ForegroundColor Cyan
    Write-Host "  Include > Specific zone > $DOMAIN" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Account Resources:" -ForegroundColor Cyan
    Write-Host "  Include > Your Account" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Steps to create the token:" -ForegroundColor White
    Write-Host "  1. Go to: https://dash.cloudflare.com/profile/api-tokens" -ForegroundColor Gray
    Write-Host "  2. Click 'Create Token'" -ForegroundColor Gray
    Write-Host "  3. Select 'Create Custom Token'" -ForegroundColor Gray
    Write-Host "  4. Add the permissions listed above" -ForegroundColor Gray
    Write-Host "  5. Create and copy the token" -ForegroundColor Gray
    Write-Host ""
    
    $inputToken = Read-Host "Paste your API Token here (or press Enter to skip)"
    
    if ($inputToken) {
        # Validate token format (basic check)
        if ($inputToken.Length -lt 20) {
            Write-ErrorMsg "Token seems too short. Please check and try again."
            return $false
        }
        
        # Test the token
        Write-Info "Validating token..."
        Set-CloudflareEnv -ApiToken $inputToken
        
        $testResult = Test-ApiToken -ApiToken $inputToken
        if ($testResult) {
            Save-Token -ApiToken $inputToken
            Write-Success "Token validated and saved successfully!"
            return $true
        } else {
            Write-ErrorMsg "Token validation failed. Please check permissions and try again."
            return $false
        }
    }
    
    return $false
}

function Test-ApiToken {
    param([string]$ApiToken)
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        $response = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/user/tokens/verify" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        return $response.success -eq $true
    }
    catch {
        Write-ErrorMsg "Token verification failed: $($_.Exception.Message)"
        return $false
    }
}

function Get-AccountId {
    param([string]$ApiToken)
    
    $headers = @{
        "Authorization" = "Bearer $ApiToken"
        "Content-Type" = "application/json"
    }
    
    # Method 1: Check environment variable for manually specified account ID
    if ($env:CLOUDFLARE_ACCOUNT_ID) {
        Write-Info "Using account ID from CLOUDFLARE_ACCOUNT_ID environment variable"
        return $env:CLOUDFLARE_ACCOUNT_ID
    }
    
    # Method 2: Try to get account ID from the zone (most reliable with zone-scoped tokens)
    try {
        $zoneResponse = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$DOMAIN" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($zoneResponse.success -and $zoneResponse.result.Count -gt 0) {
            $accountId = $zoneResponse.result[0].account.id
            if ($accountId) {
                Write-Info "Got account ID from zone information"
                return $accountId
            }
        }
    }
    catch {
        Write-Info "Could not get account from zone: $($_.Exception.Message)"
    }
    
    # Method 3: Try the accounts endpoint (requires Account Settings Read permission)
    try {
        $response = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/accounts" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($response.success -and $response.result.Count -gt 0) {
            return $response.result[0].id
        }
    }
    catch {
        Write-Info "Could not list accounts: $($_.Exception.Message)"
    }
    
    # Method 4: Try to get from token verification (some tokens include account info)
    try {
        $verifyResponse = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/user/tokens/verify" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($verifyResponse.success -and $verifyResponse.result.policies) {
            foreach ($policy in $verifyResponse.result.policies) {
                if ($policy.permission_groups) {
                    foreach ($pg in $policy.permission_groups) {
                        if ($pg.resources -and $pg.resources."com.cloudflare.api.account.*") {
                            # Extract account ID from resource string
                            $resourceKeys = $pg.resources.PSObject.Properties.Name | Where-Object { $_ -match "com\.cloudflare\.api\.account\.([a-f0-9]+)" }
                            if ($resourceKeys) {
                                $resourceKeys[0] -match "com\.cloudflare\.api\.account\.([a-f0-9]+)" | Out-Null
                                return $matches[1]
                            }
                        }
                    }
                }
            }
        }
    }
    catch {
        # Token verify doesn't always include account info, that's OK
    }
    
    return $null
}

function Get-ZoneId {
    param([string]$ApiToken, [string]$Domain)
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        $response = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$Domain" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($response.success -and $response.result.Count -gt 0) {
            return $response.result[0].id
        }
    }
    catch {
        Write-ErrorMsg "Failed to get zone ID: $($_.Exception.Message)"
    }
    
    return $null
}

# ============================================
# Cloudflared Installation
# ============================================

function Test-CloudflaredInstalled {
    $cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    return $null -ne $cloudflared
}

function Install-Cloudflared {
    Write-Header "Installing cloudflared"
    
    # Check if winget is available
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Info "Installing via winget..."
        winget install --id Cloudflare.cloudflared -e --accept-source-agreements --accept-package-agreements
        
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        
        if (Test-CloudflaredInstalled) {
            Write-Success "cloudflared installed successfully!"
            return $true
        }
    }
    
    # Fallback: Download directly
    Write-Info "Downloading cloudflared directly..."
    $downloadUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    $installPath = "C:\Program Files\cloudflared"
    $exePath = "$installPath\cloudflared.exe"
    
    # Create directory
    if (-not (Test-Path $installPath)) {
        New-Item -ItemType Directory -Path $installPath -Force | Out-Null
    }
    
    # Download
    Write-Info "Downloading from $downloadUrl..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $exePath -UseBasicParsing
    
    # Add to PATH
    $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($currentPath -notlike "*$installPath*") {
        [Environment]::SetEnvironmentVariable("Path", "$currentPath;$installPath", "User")
        $env:Path = "$env:Path;$installPath"
    }
    
    if (Test-Path $exePath) {
        Write-Success "cloudflared downloaded to $exePath"
        Write-Info "Please restart your terminal for PATH changes to take effect"
        return $true
    }
    
    Write-ErrorMsg "Failed to install cloudflared"
    return $false
}

# ============================================
# Tunnel Management (API-based)
# ============================================

function Get-TunnelByName {
    param([string]$ApiToken, [string]$AccountId, [string]$TunnelName)
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        $response = Invoke-RestMethod `
            -Uri "https://api.cloudflare.com/client/v4/accounts/$AccountId/cfd_tunnel?name=$TunnelName" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($response.success -and $response.result.Count -gt 0) {
            return $response.result | Where-Object { $_.name -eq $TunnelName -and -not $_.deleted_at }
        }
    }
    catch {
        Write-ErrorMsg "Failed to get tunnel: $($_.Exception.Message)"
    }
    
    return $null
}

function New-CloudflareTunnelApi {
    param([string]$ApiToken, [string]$AccountId, [string]$TunnelName)
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        # Generate a random tunnel secret
        $secretBytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($secretBytes)
        $tunnelSecret = [Convert]::ToBase64String($secretBytes)
        
        $body = @{
            name = $TunnelName
            tunnel_secret = $tunnelSecret
            config_src = "local"
        } | ConvertTo-Json
        
        $response = Invoke-RestMethod `
            -Uri "https://api.cloudflare.com/client/v4/accounts/$AccountId/cfd_tunnel" `
            -Headers $headers -Method POST -Body $body -ErrorAction Stop
        
        if ($response.success) {
            $tunnel = $response.result
            
            # Save credentials file (UTF-8 without BOM for cloudflared compatibility)
            $credentials = @{
                AccountTag = $AccountId
                TunnelID = $tunnel.id
                TunnelName = $tunnel.name
                TunnelSecret = $tunnelSecret
            } | ConvertTo-Json
            
            $credPath = "$CONFIG_DIR\$($tunnel.id).json"
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($credPath, $credentials, $utf8NoBom)
            
            return $tunnel
        }
    }
    catch {
        Write-ErrorMsg "Failed to create tunnel: $($_.Exception.Message)"
    }
    
    return $null
}

function Get-TunnelToken {
    param([string]$ApiToken, [string]$AccountId, [string]$TunnelId)
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        $response = Invoke-RestMethod `
            -Uri "https://api.cloudflare.com/client/v4/accounts/$AccountId/cfd_tunnel/$TunnelId/token" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        if ($response.success) {
            return $response.result
        }
    }
    catch {
        # Token endpoint might not be available, that's OK
    }
    
    return $null
}

function Set-DnsRecord {
    param(
        [string]$ApiToken,
        [string]$ZoneId,
        [string]$Hostname,
        [string]$TunnelId
    )
    
    try {
        $headers = @{
            "Authorization" = "Bearer $ApiToken"
            "Content-Type" = "application/json"
        }
        
        # Check if record exists
        $existingRecords = Invoke-RestMethod `
            -Uri "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records?name=$Hostname&type=CNAME" `
            -Headers $headers -Method GET -ErrorAction Stop
        
        $tunnelCname = "$TunnelId.cfargotunnel.com"
        
        $body = @{
            type = "CNAME"
            name = $Hostname
            content = $tunnelCname
            proxied = $true
            ttl = 1  # Auto TTL
        } | ConvertTo-Json
        
        if ($existingRecords.result.Count -gt 0) {
            # Update existing record
            $recordId = $existingRecords.result[0].id
            $response = Invoke-RestMethod `
                -Uri "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records/$recordId" `
                -Headers $headers -Method PUT -Body $body -ErrorAction Stop
        } else {
            # Check for conflicting A/AAAA records (especially for root domain)
            $conflictingRecords = Invoke-RestMethod `
                -Uri "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records?name=$Hostname" `
                -Headers $headers -Method GET -ErrorAction Stop
            
            if ($conflictingRecords.result.Count -gt 0) {
                $existingTypes = ($conflictingRecords.result | ForEach-Object { $_.type }) -join ", "
                Write-Info "Found existing $existingTypes record(s) for $Hostname"
                
                # Delete conflicting records before creating CNAME
                foreach ($record in $conflictingRecords.result) {
                    Write-Info "Deleting existing $($record.type) record..."
                    Invoke-RestMethod `
                        -Uri "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records/$($record.id)" `
                        -Headers $headers -Method DELETE -ErrorAction SilentlyContinue | Out-Null
                }
            }
            
            # Create new record
            $response = Invoke-RestMethod `
                -Uri "https://api.cloudflare.com/client/v4/zones/$ZoneId/dns_records" `
                -Headers $headers -Method POST -Body $body -ErrorAction Stop
        }
        
        return $response.success
    }
    catch {
        $errorDetails = ""
        if ($_.Exception.Response) {
            try {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $responseBody = $reader.ReadToEnd()
                $errorJson = $responseBody | ConvertFrom-Json
                if ($errorJson.errors) {
                    $errorDetails = " - " + ($errorJson.errors | ForEach-Object { $_.message }) -join "; "
                }
            } catch {}
        }
        Write-ErrorMsg "Failed to set DNS record for $Hostname$errorDetails"
        return $false
    }
}

# ============================================
# Configuration
# ============================================

function New-TunnelConfig {
    param([string]$TunnelId, [string]$CredentialsPath)
    
    Write-Header "Creating Tunnel Configuration"
    
    # Ensure config directory exists
    if (-not (Test-Path $CONFIG_DIR)) {
        New-Item -ItemType Directory -Path $CONFIG_DIR -Force | Out-Null
    }
    
    # Use provided credentials path or find it
    if (-not $CredentialsPath) {
        $credFile = Get-ChildItem -Path $CONFIG_DIR -Filter "$TunnelId.json" -ErrorAction SilentlyContinue
        $CredentialsPath = if ($credFile) { $credFile.FullName } else { "$CONFIG_DIR\$TunnelId.json" }
    }
    
    $config = @"
# RecallHub Cloudflare Tunnel Configuration
# Auto-generated by setup-cloudflare-tunnel.ps1
# Domain: $DOMAIN

tunnel: $TunnelId
credentials-file: $CredentialsPath

# Metrics for monitoring (optional)
metrics: localhost:60123

ingress:
  # Backend API - api.recallhub.app
  - hostname: api.$DOMAIN
    service: http://localhost:$BACKEND_PORT
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      keepAliveTimeout: 90s

  # Frontend - www.recallhub.app
  - hostname: www.$DOMAIN
    service: http://localhost:$FRONTEND_PORT
    originRequest:
      noTLSVerify: true

  # Frontend - recallhub.app (root domain)
  - hostname: $DOMAIN
    service: http://localhost:$FRONTEND_PORT
    originRequest:
      noTLSVerify: true

  # Catch-all rule (required by cloudflared)
  - service: http_status:404
"@

    $config | Out-File -FilePath $CONFIG_FILE -Encoding utf8 -Force
    Write-Success "Configuration saved to $CONFIG_FILE"
    
    return $true
}

# ============================================
# Main Operations
# ============================================

function Start-FullSetup {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "  RecallHub Cloudflare Tunnel Setup    " -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "This will expose your local app on:" -ForegroundColor White
    Write-Host "  - https://$DOMAIN (Frontend)" -ForegroundColor Green
    Write-Host "  - https://www.$DOMAIN (Frontend)" -ForegroundColor Green
    Write-Host "  - https://api.$DOMAIN (Backend API)" -ForegroundColor Green
    Write-Host ""
    
    # Step 1: Check/Install cloudflared
    Write-Step 1 "Checking cloudflared installation"
    if (-not (Test-CloudflaredInstalled)) {
        Write-Info "cloudflared not found. Installing..."
        if (-not (Install-Cloudflared)) {
            return
        }
    } else {
        $version = cloudflared --version 2>&1
        Write-Success "cloudflared is installed: $version"
    }
    
    # Step 2: Get or setup API token
    Write-Step 2 "Checking API Token"
    $tokenInfo = Get-StoredToken
    
    if ($tokenInfo) {
        Write-Success "Found token from: $($tokenInfo.Source)"
        $apiToken = $tokenInfo.Token
        
        # Validate the token
        if (-not (Test-ApiToken -ApiToken $apiToken)) {
            Write-ErrorMsg "Stored token is invalid. Please setup a new token."
            if (-not (Show-TokenSetup)) {
                Write-Info "Falling back to browser-based authentication..."
                cloudflared tunnel login
                $apiToken = $null
            } else {
                $tokenInfo = Get-StoredToken
                $apiToken = $tokenInfo.Token
            }
        }
    } else {
        Write-Info "No API token found."
        if (-not (Show-TokenSetup)) {
            Write-Info "Falling back to browser-based authentication..."
            cloudflared tunnel login
            $apiToken = $null
        } else {
            $tokenInfo = Get-StoredToken
            $apiToken = $tokenInfo.Token
        }
    }
    
    # Step 3: Create or get tunnel
    Write-Step 3 "Setting up Tunnel"
    
    $tunnelId = $null
    $credentialsPath = $null
    
    if ($apiToken) {
        # Use API for tunnel management
        Set-CloudflareEnv -ApiToken $apiToken
        
        $accountId = Get-AccountId -ApiToken $apiToken
        if (-not $accountId) {
            Write-ErrorMsg "Could not get Cloudflare account ID. Check token permissions."
            return
        }
        Write-Success "Account ID: $accountId"
        
        # Check for existing tunnel
        $existingTunnel = Get-TunnelByName -ApiToken $apiToken -AccountId $accountId -TunnelName $TUNNEL_NAME
        
        if ($existingTunnel) {
            Write-Success "Found existing tunnel: $TUNNEL_NAME (ID: $($existingTunnel.id))"
            $tunnelId = $existingTunnel.id
            
            # Check if credentials file exists
            $credentialsPath = "$CONFIG_DIR\$tunnelId.json"
            if (-not (Test-Path $credentialsPath)) {
                Write-Info "Credentials file not found. Getting tunnel token..."
                $tunnelToken = Get-TunnelToken -ApiToken $apiToken -AccountId $accountId -TunnelId $tunnelId
                if ($tunnelToken) {
                    # Save tunnel token for later use
                    $env:CLOUDFLARE_TUNNEL_TOKEN = $tunnelToken
                    Write-Info "Using tunnel token for authentication"
                }
            }
        } else {
            Write-Info "Creating new tunnel: $TUNNEL_NAME"
            $newTunnel = New-CloudflareTunnelApi -ApiToken $apiToken -AccountId $accountId -TunnelName $TUNNEL_NAME
            if ($newTunnel) {
                $tunnelId = $newTunnel.id
                $credentialsPath = "$CONFIG_DIR\$tunnelId.json"
                Write-Success "Tunnel created: $TUNNEL_NAME (ID: $tunnelId)"
            } else {
                Write-ErrorMsg "Failed to create tunnel via API"
                return
            }
        }
        
        # Step 4: Setup DNS records
        Write-Step 4 "Configuring DNS Records"
        
        $zoneId = Get-ZoneId -ApiToken $apiToken -Domain $DOMAIN
        if ($zoneId) {
            Write-Success "Zone ID: $zoneId"
            
            $dnsRecords = @($DOMAIN, "www.$DOMAIN", "api.$DOMAIN")
            foreach ($record in $dnsRecords) {
                Write-Info "Setting DNS record: $record"
                if (Set-DnsRecord -ApiToken $apiToken -ZoneId $zoneId -Hostname $record -TunnelId $tunnelId) {
                    Write-Success "DNS record configured: $record -> $tunnelId.cfargotunnel.com"
                }
            }
        } else {
            Write-ErrorMsg "Could not find zone for $DOMAIN. Ensure domain is added to Cloudflare."
            Write-Info "You may need to manually configure DNS records."
        }
    } else {
        # Fallback: Use cloudflared CLI commands
        Write-Info "Using cloudflared CLI for tunnel management..."
        
        # Check for existing tunnel
        $tunnels = cloudflared tunnel list --output json 2>$null | ConvertFrom-Json
        $existingTunnel = $tunnels | Where-Object { $_.name -eq $TUNNEL_NAME }
        
        if ($existingTunnel) {
            $tunnelId = $existingTunnel.id
            Write-Success "Found existing tunnel: $TUNNEL_NAME (ID: $tunnelId)"
        } else {
            Write-Info "Creating tunnel..."
            cloudflared tunnel create $TUNNEL_NAME
            $tunnels = cloudflared tunnel list --output json 2>$null | ConvertFrom-Json
            $existingTunnel = $tunnels | Where-Object { $_.name -eq $TUNNEL_NAME }
            if ($existingTunnel) {
                $tunnelId = $existingTunnel.id
                Write-Success "Tunnel created: $TUNNEL_NAME (ID: $tunnelId)"
            }
        }
        
        # Setup DNS routes via CLI
        if ($tunnelId) {
            Write-Step 4 "Configuring DNS Routes"
            foreach ($hostname in @($DOMAIN, "www.$DOMAIN", "api.$DOMAIN")) {
                Write-Info "Setting DNS route: $hostname"
                cloudflared tunnel route dns --overwrite-dns $TUNNEL_NAME $hostname 2>&1 | Out-Null
                Write-Success "DNS route configured: $hostname"
            }
        }
    }
    
    # Step 5: Create configuration file
    Write-Step 5 "Creating Configuration File"
    if ($tunnelId) {
        New-TunnelConfig -TunnelId $tunnelId -CredentialsPath $credentialsPath
    } else {
        Write-ErrorMsg "No tunnel ID available. Setup failed."
        return
    }
    
    # Done!
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  Setup Complete!                      " -ForegroundColor Green  
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Tunnel ID: $tunnelId" -ForegroundColor White
    Write-Host "Config:    $CONFIG_FILE" -ForegroundColor White
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1. Start your Docker services:" -ForegroundColor White
    Write-Host "   docker compose up -d" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "2. Run the tunnel:" -ForegroundColor White
    Write-Host "   .\setup-cloudflare-tunnel.ps1 -Run" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "3. (Optional) Install as Windows service:" -ForegroundColor White
    Write-Host "   .\setup-cloudflare-tunnel.ps1 -Install" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Your app will be available at:" -ForegroundColor Cyan
    Write-Host "  https://$DOMAIN" -ForegroundColor Green
    Write-Host "  https://www.$DOMAIN" -ForegroundColor Green
    Write-Host "  https://api.$DOMAIN" -ForegroundColor Green
    Write-Host ""
}

function Start-Tunnel {
    Write-Header "Starting Cloudflare Tunnel"
    
    # Check for tunnel token first (simplest method)
    $tokenInfo = Get-StoredToken
    
    if ($tokenInfo -and $tokenInfo.Type -eq "tunnel") {
        Write-Info "Running with tunnel token..."
        Write-Host ""
        Write-Host "Services will be available at:" -ForegroundColor Cyan
        Write-Host "  Frontend: https://$DOMAIN" -ForegroundColor White
        Write-Host "  Frontend: https://www.$DOMAIN" -ForegroundColor White
        Write-Host "  Backend:  https://api.$DOMAIN" -ForegroundColor White
        Write-Host ""
        Write-Host "Press Ctrl+C to stop the tunnel" -ForegroundColor Yellow
        Write-Host ""
        
        cloudflared tunnel run --token $tokenInfo.Token
        return
    }
    
    # Check for config file
    if (-not (Test-Path $CONFIG_FILE)) {
        Write-ErrorMsg "Configuration file not found at $CONFIG_FILE"
        Write-Info "Run setup first: .\setup-cloudflare-tunnel.ps1"
        return
    }
    
    # Set API token if available
    if ($tokenInfo -and $tokenInfo.Type -eq "api") {
        Set-CloudflareEnv -ApiToken $tokenInfo.Token
    }
    
    Write-Info "Starting tunnel with configuration: $CONFIG_FILE"
    Write-Host ""
    Write-Host "Services will be available at:" -ForegroundColor Cyan
    Write-Host "  Frontend: https://$DOMAIN" -ForegroundColor White
    Write-Host "  Frontend: https://www.$DOMAIN" -ForegroundColor White
    Write-Host "  Backend:  https://api.$DOMAIN" -ForegroundColor White
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the tunnel" -ForegroundColor Yellow
    Write-Host ""
    
    cloudflared tunnel --config $CONFIG_FILE run
}

function Show-Status {
    Write-Header "Cloudflare Tunnel Status"
    
    # Check cloudflared
    if (Test-CloudflaredInstalled) {
        $version = cloudflared --version 2>&1
        Write-Success "cloudflared: $version"
    } else {
        Write-ErrorMsg "cloudflared: Not installed"
    }
    
    # Check token
    $tokenInfo = Get-StoredToken
    if ($tokenInfo) {
        Write-Success "Token: Found ($($tokenInfo.Source))"
        Write-Info "Token type: $($tokenInfo.Type)"
        
        if ($tokenInfo.Type -eq "api") {
            if (Test-ApiToken -ApiToken $tokenInfo.Token) {
                Write-Success "Token validation: Valid"
            } else {
                Write-ErrorMsg "Token validation: Invalid"
            }
        }
    } else {
        Write-Info "Token: Not configured"
    }
    
    # Check config
    if (Test-Path $CONFIG_FILE) {
        Write-Success "Config file: $CONFIG_FILE"
        
        # Extract tunnel ID from config
        $configContent = Get-Content $CONFIG_FILE -Raw
        if ($configContent -match "tunnel:\s*([a-f0-9-]+)") {
            Write-Info "Tunnel ID: $($matches[1])"
        }
    } else {
        Write-Info "Config file: Not found"
    }
    
    # Check service
    $service = Get-Service cloudflared -ErrorAction SilentlyContinue
    if ($service) {
        Write-Success "Windows Service: $($service.Status)"
    } else {
        Write-Info "Windows Service: Not installed"
    }
    
    Write-Host ""
    Write-Host "Configured endpoints:" -ForegroundColor Cyan
    Write-Host "  https://$DOMAIN -> localhost:$FRONTEND_PORT" -ForegroundColor White
    Write-Host "  https://www.$DOMAIN -> localhost:$FRONTEND_PORT" -ForegroundColor White
    Write-Host "  https://api.$DOMAIN -> localhost:$BACKEND_PORT" -ForegroundColor White
}

function Install-TunnelService {
    Write-Header "Installing as Windows Service"
    
    if (-not (Test-Path $CONFIG_FILE)) {
        Write-ErrorMsg "Configuration file not found. Run setup first."
        return
    }
    
    # Check if running as admin
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    
    if (-not $isAdmin) {
        Write-ErrorMsg "Please run this script as Administrator to install the service"
        Write-Info "Right-click PowerShell and select 'Run as Administrator'"
        return
    }
    
    Write-Info "Installing cloudflared as a Windows service..."
    
    # Copy config to system location for service
    $systemConfigDir = "C:\Windows\System32\config\systemprofile\.cloudflared"
    if (-not (Test-Path $systemConfigDir)) {
        New-Item -ItemType Directory -Path $systemConfigDir -Force | Out-Null
    }
    
    Copy-Item -Path $CONFIG_FILE -Destination "$systemConfigDir\config.yml" -Force
    
    # Copy credentials file
    $configContent = Get-Content $CONFIG_FILE -Raw
    if ($configContent -match "credentials-file:\s*(.+)") {
        $credFile = $matches[1].Trim()
        if (Test-Path $credFile) {
            Copy-Item -Path $credFile -Destination $systemConfigDir -Force
            Write-Success "Credentials copied to system profile"
        }
    }
    
    cloudflared service install
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Service installed successfully!"
        Write-Info "The tunnel will now start automatically with Windows"
        Write-Host ""
        Write-Host "Manage the service with:" -ForegroundColor Cyan
        Write-Host "  Start:   Start-Service cloudflared" -ForegroundColor White
        Write-Host "  Stop:    Stop-Service cloudflared" -ForegroundColor White
        Write-Host "  Status:  Get-Service cloudflared" -ForegroundColor White
    }
}

function Uninstall-TunnelService {
    Write-Header "Removing Windows Service"
    
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    
    if (-not $isAdmin) {
        Write-ErrorMsg "Please run this script as Administrator to uninstall the service"
        return
    }
    
    cloudflared service uninstall
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Service removed successfully!"
    }
}

function Show-Help {
    Write-Host @"
RecallHub Cloudflare Tunnel Setup
=================================

This script sets up a Cloudflare Tunnel to expose your local RecallHub app
on the domain $DOMAIN using API Token authentication.

Services exposed:
  - https://$DOMAIN         -> localhost:$FRONTEND_PORT (Frontend)
  - https://www.$DOMAIN     -> localhost:$FRONTEND_PORT (Frontend)
  - https://api.$DOMAIN     -> localhost:$BACKEND_PORT (Backend API)

Usage:
  .\setup-cloudflare-tunnel.ps1                 First-time setup (interactive)
  .\setup-cloudflare-tunnel.ps1 -Run            Run the tunnel
  .\setup-cloudflare-tunnel.ps1 -SetupToken     Configure/update API token
  .\setup-cloudflare-tunnel.ps1 -Status         Show tunnel status
  .\setup-cloudflare-tunnel.ps1 -Install        Install as Windows service (requires admin)
  .\setup-cloudflare-tunnel.ps1 -Uninstall      Remove Windows service (requires admin)
  .\setup-cloudflare-tunnel.ps1 -Help           Show this help

Authentication Methods (checked in order):
  1. CLOUDFLARE_API_TOKEN environment variable
  2. CLOUDFLARE_TUNNEL_TOKEN environment variable (run-only)
  3. Token in .env file
  4. Stored token in ~/.cloudflared/.cf_token
  5. Browser-based login (fallback)

Required API Token Permissions:
  - Account > Cloudflare Tunnel > Edit
  - Zone > DNS > Edit (for $DOMAIN)
  - Zone > Zone > Read

Optional Environment Variables:
  - CLOUDFLARE_ACCOUNT_ID   Manually specify account ID (if auto-detection fails)

Create token at: https://dash.cloudflare.com/profile/api-tokens

"@
}

# ============================================
# Main Execution
# ============================================

# Handle direct token input
if ($Token) {
    Write-Info "Using provided token..."
    if (Test-ApiToken -ApiToken $Token) {
        Save-Token -ApiToken $Token
        Write-Success "Token saved successfully!"
    } else {
        Write-ErrorMsg "Invalid token provided."
    }
    exit
}

# Main command routing
if ($Help) {
    Show-Help
} elseif ($Run) {
    Start-Tunnel
} elseif ($SetupToken) {
    Show-TokenSetup
} elseif ($Status) {
    Show-Status
} elseif ($Install) {
    Install-TunnelService
} elseif ($Uninstall) {
    Uninstall-TunnelService
} else {
    Start-FullSetup
}
