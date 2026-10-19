<#
.SYNOPSIS
    Per-install login token signing keys for RecallHub / Quellex installs.
.DESCRIPTION
    docker-compose.yml passes RECALLHUB_JWT_SECRET_KEY and QUELLEX_JWT_SECRET_KEY
    from the install's .env to the tenant backends as JWT_SECRET_KEY. The backends
    run with JWT_SECRET_REQUIRED=true and refuse to start without a key of their
    own, so the installers must provide one:

      * Initialize-JwtSecrets generates a key once per install and keeps it: a
        valid key already in the file stays, a key captured before a restore
        overwrote the file (Get-JwtSecrets) is put back, only a missing one is
        generated.
      * Clear-JwtSecrets blanks the keys in backup packages: the keys belong to
        the install, so a package installed on another machine gets its own.

    The hardened installer keeps its single JWT_SECRET_KEY in <app>\secrets.env
    (see Initialize-JwtSecrets.ps1), which setup.iss never ships or overwrites.

    No function writes a key to the console, a log or its result objects; only
    New-JwtSecret and Get-JwtSecrets return key values, to callers that keep them
    in memory.
#>

Set-StrictMode -Version 2.0

#: The per-tenant variables docker-compose.yml forwards to the backends.
$script:JwtSecretNames = @('RECALLHUB_JWT_SECRET_KEY', 'QUELLEX_JWT_SECRET_KEY')

#: Keys that are public and never count as valid: the fallback releases before
#: 2026-10 used (it is in the public repository). Mirrors PUBLIC_JWT_SECRETS in
#: backend/core/security.py.
$script:PublicJwtSecrets = @('recallhub-secret-key-change-in-production')

#: Shortest key the backend accepts (MIN_JWT_SECRET_LENGTH).
$script:MinJwtSecretLength = 32

$script:JwtSecretComment = '# Login token signing keys, generated once for this install (installer/scripts/RecallHub-Secrets.psm1). Keep them secret; changing one signs that tenant''s users out.'

function Get-JwtSecretNames {
    <# The .env variable names of the per-tenant signing keys. #>
    return $script:JwtSecretNames
}

function New-JwtSecret {
    <# 32 random bytes as 64 lower-case hex characters, like secrets.token_hex(32). #>
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Test-JwtSecret {
    <# True when Value is a usable signing key: set, not public, long enough. #>
    param([AllowNull()][AllowEmptyString()][string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $false }
    $trimmed = $Value.Trim()
    if ($script:PublicJwtSecrets -contains $trimmed) { return $false }
    return ($trimmed.Length -ge $script:MinJwtSecretLength)
}

# --- .env helpers (keep order and comments, write UTF-8 without BOM) -----------
function Read-EnvFileLines {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return ,[string[]][System.IO.File]::ReadAllLines($Path)
    }
    return ,[string[]]@()
}

function Write-EnvFileLines {
    param([string]$Path, [string[]]$Lines)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, [string[]]$Lines, $utf8)
}

function Get-EnvLinePattern {
    param([string]$Name)
    return ('^\s*(?:export\s+)?' + [regex]::Escape($Name) + '\s*=(.*)$')
}

