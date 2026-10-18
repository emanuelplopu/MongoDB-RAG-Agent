<#
.SYNOPSIS
    Removes the RecallHub host agent (service or logon task and its files).
.DESCRIPTION
    Stops and unregisters the agent installed by Install-HostAgent.ps1 and deletes its program
    folder. The recorded telemetry in MongoDB is never touched (it expires through its TTL). The
    settings in %ProgramData%\RecallHub\HostAgent are kept unless -RemoveConfig is given. The PawnIO
    driver is left installed (other hardware tools may use it) unless -RemovePawnIO is given.
    Requires administrator privileges (self-elevates).
.EXAMPLE
    .\Uninstall-HostAgent.ps1
.EXAMPLE
    .\Uninstall-HostAgent.ps1 -RemoveConfig -RemovePawnIO
#>
param(
    [string]$InstallDir = (Join-Path $env:ProgramFiles "RecallHub\HostAgent"),
    [string]$ConfigDir = (Join-Path $env:ProgramData "RecallHub\HostAgent"),
    [switch]$RemoveConfig,
    [switch]$RemovePawnIO,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"
$ServiceName = "RecallHubHostAgent"
$TaskName = "RecallHub Host Agent"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Administrator)) {
    if ($NoElevate) {
        Write-Host "  [ERROR] Administrator privileges are required." -ForegroundColor Red
        exit 1
    }
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"", "-NoElevate")
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [switch]) { if ($kv.Value) { $argList += "-$($kv.Key)" } }
        else { $argList += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($service) {
    if ($service.Status -ne "Stopped") { Stop-Service -Name $ServiceName -Force }
    & sc.exe delete $ServiceName | Out-Null
    Write-Host "  [OK] Removed service $ServiceName" -ForegroundColor Green
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "  [OK] Removed logon task '$TaskName'" -ForegroundColor Green
}
Get-Process -Name "RecallHubHostAgent" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if (Test-Path $InstallDir) {
    Remove-Item -Path $InstallDir -Recurse -Force
    Write-Host "  [OK] Removed $InstallDir" -ForegroundColor Green
}
if ($RemoveConfig -and (Test-Path $ConfigDir)) {
    Remove-Item -Path $ConfigDir -Recurse -Force
    Write-Host "  [OK] Removed $ConfigDir" -ForegroundColor Green
}
if ($RemovePawnIO) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget uninstall --id namazso.PawnIO -e --silent
    } else {
        Write-Host "  [INFO] Remove PawnIO via Settings > Apps." -ForegroundColor Yellow
    }
}
Write-Host "  [OK] Host agent removed (recorded telemetry kept; it expires through its TTL)." -ForegroundColor Green
exit 0
