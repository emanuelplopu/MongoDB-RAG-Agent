<#
.SYNOPSIS
    Attach (or detach) an SMB/CIFS network share as a profile document folder.
.DESCRIPTION
    Network folders of the office (NAS, file server) have to be visible inside
    the API and ingestion-worker containers before a profile can list them.
    Docker Desktop's VM mounts SMB shares itself (CIFS volumes), so this script:

      1. tests the share with a throwaway Docker volume (skip with -SkipTest),
      2. stores the share in <install>\.env as SHARE_<NAME>_UNC / _USER /
         _PASSWORD / _OPTIONS / _TENANT (the password only lives there),
      3. creates the empty mount point <install>\mounts\<name> (the volume is
         mounted over it inside the read-only /app/mounts bind mount) and
         regenerates <install>\docker-compose.shares.yml from those entries:
         one CIFS volume per share, attached read-only at /app/mounts/<name>
         in the tenant backend(s) and the ingestion worker,
      4. keeps MOUNT_MAPPINGS (for "Open in Explorer") and COMPOSE_FILE in .env
         in step, and
      5. runs "docker compose up -d" so the containers pick the share up
         (skip with -NoRestart).

    Afterwards the folder appears in the Profiles page folder picker under
    /app/mounts/<name>, where an organisation admin adds it (or any sub-folder)
    to the profile. See docs/22-DOCUMENT_FOLDERS_AND_SHARES.md.

    Runs with PowerShell 5.1, no administrator rights needed. The install path
    is the folder holding docker-compose.yml: the parent of this scripts folder
    in an installation, the repository root in a checkout, or -InstallPath.
.PARAMETER Name
    Short name of the share: lower-case letters, digits and "-" (first char a
    letter or digit). Becomes /app/mounts/<Name> and SHARE_<NAME>_* in .env.
.PARAMETER UncPath
    \\server\share or //server/share (a sub-folder of the share is fine:
    \\nas\Daten\Kanzlei). IP addresses work when name resolution is unreliable.
.PARAMETER Username
    Account on the file server. Use a dedicated read-only account.
.PARAMETER Password
    Password as a SecureString; prompted when omitted. Must not contain ","
    (the CIFS mount option separator).
.PARAMETER Domain
    Active Directory domain of the account, when it is a domain account.
.PARAMETER SmbVersion
    SMB dialect for the mount: 3.1.1 (default), 3.0, 2.1.
.PARAMETER Tenant
    Which backend gets the share besides the ingestion worker:
    quellex, recallhub or both (default).
.PARAMETER Remove
    Detach the share: delete its .env entries and its volume, regenerate the
    compose file and restart. Profiles that still list the folder keep the
    entry; the Profiles page shows it as not reachable.
.PARAMETER SkipTest
    Do not test-mount the share before writing the configuration.
.PARAMETER NoRestart
    Only write the configuration; do not run "docker compose up -d".
.EXAMPLE
    .\Add-NetworkShare.ps1 -Name kanzlei -UncPath \\nas.diejuristin\Kanzlei -Username svc-recallhub -Tenant quellex
.EXAMPLE
    .\Add-NetworkShare.ps1 -Name kanzlei -Remove
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,39}$')]
    [string]$Name,
    [string]$UncPath = "",
    [string]$Username = "",
    [System.Security.SecureString]$Password = $null,
    [string]$Domain = "",
    [ValidateSet("3.1.1", "3.0", "2.1")]
    [string]$SmbVersion = "3.1.1",
    [ValidateSet("quellex", "recallhub", "both")]
    [string]$Tenant = "both",
    [string]$InstallPath = "",
    [switch]$Remove,
    [switch]$SkipTest,
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Locate the install (the folder with docker-compose.yml) --------------------
function Resolve-InstallPath {
    param([string]$Explicit, [string]$ScriptDirectory)
    $candidates = @()
    if ($Explicit) { $candidates += $Explicit }
    $candidates += (Split-Path $ScriptDirectory -Parent)                     # <install>\scripts
    $candidates += (Split-Path (Split-Path $ScriptDirectory -Parent) -Parent) # <repo>\installer\scripts
    foreach ($c in $candidates) {
        if ($c -and (Test-Path (Join-Path $c "docker-compose.yml"))) { return (Resolve-Path $c).Path }
    }
    throw "docker-compose.yml not found next to this install; pass -InstallPath <folder>"
}

# --- .env helpers (keeps order and comments, writes UTF-8 without BOM) ----------
function Read-EnvLines {
    param([string]$Path)
    if (Test-Path $Path) { return @([System.IO.File]::ReadAllLines($Path)) }
    return @()
}

function Write-EnvLines {
    param([string]$Path, [string[]]$Lines)
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllLines($Path, [string[]]$Lines, $utf8)
}