function ConvertFrom-EnvValue {
    # Strips surrounding quotes the way Compose reads .env values.
    param([string]$Raw)
    $value = $Raw.Trim()
    if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

function Get-EnvLinesValue {
    # Last assignment wins, as in Compose.
    param([string[]]$Lines, [string]$Name)
    $pattern = Get-EnvLinePattern $Name
    $found = $null
    foreach ($line in $Lines) {
        if ($line -match $pattern) { $found = ConvertFrom-EnvValue $Matches[1] }
    }
    return $found
}

function Set-EnvLinesValue {
    # Replaces the first assignment, drops later duplicates, appends when missing.
    param([string[]]$Lines, [string]$Name, [AllowEmptyString()][string]$Value)
    $pattern = Get-EnvLinePattern $Name
    $out = New-Object System.Collections.Generic.List[string]
    $done = $false
    foreach ($line in $Lines) {
        if ($line -match $pattern) {
            if (-not $done) { $out.Add("$Name=$Value"); $done = $true }
            continue
        }
        $out.Add($line)
    }
    if (-not $done) { $out.Add("$Name=$Value") }
    return ,[string[]]$out.ToArray()
}

function Get-EnvFileValue {
    <# The value of Name in an env file, or $null when the file or the key is missing. #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Name)
    return (Get-EnvLinesValue -Lines (Read-EnvFileLines $Path) -Name $Name)
}

# --- signing keys ---------------------------------------------------------------
function Get-JwtSecrets {
    <#
    .SYNOPSIS
        The valid signing keys in an env file, by name.
    .DESCRIPTION
        Capture them before a step overwrites the file and hand them to
        Initialize-JwtSecrets -Preserve afterwards. Keep the result in memory;
        never print or persist it anywhere else.
    #>
    param([Parameter(Mandatory = $true)][string]$EnvPath, [string[]]$Names = $script:JwtSecretNames)
    $found = @{}
    $lines = Read-EnvFileLines $EnvPath
    foreach ($name in $Names) {
        $value = Get-EnvLinesValue -Lines $lines -Name $name
        if (Test-JwtSecret $value) { $found[$name] = $value.Trim() }
    }
    return $found
}

function Initialize-JwtSecrets {
    <#
    .SYNOPSIS
        Makes sure an env file holds a usable signing key for every name.
    .DESCRIPTION
        Per name, in this order: the key from -Preserve (the install's own key,
        captured before a restore overwrote the file), a valid key already in the
        file, a newly generated key. The file is written only when something
        changed; a missing file is created.
    .OUTPUTS
        One object per name with Name and Action (Kept, Restored or Generated).
        Key values are never part of the output.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$EnvPath,
        [string[]]$Names = $script:JwtSecretNames,
        [hashtable]$Preserve = @{}
    )
    if ($null -eq $Preserve) { $Preserve = @{} }
    $lines = Read-EnvFileLines $EnvPath
    $hadAnyName = $false
    foreach ($name in $Names) {
        if ($null -ne (Get-EnvLinesValue -Lines $lines -Name $name)) { $hadAnyName = $true }
    }
    if (-not $hadAnyName) {
        # First keys in this file: introduce them with a comment.
        $header = if ($lines.Count -gt 0) { @('', $script:JwtSecretComment) } else { @($script:JwtSecretComment) }
        $lines = [string[]](@($lines) + $header)
    }

    $changed = -not $hadAnyName
    $results = New-Object System.Collections.Generic.List[object]
    foreach ($name in $Names) {
        $current = Get-EnvLinesValue -Lines $lines -Name $name
        $preserved = if ($Preserve.ContainsKey($name)) { [string]$Preserve[$name] } else { $null }

        if ((Test-JwtSecret $preserved) -and ($null -eq $current -or $current.Trim() -ne $preserved.Trim())) {
            $lines = Set-EnvLinesValue -Lines $lines -Name $name -Value $preserved.Trim()
            $action = 'Restored'
            $changed = $true
        } elseif (Test-JwtSecret $current) {
            $action = 'Kept'
        } else {
            $lines = Set-EnvLinesValue -Lines $lines -Name $name -Value (New-JwtSecret)
            $action = 'Generated'
            $changed = $true
        }
        $results.Add([pscustomobject]@{ Name = $name; Action = $action })
    }

    if ($changed) { Write-EnvFileLines -Path $EnvPath -Lines $lines }
    return $results.ToArray()
}

function Clear-JwtSecrets {
    <#
    .SYNOPSIS
        Blanks the signing keys in an env file (backup packages).
    .OUTPUTS
        The names that held a value (nothing when none did).
    #>
    param([Parameter(Mandatory = $true)][string]$EnvPath, [string[]]$Names = $script:JwtSecretNames)
    $cleared = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) { return }
    $lines = Read-EnvFileLines $EnvPath
    foreach ($name in $Names) {
        $value = Get-EnvLinesValue -Lines $lines -Name $name
        if (-not [string]::IsNullOrEmpty($value)) {
            $lines = Set-EnvLinesValue -Lines $lines -Name $name -Value ''
            $cleared.Add($name)
        }
    }
    if ($cleared.Count -gt 0) { Write-EnvFileLines -Path $EnvPath -Lines $lines }
    return $cleared.ToArray()
}

Export-ModuleMember -Function @(
    'Get-JwtSecretNames',
    'New-JwtSecret',
    'Test-JwtSecret',
    'Get-EnvFileValue',
    'Get-JwtSecrets',
    'Initialize-JwtSecrets',
    'Clear-JwtSecrets'
)
