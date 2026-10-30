<#
.SYNOPSIS
    Caps or restores the CPU boost of the active Windows power plan (elevated).

.DESCRIPTION
    -Apply sets "Maximum processor state" to 99 % and "Processor performance boost mode" to
    Disabled for AC and DC on the active plan, which keeps the CPU at its base clocks: a quiet,
    cooler machine at some throughput cost. -Restore puts both back to 100 % / Aggressive (the
    Windows defaults of the Balanced plan). -Status prints the current values. Every run appends
    a transcript to C:\ProgramData\RecallHub-powerplan.log. The boost-mode setting is hidden in
    the Control Panel; the script unhides it so the values can be read back.

.EXAMPLE
    Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File scripts\Set-CpuBoostCap.ps1 -Apply'
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Restore,
    [switch]$Status
)

$ErrorActionPreference = 'Continue'
$log = 'C:\ProgramData\RecallHub-powerplan.log'
$procThrottleMax = 'bc5038f7-23e0-4960-96da-33abaf5935ec'   # Maximum processor state
$perfBoostMode = 'be337238-0d82-4146-a960-4f3749d470c7'     # Processor performance boost mode (hidden by default)

function Get-ProcessorSetting([string]$guid) {
    $out = (powercfg /query SCHEME_CURRENT SUB_PROCESSOR $guid) -join "`n"
    $ac = [regex]::Match($out, 'Current AC Power Setting Index: 0x([0-9a-fA-F]+)')
    $dc = [regex]::Match($out, 'Current DC Power Setting Index: 0x([0-9a-fA-F]+)')
    $acv = if ($ac.Success) { [convert]::ToInt32($ac.Groups[1].Value, 16) } else { 'n/a' }
    $dcv = if ($dc.Success) { [convert]::ToInt32($dc.Groups[1].Value, 16) } else { 'n/a' }
    return "AC=$acv DC=$dcv"
}

function Show-Status {
    $scheme = ((powercfg /getactivescheme) -join '') -replace '^Power Scheme GUID: ', ''
    "$(Get-Date -Format s) plan=$scheme | maxState% $(Get-ProcessorSetting $procThrottleMax) | boostMode $(Get-ProcessorSetting $perfBoostMode) (0=Disabled, 2=Aggressive)"
}

Start-Transcript -Path $log -Append | Out-Null
try {
    # Unhide the boost-mode setting so powercfg /query reports it
    powercfg -attributes SUB_PROCESSOR $perfBoostMode -ATTRIB_HIDE | Out-Null
    "before: $(Show-Status)"
    if ($Apply) {
        powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR $procThrottleMax 99
        powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR $procThrottleMax 99
        powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR $perfBoostMode 0
        powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR $perfBoostMode 0
        powercfg /setactive SCHEME_CURRENT
        "after:  $(Show-Status)"
    } elseif ($Restore) {
        powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR $procThrottleMax 100
        powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR $procThrottleMax 100
        powercfg /setacvalueindex SCHEME_CURRENT SUB_PROCESSOR $perfBoostMode 2
        powercfg /setdcvalueindex SCHEME_CURRENT SUB_PROCESSOR $perfBoostMode 2
        powercfg /setactive SCHEME_CURRENT
        "after:  $(Show-Status)"
    }
} finally {
    Stop-Transcript | Out-Null
}