function Get-EnvValue {
    param([string[]]$Lines, [string]$Key)
    foreach ($line in $Lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '\s*=(.*)$')) { return $Matches[1].Trim() }
    }
    return $null
}

function Set-EnvValue {
    # Replaces the key in place or appends it; returns the new line array.
    param([string[]]$Lines, [string]$Key, [string]$Value)
    $out = New-Object System.Collections.Generic.List[string]
    $done = $false
    foreach ($line in $Lines) {
        if (-not $done -and $line -match ('^\s*' + [regex]::Escape($Key) + '\s*=')) {
            $out.Add("$Key=$Value"); $done = $true
        } else { $out.Add($line) }
    }
    if (-not $done) { $out.Add("$Key=$Value") }
    return $out.ToArray()
}

function Remove-EnvKey {
    param([string[]]$Lines, [string]$Key)
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($line in $Lines) {
        if ($line -notmatch ('^\s*' + [regex]::Escape($Key) + '\s*=')) { $out.Add($line) }
    }
    return $out.ToArray()
}

function Get-ShareNames {
    # Every NAME with a SHARE_<NAME>_UNC entry, as written to .env (upper case).
    param([string[]]$Lines)
    $names = New-Object System.Collections.Generic.List[string]
    foreach ($line in $Lines) {
        if ($line -match '^\s*SHARE_([A-Z0-9_]+)_UNC\s*=') { if (-not $names.Contains($Matches[1])) { $names.Add($Matches[1]) } }
    }
    return $names.ToArray()
}

function ConvertTo-EnvKeyName { param([string]$ShareName) return $ShareName.ToUpperInvariant().Replace("-", "_") }

function ConvertTo-MountName {
    # Reverse of ConvertTo-EnvKeyName for the container path; stored explicitly to be safe.
    param([string[]]$Lines, [string]$KeyName)
    $stored = Get-EnvValue -Lines $Lines -Key "SHARE_${KeyName}_NAME"
    if ($stored) { return $stored }
    return $KeyName.ToLowerInvariant().Replace("_", "-")
}

