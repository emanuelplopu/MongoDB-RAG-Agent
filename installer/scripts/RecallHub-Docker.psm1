<#
.SYNOPSIS
    Docker Desktop readiness helpers shared by the RecallHub service scripts.
.DESCRIPTION
    Docker Desktop is a single-instance application: launching "Docker Desktop.exe"
    while it is already running - or still booting after login - starts nothing,
    it only tells the running instance to show its Dashboard window. The tray app
    and Docker Desktop both autostart at login, and the engine needs 20-60 s before
    `docker info` answers, so a script that treats "no answer" as "not running" and
    relaunches the exe makes the Dashboard pop up in front of every user on every
    login.

    Confirm-DockerEngine therefore
      1. returns immediately when the engine already answers,
      2. only waits (never launches) while a "Docker Desktop" process exists,
      3. launches Docker Desktop only when no such process exists, preferring the
         Docker Desktop CLI (`docker desktop start`, bundled since 4.37) over the
         executable, which is started minimized as a fallback.

    Every probe is a small function so the tests in installer/tests can mock it.
#>

Set-StrictMode -Version 2.0

$script:DefaultDesktopPath = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'

function Invoke-DockerCli {
    <#
    .SYNOPSIS
        Runs the docker CLI and returns $true when it exits with 0.
    .DESCRIPTION
        Docker writes progress and errors to stderr; Windows PowerShell 5.1 would turn
        that into a terminating error under ErrorActionPreference = Stop, so the call
        runs under Continue and is judged by the exit code only. Output is discarded.
    #>
    [CmdletBinding()]
    [OutputType([bool])]
    param([Parameter(Mandatory)][string[]]$Arguments)

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & docker @Arguments 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-DockerEngine {
    <# .SYNOPSIS True when the Docker engine answers `docker info`. #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    return (Invoke-DockerCli -Arguments @('info'))
}

function Test-DockerDesktopProcess {
    <# .SYNOPSIS True when a "Docker Desktop" process exists (running or still booting). #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    return ($null -ne (Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue))
}

function Test-DockerDesktopCli {
    <# .SYNOPSIS True when the Docker Desktop CLI plugin (`docker desktop ...`) is available. #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    return (Invoke-DockerCli -Arguments @('desktop', 'version'))
}

function Invoke-DockerDesktopStartCli {
    <# .SYNOPSIS Asks the Docker Desktop CLI to start Docker Desktop in the background. #>
    [CmdletBinding()]
    [OutputType([bool])]
    param()
    return (Invoke-DockerCli -Arguments @('desktop', 'start', '--detach'))
}

function Start-DockerDesktopQuietly {
    <#
    .SYNOPSIS
        Launches Docker Desktop without bringing its Dashboard to the front.
    .DESCRIPTION
        Call this only when no Docker Desktop process exists (Test-DockerDesktopProcess):
        a second launch of a running instance opens the Dashboard window.
    .PARAMETER DesktopPath
        Docker Desktop executable, used when the Docker Desktop CLI is unavailable.
    .PARAMETER Log
        Script block receiving (Message, Level) for progress messages.
    .OUTPUTS
        'cli' when started through `docker desktop start`, 'exe' when the executable
        was started, $null when nothing could be launched.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [string]$DesktopPath = $script:DefaultDesktopPath,
        [scriptblock]$Log = { param([string]$Message, [string]$Level) }
    )

    if (Test-DockerDesktopCli) {
        if (Invoke-DockerDesktopStartCli) {
            & $Log "Docker Desktop start requested via 'docker desktop start'" 'INFO'
            return 'cli'
        }
        & $Log "'docker desktop start' failed - falling back to $DesktopPath" 'WARN'
    }

    if (-not (Test-Path -LiteralPath $DesktopPath)) {
        & $Log "Docker Desktop not found at $DesktopPath" 'ERROR'
        return $null
    }

    Start-Process -FilePath $DesktopPath -WindowStyle Minimized
    & $Log "Docker Desktop started from $DesktopPath" 'INFO'
    return 'exe'
}

