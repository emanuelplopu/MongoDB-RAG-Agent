#Requires -Version 5.1
<#
.SYNOPSIS
    Generates the login token signing keys of an install once and keeps them.
.DESCRIPTION
    The backends refuse to start without a signing key of their own
    (JWT_SECRET_REQUIRED), so every install needs one per tenant:

      * Backup/restore installs (the repo's docker-compose.yml, e.g. C:\Quellex):
        RECALLHUB_JWT_SECRET_KEY and QUELLEX_JWT_SECRET_KEY in <InstallPath>\.env,
        which docker-compose.yml passes to the tenant backends as JWT_SECRET_KEY.
        full-restore.ps1 and INSTALL.ps1 call the module directly; run this
        script by hand to add keys to an older install.
      * Hardened installer (-Hardened): JWT_SECRET_KEY in <InstallPath>\secrets.env,
        the second env_file of docker-compose.hardened.yml. setup.iss runs this
        after copying files; it never ships or overwrites secrets.env, so
        upgrades keep the key.

    Valid keys are kept. The output names each key and whether it was kept or
    generated, never its value. Generating a key for a running stack signs its
    users out once the backend is recreated.
.PARAMETER InstallPath
    The folder with docker-compose.yml and .env. Defaults to the parent of this
    script's folder (<app> for <app>\scripts).
.PARAMETER Hardened
    Write JWT_SECRET_KEY to secrets.env (hardened installer layout).
.OUTPUTS
    Exit 0 = keys present, 1 = keys could not be written.
.EXAMPLE
    .\Initialize-JwtSecrets.ps1 -InstallPath C:\Quellex
.EXAMPLE
    .\Initialize-JwtSecrets.ps1 -InstallPath "C:\Program Files\RecallHub" -Hardened
#>
param(
    [string]$InstallPath = "",
    [switch]$Hardened
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Import-Module (Join-Path $ScriptDir "RecallHub-Secrets.psm1") -Force

if (-not $InstallPath) { $InstallPath = Split-Path $ScriptDir -Parent }

if ($Hardened) {
    $envPath = Join-Path $InstallPath "secrets.env"
    $names = @("JWT_SECRET_KEY")
} else {
    $envPath = Join-Path $InstallPath ".env"
    $names = Get-JwtSecretNames
}

try {
    $results = @(Initialize-JwtSecrets -EnvPath $envPath -Names $names)
} catch {
    Write-Host "[ERROR] Could not write the login signing keys to ${envPath}: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "        The backend refuses to start without them; run this script as an administrator." -ForegroundColor Red
    exit 1
}

foreach ($r in $results) {
    $verb = switch ($r.Action) { "Generated" { "generated" } "Restored" { "restored" } default { "kept" } }
    Write-Host "[OK] $($r.Name): $verb ($envPath)" -ForegroundColor Green
}
exit 0