function ConvertTo-CifsDevice {
    param([string]$Path)
    $p = $Path.Trim().Replace("\", "/")
    if ($p -notmatch '^//[^/]+/.+') { throw "UncPath must look like \\server\share (got '$Path')" }
    return $p.TrimEnd("/")
}

function ConvertTo-UncPath { param([string]$Device) return $Device.Replace("/", "\") }

function ConvertFrom-SecureStringPlain {
    param([System.Security.SecureString]$Secure)
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
}

# --- Compose file generation -----------------------------------------------------
function New-SharesCompose {
    param([string[]]$Lines)
    $names = Get-ShareNames -Lines $Lines
    if ($names.Count -eq 0) { return $null }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine("# Generated by installer/scripts/Add-NetworkShare.ps1 - do not edit by hand.")
    [void]$sb.AppendLine("# Network shares as profile document folders; credentials and options live in .env")
    [void]$sb.AppendLine("# (SHARE_<NAME>_*). Attached read-only at /app/mounts/<name> in the backend(s) and")
    [void]$sb.AppendLine("# the ingestion worker. See docs/22-DOCUMENT_FOLDERS_AND_SHARES.md.")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("volumes:")
    $byService = @{ "backend" = @(); "backend-quellex" = @(); "ingestion-worker" = @() }
    foreach ($key in $names) {
        $mount = ConvertTo-MountName -Lines $Lines -KeyName $key
        $tenantValue = Get-EnvValue -Lines $Lines -Key "SHARE_${key}_TENANT"
        if (-not $tenantValue) { $tenantValue = "both" }
        $options = Get-EnvValue -Lines $Lines -Key "SHARE_${key}_OPTIONS"
        if (-not $options) { $options = "vers=3.1.1" }
        $volume = "share_" + $key.ToLowerInvariant()
        [void]$sb.AppendLine("  ${volume}:")
        [void]$sb.AppendLine("    driver: local")
        [void]$sb.AppendLine("    driver_opts:")
        [void]$sb.AppendLine("      type: cifs")
        [void]$sb.AppendLine("      device: `${SHARE_${key}_UNC}")
        [void]$sb.AppendLine("      o: `"username=`${SHARE_${key}_USER},password=`${SHARE_${key}_PASSWORD},${options},ro,noperm,iocharset=utf8,file_mode=0444,dir_mode=0555`"")
        $entry = "      - ${volume}:/app/mounts/${mount}:ro"
        $byService["ingestion-worker"] += $entry
        if ($tenantValue -eq "quellex" -or $tenantValue -eq "both") { $byService["backend-quellex"] += $entry }
        if ($tenantValue -eq "recallhub" -or $tenantValue -eq "both") { $byService["backend"] += $entry }
    }
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("services:")
    foreach ($service in @("backend", "backend-quellex", "ingestion-worker")) {
        if ($byService[$service].Count -eq 0) { continue }
        [void]$sb.AppendLine("  ${service}:")
        [void]$sb.AppendLine("    volumes:")
        foreach ($entry in $byService[$service]) { [void]$sb.AppendLine($entry) }
    }
    return $sb.ToString()
}

function Update-MountMappings {
    # /app/mounts/<name>=\\server\share for every share. Entries for other
    # container paths (hand-written local folder mappings) are kept; the entry
    # of a share being removed is dropped.
    param([string[]]$Lines, [string[]]$DropPaths = @())
    $names = Get-ShareNames -Lines $Lines
    $shareMounts = @{}
    foreach ($key in $names) {
        $mount = ConvertTo-MountName -Lines $Lines -KeyName $key
        $device = Get-EnvValue -Lines $Lines -Key "SHARE_${key}_UNC"
        $shareMounts["/app/mounts/$mount"] = ConvertTo-UncPath $device
    }
    $existing = Get-EnvValue -Lines $Lines -Key "MOUNT_MAPPINGS"
    $kept = New-Object System.Collections.Generic.List[string]
    if ($existing) {
        foreach ($pair in ($existing -split '\|')) {
            $p = $pair.Trim()
            if (-not $p) { continue }
            $containerPath = ($p -split '=', 2)[0].Trim()
            if ($shareMounts.ContainsKey($containerPath)) { continue }   # rewritten below
            if ($DropPaths -contains $containerPath) { continue }       # share being removed
            $kept.Add($p)
        }
    }
    foreach ($k in ($shareMounts.Keys | Sort-Object)) { $kept.Add("$k=" + $shareMounts[$k]) }
    if ($kept.Count -eq 0) { return (Remove-EnvKey -Lines $Lines -Key "MOUNT_MAPPINGS") }
    return (Set-EnvValue -Lines $Lines -Key "MOUNT_MAPPINGS" -Value ($kept -join "|"))
}

function Update-ComposeFileList {
    # COMPOSE_FILE so a plain "docker compose up -d" loads the same files as the
    # installer scripts. A custom list that we did not write is left alone.
    param([string[]]$Lines, [string]$Install, [bool]$HasShares)
    $files = @("docker-compose.yml")
    if (Test-Path (Join-Path $Install "docker-compose.override.yml")) { $files += "docker-compose.override.yml" }
    $managed = ($files -join ";")
    $withShares = (($files + "docker-compose.shares.yml") -join ";")
    $current = Get-EnvValue -Lines $Lines -Key "COMPOSE_FILE"
    $ours = (-not $current) -or ($current -eq $managed) -or ($current -eq $withShares) -or ($current -like "*docker-compose.shares.yml*")
    if (-not $ours) {
        Write-Host "  [WARN] COMPOSE_FILE in .env is customised ($current); add docker-compose.shares.yml to it yourself." -ForegroundColor Yellow
        return $Lines
    }
    if ($HasShares) { return (Set-EnvValue -Lines $Lines -Key "COMPOSE_FILE" -Value $withShares) }
    if ($current) { return (Remove-EnvKey -Lines $Lines -Key "COMPOSE_FILE") }
    return $Lines
}

# --- Share test with a throwaway volume ------------------------------------------
function Test-ShareMount {
    param([string]$Device, [string]$User, [string]$PlainPassword, [string]$Options)
    $volume = "recallhub-sharetest-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $o = "username=$User,password=$PlainPassword,$Options,ro,noperm,iocharset=utf8"
    Write-Host "  Testing the share with a temporary Docker volume ..." -ForegroundColor Gray
    $create = & docker volume create --driver local --opt type=cifs --opt "device=$Device" --opt "o=$o" $volume 2>&1
    if ($LASTEXITCODE -ne 0) { throw "docker volume create failed: $create" }
    try {
        $listing = & docker run --rm -v "${volume}:/mnt:ro" alpine sh -c "ls -A /mnt | head -n 5; echo; ls -A /mnt | wc -l" 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Mounting the share failed: $($listing -join ' '). Check server name, share path, account, password (no ',' allowed) and SMB version (-SmbVersion 3.0 or 2.1 for older servers)."
        }
        $lines = @($listing | Where-Object { $_ -ne "" })
        $count = $lines[$lines.Count - 1]
        Write-Host "  Share is reachable: $count top-level entries" -ForegroundColor Green
    } finally {
        & docker volume rm $volume 2>&1 | Out-Null
    }
}

# =================================================================================
$install = Resolve-InstallPath -Explicit $InstallPath -ScriptDirectory $ScriptDir
$envPath = Join-Path $install ".env"
$sharesPath = Join-Path $install "docker-compose.shares.yml"
$keyName = ConvertTo-EnvKeyName $Name
$lines = Read-EnvLines -Path $envPath

Write-Host ""
Write-Host "RecallHub network share: $Name  ($install)" -ForegroundColor Cyan

$dropMountPaths = @()
if ($Remove) {
    if (-not (Get-EnvValue -Lines $lines -Key "SHARE_${keyName}_UNC")) {
        Write-Host "  [INFO] No share named '$Name' in .env; nothing to remove." -ForegroundColor Yellow
        exit 0
    }
    $dropMountPaths = @("/app/mounts/" + (ConvertTo-MountName -Lines $lines -KeyName $keyName))
    foreach ($suffix in @("NAME", "UNC", "USER", "PASSWORD", "OPTIONS", "TENANT")) {
        $lines = Remove-EnvKey -Lines $lines -Key "SHARE_${keyName}_${suffix}"
    }
} else {
    if (-not $UncPath) { throw "-UncPath is required (\\server\share)" }
    if (-not $Username) { throw "-Username is required" }
    $device = ConvertTo-CifsDevice $UncPath
    if ($null -eq $Password) { $Password = Read-Host -Prompt "  Password for $Username on $UncPath" -AsSecureString }
    $plain = ConvertFrom-SecureStringPlain $Password
    if (-not $plain) { throw "The password must not be empty" }
    if ($plain.Contains(",")) { throw "The password must not contain ',' (CIFS mount option separator); use another account or change the password" }
    if ($Username.Contains(",") -or $Domain.Contains(",")) { throw "User name and domain must not contain ','" }
    if ($plain -match '[\r\n]' -or $plain.Contains('$')) { throw "The password must not contain line breaks or '$' (compose interpolation)" }

    $options = "vers=$SmbVersion"
    if ($Domain) { $options += ",domain=$Domain" }

    if (-not $SkipTest) { Test-ShareMount -Device $device -User $Username -PlainPassword $plain -Options $options }

    # The volume is mounted at /app/mounts/<name> on top of the read-only bind
    # mount of <install>\mounts, so the mount point must already exist on the
    # host or the container fails to start ("mkdir ... read-only file system").
    $mountPoint = Join-Path $install "mounts\$Name"
    if (-not (Test-Path $mountPoint)) {
        New-Item -ItemType Directory -Path $mountPoint -Force | Out-Null
        Write-Host "  Created mount point $mountPoint (stays empty; the share is mounted over it)" -ForegroundColor Gray
    }

    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_NAME" -Value $Name
    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_UNC" -Value $device
    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_USER" -Value $Username
    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_PASSWORD" -Value $plain
    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_OPTIONS" -Value $options
    $lines = Set-EnvValue -Lines $lines -Key "SHARE_${keyName}_TENANT" -Value $Tenant
}

$compose = New-SharesCompose -Lines $lines
$lines = Update-MountMappings -Lines $lines -DropPaths $dropMountPaths
$lines = Update-ComposeFileList -Lines $lines -Install $install -HasShares ($null -ne $compose)
Write-EnvLines -Path $envPath -Lines $lines

if ($null -ne $compose) {
    [System.IO.File]::WriteAllText($sharesPath, $compose, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "  Wrote $sharesPath" -ForegroundColor Green
} elseif (Test-Path $sharesPath) {
    Remove-Item $sharesPath -Force
    Write-Host "  Removed $sharesPath (no shares left)" -ForegroundColor Green
}

if ($Remove) {
    $volumeName = "recallhub_share_" + $keyName.ToLowerInvariant()
    Write-Host "  The Docker volume $volumeName is dropped by 'docker compose up -d' once no service uses it; remove it with 'docker volume rm $volumeName' if it lingers." -ForegroundColor Gray
}

if ($NoRestart) {
    Write-Host "  Configuration written. Start or restart the services to apply it (Start-Services.ps1 / docker compose up -d)." -ForegroundColor Yellow
    exit 0
}

Write-Host "  Applying with docker compose up -d ..." -ForegroundColor Gray
Push-Location $install
try {
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw "docker compose up -d failed (exit $LASTEXITCODE); the share settings are in .env and docker-compose.shares.yml" }
} finally { Pop-Location }

if (-not $Remove) {
    Write-Host ""
    Write-Host "  Done. The share is /app/mounts/$Name inside the containers." -ForegroundColor Green
    Write-Host "  In the Profiles page an organisation admin can now add it (or a sub-folder) to the profile;" -ForegroundColor Green
    Write-Host "  the next ingestion run indexes it." -ForegroundColor Green
}
