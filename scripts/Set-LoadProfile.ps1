<#
.SYNOPSIS
    Switch the ingestion load profile of a running RecallHub/Quellex backend.

.DESCRIPTION
    Reads the current ingestion performance config from the API, sets `load_profile`
    (server_room, office or office_lite) and writes it back, so a running job picks the
    new slot limit up within a minute (backend/services/ingestion_schedule.py).
    Meant for scheduled tasks, e.g. "office_lite on Monday 08:00, server_room on Friday evening".

.PARAMETER Profile
    server_room | office | office_lite

.PARAMETER ApiBase
    Backend base URL (default http://127.0.0.1:11001).

.PARAMETER ApiKeyFile
    File holding an admin API key (one line). Never commit the key.

.PARAMETER LogFile
    Appends one line per run (default C:\RecallHub-Verify\logs\load-profile.log).

.EXAMPLE
    .\Set-LoadProfile.ps1 -Profile office_lite -ApiKeyFile C:\RecallHub-Verify\secrets\load-profile-api-key.txt
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('server_room', 'office', 'office_lite')]
    [string]$Profile,
    [string]$ApiBase = 'http://127.0.0.1:11001',
    [Parameter(Mandatory = $true)]
    [string]$ApiKeyFile,
    [string]$LogFile = 'C:\RecallHub-Verify\logs\load-profile.log'
)

$ErrorActionPreference = 'Stop'
$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
$logDir = Split-Path -Parent $LogFile
if ($logDir -and -not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

function Write-Log([string]$text) {
    $line = "$stamp $text"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

try {
    if (-not (Test-Path $ApiKeyFile)) { throw "API key file not found: $ApiKeyFile" }
    $key = (Get-Content -Path $ApiKeyFile -Raw).Trim()
    if (-not $key) { throw "API key file is empty: $ApiKeyFile" }
    $headers = @{ 'X-API-Key' = $key; 'Content-Type' = 'application/json' }
    $url = "$ApiBase/api/v1/system/ingestion-performance"

    $config = Invoke-RestMethod -Uri $url -Headers $headers -Method Get -TimeoutSec 30
    $before = $config.load_profile
    $config.load_profile = $Profile
    $body = $config | ConvertTo-Json -Depth 6
    $result = Invoke-RestMethod -Uri $url -Headers $headers -Method Post -Body $body -TimeoutSec 30
    $after = (Invoke-RestMethod -Uri $url -Headers $headers -Method Get -TimeoutSec 30).load_profile
    if ($after -ne $Profile) { throw "profile is '$after' after the save, expected '$Profile'" }
    Write-Log "load profile $before -> $after ($($result.message))"
    exit 0
}
catch {
    Write-Log "FAILED to set load profile '$Profile': $($_.Exception.Message)"
    exit 1
}
