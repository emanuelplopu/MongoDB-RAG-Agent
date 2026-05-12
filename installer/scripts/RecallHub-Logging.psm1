#requires -Version 5.1
<#
.SYNOPSIS
    RecallHub shared logging, diagnostics, and retry module.

.DESCRIPTION
    Provides consistent logging, structured diagnostics, section tracking,
    prerequisite testing, and exponential-backoff retry for all RecallHub
    installer scripts. Import this module at the top of every installer script.

.NOTES
    Module:      RecallHub-Logging
    Version:     1.0.0
    Author:      RecallHub Installer Team
    Requires:    PowerShell 5.1+ (Windows PowerShell) or PowerShell 7+
#>

# ---------------------------------------------------------------------------
# Module-scoped state
# ---------------------------------------------------------------------------
$script:LogFile = $null
$script:LogStartTime = $null
$script:Sections = @{}
$script:DiagnosticEntries = [System.Collections.ArrayList]::new()

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------
function Write-SafeFile {
    <#
    .SYNOPSIS
        Writes content to a file with UTF-8 no-BOM encoding and error handling.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Content,
        [switch]$Append
    )

    try {
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        if ($Append) {
            [System.IO.File]::AppendAllText($Path, $Content + [Environment]::NewLine, $utf8NoBom)
        } else {
            [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
        }
    } catch [System.IO.IOException] {
        # Disk full, path too long, or concurrent access - fallback to console
        Write-Warning "RecallHub-Logging: Could not write to log file '$Path': $($_.Exception.Message)"
    } catch {
        Write-Warning "RecallHub-Logging: Unexpected error writing to '$Path': $($_.Exception.Message)"
    }
}

function Get-TimestampString {
    return (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
}

function Get-IsoTimestamp {
    return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
}

# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

function Initialize-InstallLog {
    <#
    .SYNOPSIS
        Initializes the install logging session.

    .DESCRIPTION
        Creates log directory and timestamped log file, records machine info header.

    .PARAMETER LogName
        Base name for the log file. Defaults to "install".

    .PARAMETER LogDir
        Directory for log files. Defaults to $env:LOCALAPPDATA\RecallHub\logs.

    .OUTPUTS
        [string] The full path to the created log file.
    #>
    [CmdletBinding()]
    param(
        [string]$LogName = "install",
        [string]$LogDir = (Join-Path $env:LOCALAPPDATA 'RecallHub\logs')
    )

    # Create log directory
    if (-not (Test-Path $LogDir)) {
        try {
            New-Item -Path $LogDir -ItemType Directory -Force | Out-Null
        } catch {
            Write-Warning "RecallHub-Logging: Cannot create log directory '$LogDir': $($_.Exception.Message)"
            # Fallback to temp
            $LogDir = $env:TEMP
        }
    }

    # Build timestamped filename
    $timestamp = (Get-Date).ToString('yyyy-MM-dd-HHmmss')
    $fileName = "$LogName-$timestamp.log"
    $script:LogFile = Join-Path $LogDir $fileName
    $script:LogStartTime = Get-Date
    $script:Sections = @{}
    $script:DiagnosticEntries = [System.Collections.ArrayList]::new()

    # Write header
    $osVersion = [System.Environment]::OSVersion.VersionString
    $psVersion = $PSVersionTable.PSVersion.ToString()
    $computerName = $env:COMPUTERNAME
    $userName = $env:USERNAME

    $header = @"
================================================================================
RecallHub Installer Log
================================================================================
Started:      $(Get-TimestampString)
Computer:     $computerName
OS:           $osVersion
PowerShell:   $psVersion
User:         $userName
Log File:     $($script:LogFile)
================================================================================

"@
    Write-SafeFile -Path $script:LogFile -Content $header

    return $script:LogFile
}

function Write-InstallLog {
    <#
    .SYNOPSIS
        Writes a log message to both console and log file.

    .PARAMETER Message
        The message to log.

    .PARAMETER Level
        Log level: INFO, WARN, ERROR, or DEBUG. Defaults to INFO.

    .PARAMETER Source
        Optional source identifier (calling script name).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][string]$Message,
        [ValidateSet('INFO', 'WARN', 'ERROR', 'DEBUG')]
        [string]$Level = 'INFO',
        [string]$Source = ''
    )

    $ts = Get-TimestampString
    $sourceTag = if ($Source) { $Source } else { '-' }
    $formatted = "[$ts] [$Level] [$sourceTag] $Message"

    # Console output (color-coded)
    $showInConsole = $true
    if ($Level -eq 'DEBUG' -and -not $env:RECALLHUB_DEBUG) {
        $showInConsole = $false
    }

    if ($showInConsole) {
        $color = switch ($Level) {
            'INFO'  { 'Cyan' }
            'WARN'  { 'Yellow' }
            'ERROR' { 'Red' }
            'DEBUG' { 'Gray' }
            default { 'White' }
        }
        Write-Host $formatted -ForegroundColor $color
    }

    # File output (if log initialized)
    if ($script:LogFile -and (Test-Path (Split-Path $script:LogFile -Parent) -ErrorAction SilentlyContinue)) {
        Write-SafeFile -Path $script:LogFile -Content $formatted -Append
    }
}

