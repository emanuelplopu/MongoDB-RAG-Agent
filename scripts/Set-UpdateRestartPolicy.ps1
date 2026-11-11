<#
.SYNOPSIS
    Stops Windows Update from restarting this machine while the console session is logged on.

.DESCRIPTION
    Writes the Windows Update policy values that keep update installs automatic but leave the
    restart to a person:

      AUOptions                        = 4  (auto download and schedule the install)
      NoAutoRebootWithLoggedOnUsers    = 1  (no automatic restart while a user is logged on)
      AlwaysAutoRebootAtScheduledTime  = 0  (no forced restart at the scheduled time)

    Background: on 2026-11-11 Windows Update restarted the Quellex box unattended at 05:01 and the
    board did not come back until someone switched it on at 09:25
    (docs/system-validation/MINIPC_OUTAGE_RCA_2026-11-11.md).

    Run elevated. -Status only prints the current values; -Remove deletes them again.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\Set-UpdateRestartPolicy.ps1
#>
[CmdletBinding()]
param(
    [switch]$Status,
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$key = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU'
$values = @{
    AUOptions                       = 4
    NoAutoRebootWithLoggedOnUsers   = 1
    AlwaysAutoRebootAtScheduledTime = 0
}

function Show-Status {
    if (-not (Test-Path $key)) { Write-Output "$key : not present (no policy)"; return }
    $p = Get-ItemProperty -Path $key
    foreach ($name in $values.Keys | Sort-Object) {
        $v = $p.PSObject.Properties[$name]
        Write-Output ("{0,-32} = {1}" -f $name, $(if ($v) { $v.Value } else { '(not set)' }))
    }
}

if ($Status) { Show-Status; exit 0 }

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this script elevated (administrator).' }

if ($Remove) {
    if (Test-Path $key) {
        foreach ($name in $values.Keys) { Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue }
    }
    Write-Output 'Policy values removed.'
} else {
    New-Item -Path $key -Force | Out-Null
    foreach ($name in $values.Keys) {
        New-ItemProperty -Path $key -Name $name -Value $values[$name] -PropertyType DWord -Force | Out-Null
    }
    Write-Output 'Policy values written.'
}
Show-Status
