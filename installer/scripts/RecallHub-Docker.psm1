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

function Get-DotEnvValue {
    <#
    .SYNOPSIS
        Value of a key in a Compose .env file; $null when the file or key is absent.
    .DESCRIPTION
        Reads the file the way Compose does for the keys the service scripts need:
        leading whitespace and an optional "export " are ignored, comment lines are
        skipped, surrounding quotes are stripped and the last definition wins.
        Variable interpolation is not supported.
    #>
    [CmdletBinding()]
    [OutputType([string])]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Key
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $value = $null
    $pattern = '^\s*(?:export\s+)?' + [regex]::Escape($Key) + '\s*=(.*)$'
    foreach ($line in (Get-Content -LiteralPath $Path)) {
        if ($line -match $pattern) {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
    }
    return $value
}

function Get-ComposeFileSet {
    <#
    .SYNOPSIS
        The compose files Start-Services.ps1 and Stop-Services.ps1 pass with -f, in order.
    .DESCRIPTION
        An explicit -f list makes Compose ignore COMPOSE_FILE from the project's .env
        (just as an explicit --profile replaces COMPOSE_PROFILES), so a start or stop
        from the scripts used to drop every layer added there, e.g.
        docker-compose.gpu.yml. The scripts therefore read COMPOSE_FILE themselves:

          * ComposeFile is the default docker-compose.yml and COMPOSE_FILE is set
            (process environment first, then <InstallPath>\.env, as Compose does):
            the listed files in that order, split on COMPOSE_PATH_SEPARATOR (same
            lookup; default ';', the Windows path separator). Relative entries are
            resolved against InstallPath. A listed file that does not exist is
            reported through -Log (WARN) and skipped; when none of them exists the
            default set below is used instead, so the start never fails on a typo.
          * Otherwise: <InstallPath>\<ComposeFile>, plus docker-compose.override.yml
            and docker-compose.shares.yml when ComposeFile is docker-compose.yml and
            they exist (the behaviour from before COMPOSE_FILE was honoured).

        Must match ServiceManager.ResolveComposeFiles in the tray app.
    .PARAMETER InstallPath
        Folder holding the compose files and .env (Compose's project directory).
    .PARAMETER ComposeFile
        docker.composeFile from config.json, relative to InstallPath or absolute.
    .PARAMETER Log
        Script block receiving (Message, Level) for skipped entries.
    .OUTPUTS
        PSCustomObject with Files (paths as passed to -f, never empty, in order,
        duplicates dropped) and Source ('COMPOSE_FILE' or 'default').
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$InstallPath,
        [string]$ComposeFile = 'docker-compose.yml',
        [scriptblock]$Log = { param([string]$Message, [string]$Level) }
    )

    $composePath = if ([System.IO.Path]::IsPathRooted($ComposeFile)) { $ComposeFile } else { Join-Path $InstallPath $ComposeFile }
    $isDefaultFile = ([System.IO.Path]::GetFileName($ComposeFile) -eq 'docker-compose.yml')
    $envFile = Join-Path $InstallPath '.env'

    if ($isDefaultFile) {
        $list = $env:COMPOSE_FILE
        $origin = 'the environment'
        if ([string]::IsNullOrWhiteSpace($list)) {
            $list = Get-DotEnvValue -Path $envFile -Key 'COMPOSE_FILE'
            $origin = $envFile
        }
        if (-not [string]::IsNullOrWhiteSpace($list)) {
            $separator = $env:COMPOSE_PATH_SEPARATOR
            if ([string]::IsNullOrEmpty($separator)) { $separator = Get-DotEnvValue -Path $envFile -Key 'COMPOSE_PATH_SEPARATOR' }
            if ([string]::IsNullOrEmpty($separator)) { $separator = [string][System.IO.Path]::PathSeparator }

            $files = New-Object System.Collections.Generic.List[string]
            foreach ($entry in ($list -split [regex]::Escape($separator))) {
                $name = $entry.Trim().Trim('"').Trim("'")
                if (-not $name) { continue }
                $path = if ([System.IO.Path]::IsPathRooted($name)) { $name } else { Join-Path $InstallPath $name }
                if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                    & $Log "COMPOSE_FILE lists $name but $path does not exist - skipped" 'WARN'
                    continue
                }
                if ($files -contains $path) { continue }
                $files.Add($path)
            }
            if ($files.Count -gt 0) {
                return [pscustomobject]@{ Files = @($files); Source = 'COMPOSE_FILE' }
            }
            & $Log "COMPOSE_FILE from $origin ($list) names no existing file - using the default compose file set" 'WARN'
        }
    }

    $default = New-Object System.Collections.Generic.List[string]
    $default.Add($composePath)
    if ($isDefaultFile) {
        foreach ($extra in @('docker-compose.override.yml', 'docker-compose.shares.yml')) {
            $extraPath = Join-Path $InstallPath $extra
            if (Test-Path -LiteralPath $extraPath -PathType Leaf) { $default.Add($extraPath) }
        }
    }
    return [pscustomobject]@{ Files = @($default); Source = 'default' }
}

Export-ModuleMember -Function @(
    'Test-DockerEngine',
    'Test-DockerDesktopProcess',
    'Test-DockerDesktopCli',
    'Invoke-DockerDesktopStartCli',
    'Start-DockerDesktopQuietly',
    'Wait-DockerEngine',
    'Confirm-DockerEngine',
    'Get-DotEnvValue',
    'Get-ComposeFileSet'
)