function Write-InstallDiagnostic {
    <#
    .SYNOPSIS
        Records a structured diagnostic entry for post-install analysis.

    .PARAMETER ErrorCode
        Structured error code (e.g. "WSL-001", "DOCKER-003").

    .PARAMETER Component
        Component name (e.g. "Docker", "WSL", "Ollama").

    .PARAMETER Message
        Human-readable description of the issue.

    .PARAMETER Context
        Hashtable with additional contextual data.

    .PARAMETER Exception
        Optional exception object for stack trace capture.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ErrorCode,
        [Parameter(Mandatory)][string]$Component,
        [Parameter(Mandatory)][string]$Message,
        [hashtable]$Context = @{},
        [System.Exception]$Exception = $null
    )

    $stackTrace = $null
    if ($Exception) {
        $stackTrace = $Exception.ToString()
    }

    $entry = [PSCustomObject]@{
        timestamp  = Get-IsoTimestamp
        errorCode  = $ErrorCode
        component  = $Component
        message    = $Message
        context    = $Context
        stackTrace = $stackTrace
    }

    $script:DiagnosticEntries.Add($entry) | Out-Null

    # Write JSON representation to log file
    try {
        $jsonLine = $entry | ConvertTo-Json -Compress -Depth 5
    } catch {
        $jsonLine = "{`"errorCode`":`"$ErrorCode`",`"message`":`"$Message`"}"
    }

    if ($script:LogFile -and (Test-Path (Split-Path $script:LogFile -Parent) -ErrorAction SilentlyContinue)) {
        Write-SafeFile -Path $script:LogFile -Content "[DIAGNOSTIC] $jsonLine" -Append
    }

    # Also log as ERROR
    Write-InstallLog -Message "[$ErrorCode] ($Component) $Message" -Level 'ERROR' -Source 'Diagnostic'
}

function Start-InstallSection {
    <#
    .SYNOPSIS
        Marks the beginning of a named install section for timing and tracking.

    .PARAMETER Name
        The section name.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name
    )

    $script:Sections[$Name] = @{
        StartTime = Get-Date
        EndTime   = $null
        Status    = 'InProgress'
    }

    $banner = @"

--- Section: $Name ---
"@
    Write-InstallLog -Message ">>> START: $Name" -Level 'INFO' -Source 'Section'

    if ($script:LogFile -and (Test-Path (Split-Path $script:LogFile -Parent) -ErrorAction SilentlyContinue)) {
        Write-SafeFile -Path $script:LogFile -Content $banner -Append
    }
}

function Complete-InstallSection {
    <#
    .SYNOPSIS
        Marks the completion of a named install section.

    .PARAMETER Name
        The section name (must match a previous Start-InstallSection call).

    .PARAMETER Status
        Completion status: Success, Failed, or Skipped.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)]
        [ValidateSet('Success', 'Failed', 'Skipped')]
        [string]$Status
    )

    if ($script:Sections.ContainsKey($Name)) {
        $script:Sections[$Name].EndTime = Get-Date
        $script:Sections[$Name].Status = $Status

        $duration = ($script:Sections[$Name].EndTime - $script:Sections[$Name].StartTime)
        $durationStr = '{0:N1}s' -f $duration.TotalSeconds
    } else {
        $durationStr = '?s'
    }

    $levelForStatus = switch ($Status) {
        'Success' { 'INFO' }
        'Failed'  { 'ERROR' }
        'Skipped' { 'WARN' }
        default   { 'INFO' }
    }

    Write-InstallLog -Message "<<< END: $Name [$Status] ($durationStr)" -Level $levelForStatus -Source 'Section'
}

function Export-InstallSummary {
    <#
    .SYNOPSIS
        Exports a JSON summary of the install session.

    .PARAMETER OutputPath
        Optional path for the summary file. Defaults to log file path with .summary.json suffix.

    .OUTPUTS
        [string] The full path to the summary JSON file.
    #>
    [CmdletBinding()]
    param(
        [string]$OutputPath = ''
    )

    if (-not $OutputPath) {
        if ($script:LogFile) {
            $OutputPath = $script:LogFile -replace '\.log$', '.summary.json'
        } else {
            $OutputPath = Join-Path $env:TEMP "recallhub-install-$(Get-Date -Format 'yyyyMMdd-HHmmss').summary.json"
        }
    }

    # Calculate duration
    $totalSeconds = 0
    if ($script:LogStartTime) {
        $totalSeconds = [math]::Round(((Get-Date) - $script:LogStartTime).TotalSeconds, 1)
    }

    # Machine info
    $osVersion = [System.Environment]::OSVersion.VersionString
    $computerName = $env:COMPUTERNAME
    $ramGB = 0
    $diskFreeGB = 0
    try {
        $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction SilentlyContinue
        if ($cs) { $ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1) }
    } catch { }
    try {
        $sysDrive = $env:SystemDrive
        if ($sysDrive) {
            $disk = Get-PSDrive -Name ($sysDrive.TrimEnd(':')) -ErrorAction SilentlyContinue
            if ($disk) { $diskFreeGB = [math]::Round($disk.Free / 1GB, 1) }
        }
    } catch { }

    # Build sections array
    $sectionsList = @()
    foreach ($key in $script:Sections.Keys) {
        $s = $script:Sections[$key]
        $durationMs = 0
        if ($s.StartTime -and $s.EndTime) {
            $durationMs = [math]::Round(($s.EndTime - $s.StartTime).TotalMilliseconds)
        }
        $sectionsList += @{
            name       = $key
            status     = $s.Status
            durationMs = $durationMs
        }
    }

    # Determine overall status
    $hasErrors = ($script:DiagnosticEntries | Where-Object { $_ }) -and ($script:DiagnosticEntries.Count -gt 0)
    $hasFailedSections = ($sectionsList | Where-Object { $_.status -eq 'Failed' }).Count -gt 0
    $hasWarnings = ($sectionsList | Where-Object { $_.status -eq 'Skipped' }).Count -gt 0

    $overallStatus = 'success'
    if ($hasErrors -or $hasFailedSections) {
        $overallStatus = 'failed'
    } elseif ($hasWarnings) {
        $overallStatus = 'partial'
    }

    # Build summary object
    $summary = [ordered]@{
        installId     = [guid]::NewGuid().ToString()
        timestamp     = Get-IsoTimestamp
        duration      = $totalSeconds
        machine       = [ordered]@{
            computerName = $computerName
            osVersion    = $osVersion
            ramGB        = $ramGB
            diskFreeGB   = $diskFreeGB
        }
        sections      = $sectionsList
        diagnostics   = @($script:DiagnosticEntries)
        overallStatus = $overallStatus
    }

    try {
        $json = $summary | ConvertTo-Json -Depth 10
        Write-SafeFile -Path $OutputPath -Content $json
        Write-InstallLog -Message "Install summary exported to: $OutputPath" -Level 'INFO' -Source 'Summary'
    } catch {
        Write-Warning "RecallHub-Logging: Failed to export summary: $($_.Exception.Message)"
    }

    return $OutputPath
}

function Test-Prerequisite {
    <#
    .SYNOPSIS
        Tests a single prerequisite condition and returns a structured result.

    .PARAMETER Name
        Display name of the prerequisite.

    .PARAMETER ScriptBlock
        The check to execute. Should return $true for pass or throw/return $false for fail.

    .PARAMETER Required
        Whether this prerequisite is mandatory. Defaults to $true.

    .PARAMETER Description
        Human-readable description of what the check validates.

    .OUTPUTS
        [PSCustomObject] with properties: Name, Status, Required, Message, Duration
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$ScriptBlock,
        [bool]$Required = $true,
        [string]$Description = ''
    )

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $status = 'Fail'
    $message = ''

    try {
        $result = & $ScriptBlock
        if ($result -eq $false) {
            $status = if ($Required) { 'Fail' } else { 'Warn' }
            $message = "Check returned false"
        } else {
            $status = 'Pass'
            $message = "Check passed"
            if ($result -is [string] -and $result -ne 'True') {
                $message = $result
            }
        }
    } catch {
        $status = if ($Required) { 'Fail' } else { 'Warn' }
        $message = $_.Exception.Message
    }

    $sw.Stop()
    $durationMs = $sw.ElapsedMilliseconds

    # Log the result
    $logLevel = switch ($status) {
        'Pass' { 'INFO' }
        'Warn' { 'WARN' }
        'Fail' { 'ERROR' }
    }
    $reqTag = if ($Required) { 'Required' } else { 'Optional' }
    Write-InstallLog -Message "Prerequisite [$reqTag] '$Name': $status - $message (${durationMs}ms)" -Level $logLevel -Source 'Prereq'

    # Write diagnostic for required failures
    if ($status -eq 'Fail' -and $Required) {
        Write-InstallDiagnostic -ErrorCode "PREREQ-FAIL" -Component $Name -Message $message -Context @{
            description = $Description
            required    = $Required
            durationMs  = $durationMs
        }
    }

    return [PSCustomObject]@{
        Name     = $Name
        Status   = $status
        Required = $Required
        Message  = $message
        Duration = $durationMs
    }
}

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        Executes a scriptblock with exponential backoff retry and timeout.

    .PARAMETER ScriptBlock
        The operation to execute.

    .PARAMETER RetryCount
        Maximum number of retry attempts. Defaults to 3.

    .PARAMETER InitialDelay
        Initial delay in seconds before first retry. Defaults to 2.

    .PARAMETER BackoffMultiplier
        Multiplier applied to delay after each failure. Defaults to 2.0.

    .PARAMETER MaxDelay
        Maximum delay cap in seconds. Defaults to 60.

    .PARAMETER TimeoutSeconds
        Maximum time in seconds for each attempt. Defaults to 300.

    .PARAMETER OperationName
        Descriptive name for logging purposes.

    .OUTPUTS
        The result of the scriptblock on success.

    .NOTES
        Throws on final failure after all retries are exhausted.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][scriptblock]$ScriptBlock,
        [int]$RetryCount = 3,
        [double]$InitialDelay = 2.0,
        [double]$BackoffMultiplier = 2.0,
        [double]$MaxDelay = 60.0,
        [int]$TimeoutSeconds = 300,
        [string]$OperationName = 'Operation'
    )

    $attempts = [System.Collections.ArrayList]::new()
    $currentDelay = $InitialDelay
    $totalAttempts = $RetryCount + 1  # initial attempt + retries

    for ($i = 1; $i -le $totalAttempts; $i++) {
        $attemptStart = Get-Date
        $attemptError = $null

        Write-InstallLog -Message "$OperationName - Attempt $i of $totalAttempts" -Level 'INFO' -Source 'Retry'

        try {
            # Execute with timeout using a job for PS 5.1 compat
            $job = Start-Job -ScriptBlock $ScriptBlock
            $completed = $job | Wait-Job -Timeout $TimeoutSeconds

            if ($null -eq $completed) {
                # Timeout - kill the job
                $job | Stop-Job -PassThru | Remove-Job -Force
                $attemptError = "Operation timed out after ${TimeoutSeconds}s"
                Write-InstallLog -Message "$OperationName - Attempt $i timed out after ${TimeoutSeconds}s" -Level 'WARN' -Source 'Retry'
            } else {
                # Check for job errors
                if ($job.State -eq 'Failed') {
                    $jobError = $job.ChildJobs[0].JobStateInfo.Reason
                    $job | Remove-Job -Force
                    if ($jobError) {
                        throw $jobError
                    }
                    throw "Job failed without specific error"
                }

                $result = $job | Receive-Job
                $job | Remove-Job -Force

                # Check if Receive-Job produced an error
                if ($job.State -eq 'Failed') {
                    throw "Job execution failed"
                }

                Write-InstallLog -Message "$OperationName - Attempt $i succeeded" -Level 'INFO' -Source 'Retry'
                return $result
            }
        } catch {
            $attemptError = $_.Exception.Message
            Write-InstallLog -Message "$OperationName - Attempt $i failed: $attemptError" -Level 'WARN' -Source 'Retry'

            # Clean up job if it exists
            if ($job) {
                try { $job | Remove-Job -Force -ErrorAction SilentlyContinue } catch { }
            }
        }

        $attemptDuration = ((Get-Date) - $attemptStart).TotalSeconds
        $attempts.Add(@{
            attempt  = $i
            error    = $attemptError
            duration = [math]::Round($attemptDuration, 2)
        }) | Out-Null

        # If not the last attempt, wait with backoff
        if ($i -lt $totalAttempts) {
            $sleepTime = [math]::Min($currentDelay, $MaxDelay)
            Write-InstallLog -Message "$OperationName - Waiting ${sleepTime}s before retry..." -Level 'DEBUG' -Source 'Retry'
            Start-Sleep -Seconds $sleepTime
            $currentDelay = $currentDelay * $BackoffMultiplier
        }
    }

    # All attempts exhausted
    $attemptDetails = ($attempts | ForEach-Object { "Attempt $($_.attempt): $($_.error) ($($_.duration)s)" }) -join '; '
    $finalMessage = "$OperationName failed after $totalAttempts attempts. Details: $attemptDetails"

    Write-InstallDiagnostic -ErrorCode "RETRY-EXHAUSTED" -Component $OperationName -Message $finalMessage -Context @{
        totalAttempts = $totalAttempts
        attempts      = $attempts
    }

    throw $finalMessage
}

# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------
Export-ModuleMember -Function @(
    'Initialize-InstallLog'
    'Write-InstallLog'
    'Write-InstallDiagnostic'
    'Start-InstallSection'
    'Complete-InstallSection'
    'Export-InstallSummary'
    'Test-Prerequisite'
    'Invoke-WithRetry'
)
