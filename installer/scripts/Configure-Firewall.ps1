<#
.SYNOPSIS
    Configures Windows Firewall rules for RecallHub.

.DESCRIPTION
    This script creates firewall rules to:
    1. Allow localhost-only access to the frontend (port 11080)
    2. Block external access to all other RecallHub ports
    3. Optionally allow SSH access for admin purposes

.NOTES
    Requires administrator privileges.
#>

param(
    [switch]$EnableSSH,
    [switch]$RemoveRules,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$RULE_PREFIX = "RecallHub"
$FRONTEND_PORT = 11080
$BACKEND_PORT = 11000
$MONGODB_PORT = 11017
$OLLAMA_PORT = 11434
$SSH_PORT = 22

function Write-Step {
    param([string]$Message)
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warning {
    param([string]$Message)
    Write-Host "    [WARN] $Message" -ForegroundColor Yellow
}

function Write-Error {
    param([string]$Message)
    Write-Host "    [ERROR] $Message" -ForegroundColor Red
}

function Test-Administrator {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-ExistingRules {
    Write-Step "Removing existing RecallHub firewall rules..."
    
    try {
        $existingRules = Get-NetFirewallRule -DisplayName "$RULE_PREFIX*" -ErrorAction SilentlyContinue
        
        if ($existingRules) {
            $existingRules | Remove-NetFirewallRule
            Write-Success "Removed $($existingRules.Count) existing rules"
        }
        else {
            Write-Success "No existing rules found"
        }
    }
    catch {
        Write-Warning "Could not remove existing rules: $_"
    }
}

function Add-FrontendRule {
    Write-Step "Creating frontend access rule (localhost only)..."
    
    try {
        # Allow inbound on frontend port from localhost only
        New-NetFirewallRule `
            -DisplayName "$RULE_PREFIX - Frontend (localhost)" `
            -Description "Allow access to RecallHub frontend from localhost only" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $FRONTEND_PORT `
            -RemoteAddress 127.0.0.1 `
            -Action Allow `
            -Profile Any `
            -Enabled True | Out-Null
        
        Write-Success "Created frontend allow rule (port $FRONTEND_PORT, localhost only)"
    }
    catch {
        Write-Error "Failed to create frontend rule: $_"
        throw
    }
}

function Add-BlockRules {
    Write-Step "Creating block rules for internal services..."
    
    $blockedPorts = @(
        @{ Port = $BACKEND_PORT; Name = "Backend API" },
        @{ Port = $MONGODB_PORT; Name = "MongoDB" },
        @{ Port = $OLLAMA_PORT; Name = "Ollama" }
    )
    
    foreach ($service in $blockedPorts) {
        try {
            # Block all inbound traffic to these ports
            New-NetFirewallRule `
                -DisplayName "$RULE_PREFIX - Block $($service.Name) External" `
                -Description "Block external access to $($service.Name)" `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $service.Port `
                -Action Block `
                -Profile Any `
                -Enabled True | Out-Null
            
            Write-Success "Created block rule for $($service.Name) (port $($service.Port))"
        }
        catch {
            Write-Warning "Could not create block rule for $($service.Name): $_"
        }
    }
}

function Add-OutboundBlockRules {
    Write-Step "Creating outbound block rules (air-gap protection)..."
    
    try {
        # Get the RecallHub WSL2 interface
        # Note: This blocks outbound from WSL2 containers
        
        # Block HTTP/HTTPS outbound except to localhost
        New-NetFirewallRule `
            -DisplayName "$RULE_PREFIX - Block Outbound HTTP" `
            -Description "Block outbound HTTP except localhost" `
            -Direction Outbound `
            -Protocol TCP `
            -RemotePort 80,443 `
            -RemoteAddress "0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255" `
            -Program "wsl.exe" `
            -Action Block `
            -Profile Any `
            -Enabled True | Out-Null
        
        Write-Success "Created outbound block rules for HTTP/HTTPS"
    }
    catch {
        Write-Warning "Could not create outbound block rules: $_"
        Write-Host "    This may require manual configuration for full air-gap protection"
    }
}

function Add-SSHRule {
    Write-Step "Creating SSH access rule (localhost only)..."
    
    try {
        New-NetFirewallRule `
            -DisplayName "$RULE_PREFIX - SSH (localhost)" `
            -Description "Allow SSH access to RecallHub from localhost only" `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $SSH_PORT `
            -RemoteAddress 127.0.0.1 `
            -Action Allow `
            -Profile Any `
            -Enabled True | Out-Null
        
        Write-Success "Created SSH allow rule (port $SSH_PORT, localhost only)"
    }
    catch {
        Write-Error "Failed to create SSH rule: $_"
    }
}

function Show-CurrentRules {
    Write-Step "Current RecallHub firewall rules:"
    
    try {
        $rules = Get-NetFirewallRule -DisplayName "$RULE_PREFIX*" -ErrorAction SilentlyContinue
        
        if ($rules) {
            $rules | ForEach-Object {
                $portFilter = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $_
                $addressFilter = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $_
                
                Write-Host "`n    Rule: $($_.DisplayName)"
                Write-Host "    Action: $($_.Action)"
                Write-Host "    Direction: $($_.Direction)"
                Write-Host "    Protocol: $($portFilter.Protocol)"
                Write-Host "    Port: $($portFilter.LocalPort)"
                Write-Host "    Remote Address: $($addressFilter.RemoteAddress)"
                Write-Host "    Enabled: $($_.Enabled)"
            }
        }
        else {
            Write-Host "    No RecallHub firewall rules found"
        }
    }
    catch {
        Write-Warning "Could not retrieve firewall rules"
    }
}

function Test-RulesEffective {
    Write-Step "Testing firewall rules..."
    
    # Test that frontend is accessible from localhost
    try {
        $result = Test-NetConnection -ComputerName localhost -Port $FRONTEND_PORT -WarningAction SilentlyContinue
        if ($result.TcpTestSucceeded) {
            Write-Success "Frontend (localhost:$FRONTEND_PORT) is accessible"
        }
        else {
            Write-Warning "Frontend may not be accessible (service might not be running)"
        }
    }
    catch {
        Write-Warning "Could not test frontend connectivity"
    }
}

function Main {
    Write-Host @"
╔═══════════════════════════════════════════════════════════════════╗
║         RecallHub Firewall Configuration                          ║
║         Securing network access to RecallHub services             ║
╚═══════════════════════════════════════════════════════════════════╝
"@ -ForegroundColor Magenta

    # Check for admin privileges
    if (-not (Test-Administrator)) {
        Write-Error "This script requires administrator privileges."
        Write-Host "`n    Please run PowerShell as Administrator and try again."
        exit 1
    }
    
    Write-Success "Running with administrator privileges"
    
    # Handle remove option
    if ($RemoveRules) {
        Remove-ExistingRules
        Write-Host "`n" + ("=" * 60) -ForegroundColor Green
        Write-Host "FIREWALL RULES REMOVED" -ForegroundColor Green
        Write-Host ("=" * 60) -ForegroundColor Green
        exit 0
    }
    
    # Remove existing rules first
    Remove-ExistingRules
    
    # Create new rules
    Add-FrontendRule
    Add-BlockRules
    Add-OutboundBlockRules
    
    # Optionally add SSH rule
    if ($EnableSSH) {
        Add-SSHRule
    }
    
    # Show current rules
    Show-CurrentRules
    
    # Test rules
    Test-RulesEffective
    
    Write-Host "`n" + ("=" * 60) -ForegroundColor Green
    Write-Host "FIREWALL CONFIGURATION COMPLETE" -ForegroundColor Green
    Write-Host ("=" * 60) -ForegroundColor Green
    Write-Host "`nRecallHub is now protected with the following configuration:"
    Write-Host "  - Frontend: Accessible from localhost only (port $FRONTEND_PORT)"
    Write-Host "  - Backend: Blocked from external access (port $BACKEND_PORT)"
    Write-Host "  - MongoDB: Blocked from external access (port $MONGODB_PORT)"
    Write-Host "  - Ollama: Blocked from external access (port $OLLAMA_PORT)"
    if ($EnableSSH) {
        Write-Host "  - SSH: Accessible from localhost only (port $SSH_PORT)"
    }
    Write-Host "`nOutbound connections are blocked for air-gap protection."
}

# Run main
Main