function Wait-DockerEngine {
    <#
    .SYNOPSIS
        Polls the Docker engine until it answers.
    .OUTPUTS
        Seconds waited until the engine answered, or -1 when TimeoutSeconds elapsed.
    #>
    [CmdletBinding()]
    [OutputType([int])]
    param(
        [int]$TimeoutSeconds = 120,
        [int]$PollSeconds = 3
    )

    $waited = 0
    while ($waited -lt $TimeoutSeconds) {
        Start-Sleep -Seconds $PollSeconds
        $waited += $PollSeconds
        if (Test-DockerEngine) { return $waited }
    }
    return -1
}

function New-DockerEngineResult {
    param([bool]$Ready, [string]$Action, [int]$Seconds, [string]$Message)
    return [pscustomobject]@{
        Ready   = $Ready
        Action  = $Action
        Seconds = $Seconds
        Message = $Message
    }
}

function Confirm-DockerEngine {
    <#
    .SYNOPSIS
        Makes sure the Docker engine is reachable, starting Docker Desktop only when
        nothing is running yet.
    .PARAMETER DesktopPath
        Docker Desktop executable (fallback when the Docker Desktop CLI is unavailable).
    .PARAMETER TimeoutSeconds
        How long to wait for the engine after Docker Desktop was found booting or was started.
    .PARAMETER PollSeconds
        Interval between `docker info` probes.
    .PARAMETER NoLaunch
        Never start Docker Desktop; only wait for an instance that already exists.
    .PARAMETER Log
        Script block receiving (Message, Level) for progress messages.
    .OUTPUTS
        PSCustomObject with Ready (bool), Action (ready | waited | started-cli |
        started-exe | not-installed | timeout | disabled), Seconds (int) and Message.
    #>
    [CmdletBinding()]
    param(
        [string]$DesktopPath = $script:DefaultDesktopPath,
        [int]$TimeoutSeconds = 120,
        [int]$PollSeconds = 3,
        [switch]$NoLaunch,
        [scriptblock]$Log = { param([string]$Message, [string]$Level) }
    )

    if (Test-DockerEngine) {
        return (New-DockerEngineResult -Ready $true -Action 'ready' -Seconds 0 -Message 'Docker engine is ready')
    }

    if (Test-DockerDesktopProcess) {
        & $Log "Docker Desktop is running but its engine is not ready yet - waiting up to ${TimeoutSeconds}s (not relaunching it: that would only open the Dashboard window)" 'INFO'
        $action = 'waited'
    } elseif ($NoLaunch) {
        return (New-DockerEngineResult -Ready $false -Action 'disabled' -Seconds 0 -Message 'Docker engine is not reachable and starting Docker Desktop is disabled')
    } else {
        $how = Start-DockerDesktopQuietly -DesktopPath $DesktopPath -Log $Log
        if (-not $how) {
            return (New-DockerEngineResult -Ready $false -Action 'not-installed' -Seconds 0 -Message "Docker Desktop not found at $DesktopPath")
        }
        $action = "started-$how"
    }

    $seconds = Wait-DockerEngine -TimeoutSeconds $TimeoutSeconds -PollSeconds $PollSeconds
    if ($seconds -lt 0) {
        return (New-DockerEngineResult -Ready $false -Action 'timeout' -Seconds $TimeoutSeconds -Message "Docker engine did not answer within ${TimeoutSeconds}s - Docker Desktop may be showing a dialog; open it from its tray icon")
    }
    return (New-DockerEngineResult -Ready $true -Action $action -Seconds $seconds -Message "Docker engine ready after ${seconds}s ($action)")
}

Export-ModuleMember -Function @(
    'Test-DockerEngine',
    'Test-DockerDesktopProcess',
    'Test-DockerDesktopCli',
    'Invoke-DockerDesktopStartCli',
    'Start-DockerDesktopQuietly',
    'Wait-DockerEngine',
    'Confirm-DockerEngine'
)
