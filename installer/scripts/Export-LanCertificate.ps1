<#
.SYNOPSIS
    Builds the trust package other computers need for https://quellex.local (no certificate warning).
.DESCRIPTION
    The Caddy gateway (gateway-quellex / gateway service in docker-compose.yml)
    signs its certificates with its own local CA, stored in the gateway's Docker
    volume. Browsers on other computers warn until that root certificate is in
    their trust store. This script exports the root certificate (the private key
    never leaves the container) and writes a self-contained package, meant for a
    read-only network share:

      Quellex-LAN-Root-CA.crt     the root certificate (DER; opens on Windows, macOS, iOS, Android)
      Install-Quellex-Trust.cmd   Windows: double-click, confirm the UAC prompt, done
      Install-Quellex-Trust.ps1   the installer itself - verifies the certificate's SHA-1 fingerprint
                                  against the value embedded here before importing it machine-wide,
                                  and lets Firefox use the Windows store (ImportEnterpriseRoots policy)
      Uninstall-Quellex-Trust.cmd removes the certificate again
      Quellex-LAN-Trust.mobileconfig  Apple configuration profile for iPhone / iPad / Mac (one tap)
      README.txt                  instructions (German and English) and the fingerprint to compare

    The package is tied to the exported certificate by its fingerprint; a swapped
    .crt is refused by the installer. Keep the share read-only for staff.
.PARAMETER Tenant
    recallhub | quellex. Defaults to the tenant in %LocalAppData%\RecallHub\config.json.
.PARAMETER PackagePath
    Folder (or UNC share path) that receives the package. Default: Desktop\<App>-Trust.
.PARAMETER OutputPath
    Legacy: write only the .crt file to this path instead of a package.
.PARAMETER TrustOnThisComputer
    Also import the certificate into this user's Trusted Root store (Windows
    shows a confirmation dialog), so browsers on this PC open the address too.
.EXAMPLE
    .\Export-LanCertificate.ps1 -PackagePath "\\NAS\IT\Quellex-Trust"
.EXAMPLE
    .\Export-LanCertificate.ps1 -TrustOnThisComputer
#>
param(
    [ValidateSet("", "recallhub", "quellex")][string]$Tenant = "",
    [string]$PackagePath = "",
    [string]$OutputPath = "",
    [switch]$TrustOnThisComputer
)

$ErrorActionPreference = "Stop"

# --- Resolve tenant / names -------------------------------------------------
$configPath = Join-Path (Join-Path $env:LOCALAPPDATA "RecallHub") "config.json"
$config = $null
if (Test-Path $configPath) {
    try { $config = Get-Content $configPath -Raw | ConvertFrom-Json } catch { $config = $null }
}
if (-not $Tenant -and $config -and $config.app -and $config.app.tenant) { $Tenant = [string]$config.app.tenant }
if (-not $Tenant) { $Tenant = "recallhub" }

$appName   = if ($Tenant -eq "quellex") { "Quellex" } else { "RecallHub" }
$container = if ($Tenant -eq "quellex") { "rag-gateway-quellex" } else { "rag-gateway" }
$volume    = if ($Tenant -eq "quellex") { "recallhub_gateway_quellex_data" } else { "recallhub_gateway_data" }
$certInContainer = "/data/caddy/pki/authorities/local/root.crt"
$hostname  = if ($config -and $config.network -and $config.network.hostname) { [string]$config.network.hostname } else { $Tenant }
$httpsPort = if ($config -and $config.network -and $config.network.httpsPort) { [int]$config.network.httpsPort } elseif ($Tenant -eq "quellex") { 443 } else { 11443 }
$url       = if ($httpsPort -eq 443) { "https://$hostname.local" } else { "https://$hostname.local:$httpsPort" }
$certFileName = "$appName-LAN-Root-CA.crt"

Write-Host ""
Write-Host "  $appName trust package (root certificate of the LAN gateway)" -ForegroundColor Cyan

# --- Get the root certificate out of the gateway ----------------------------
$null = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [ERROR] Docker is not running. Start Docker Desktop (or the $appName tray app) and retry." -ForegroundColor Red
    exit 1
}

$tempPem = Join-Path ([IO.Path]::GetTempPath()) ("lan-root-ca-" + [guid]::NewGuid().ToString("N") + ".pem")
$exported = $false
$containers = @(docker ps -a --format "{{.Names}}" 2>$null)
if ($containers -contains $container) {
    docker cp "${container}:${certInContainer}" $tempPem 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $tempPem)) { $exported = $true }
}
if (-not $exported) {
    $volumes = @(docker volume ls --format "{{.Name}}" 2>$null)
    if ($volumes -contains $volume) {
        $pem = docker run --rm -v "${volume}:/data:ro" caddy:2.11-alpine cat $certInContainer 2>$null
        if ($LASTEXITCODE -eq 0 -and $pem) {
            [IO.File]::WriteAllText($tempPem, (($pem -join "`n") + "`n"))
            $exported = $true
        }
    }
}
if (-not $exported) {
    Write-Host "  [ERROR] Certificate not found. The gateway has to start once to create its CA:" -ForegroundColor Red
    Write-Host "          docker compose --profile $Tenant up -d $(if ($Tenant -eq 'quellex') { 'gateway-quellex' } else { 'gateway' })" -ForegroundColor Gray
    exit 2
}

$cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($tempPem)
Remove-Item $tempPem -Force -ErrorAction SilentlyContinue
$der = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$thumbprint = $cert.Thumbprint.ToUpperInvariant()
$fingerprintPretty = ($thumbprint -replace '(..)(?!$)', '$1:')
$validTo = $cert.NotAfter.ToString("yyyy-MM-dd")

Write-Host "  [OK] Root certificate: $($cert.Subject)" -ForegroundColor Green
Write-Host "       Valid to:    $validTo" -ForegroundColor Gray
Write-Host "       Fingerprint: $fingerprintPretty (SHA-1)" -ForegroundColor Gray

# --- Legacy single-file mode -------------------------------------------------
if ($OutputPath) {
    $outDir = Split-Path -Parent $OutputPath
    if ($outDir -and -not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }
    [IO.File]::WriteAllBytes($OutputPath, $der)
    Write-Host "  [OK] Written: $OutputPath" -ForegroundColor Green
}

# --- Package -----------------------------------------------------------------
if (-not $OutputPath -or $PackagePath) {
    if (-not $PackagePath) { $PackagePath = Join-Path ([Environment]::GetFolderPath("Desktop")) "$appName-Trust" }
    if (-not (Test-Path $PackagePath)) { New-Item -ItemType Directory -Path $PackagePath -Force | Out-Null }

    [IO.File]::WriteAllBytes((Join-Path $PackagePath $certFileName), $der)

    # ---- Windows installer (fingerprint-pinned, self-elevating) ----
    $installer = @'
<#
  __APP__ trust installer - makes __URL__ a trusted site on this computer.

  Run via Install-__APP__-Trust.cmd (double-click). What it does:
    1. Reads __CERT__ next to this script and refuses it unless its SHA-1
       fingerprint is exactly __FP__ (the value printed when the package was created).
    2. Imports it into "Trusted Root Certification Authorities" for the whole
       computer (asks for administrator rights once). Without admin rights it
       falls back to the current user's store (Windows then shows a confirmation).
    3. Tells Firefox to use the Windows store (policy ImportEnterpriseRoots), so
       Firefox trusts the site as well.
  -Remove undoes steps 2 and 3. -VerifyOnly only performs the fingerprint check.
#>
param(
    [switch]$Remove,
    [switch]$VerifyOnly,
    [switch]$NoElevate,
    [string]$LogPath = ""
)
$ErrorActionPreference = "Stop"
$expected = "__THUMBPRINT__"
$appName  = "__APP__"
$url      = "__URL__"
$certFile = Join-Path $PSScriptRoot "__CERT__"

# Every message also goes to $LogPath when set: the elevated step runs in a
# second window that closes immediately, and the launching window prints the
# log afterwards so the result is never hidden.
function Say([string]$text, [string]$color = "Gray") {
    Write-Host $text -ForegroundColor $color
    if ($LogPath) { try { Add-Content -Path $LogPath -Value $text -Encoding UTF8 } catch { } }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Installed([string]$storeName) {
    # Read the store the same way Windows and Chrome do.
    $location = if ($storeName -eq "LocalMachine") { [Security.Cryptography.X509Certificates.StoreLocation]::LocalMachine } else { [Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser }
    $store = New-Object Security.Cryptography.X509Certificates.X509Store("Root", $location)
    try {
        $store.Open([Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)
        return (@($store.Certificates | Where-Object { $_.Thumbprint -eq $expected }).Count -gt 0)
    } finally { $store.Close() }
}

if (-not $LogPath) {
    Write-Host ""
    Write-Host "  $appName - trusted certificate for $url" -ForegroundColor Cyan
    Write-Host ""
}

if (-not $Remove) {
    if (-not (Test-Path $certFile)) {
        Say "  [ERROR] $certFile is missing. Copy the whole package folder, not just this script." "Red"
        exit 2
    }
    $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certFile)
    $actual = $cert.Thumbprint.ToUpperInvariant()
    if ($actual -ne $expected) {
        Say "  [ERROR] The certificate file does not match this package." "Red"
        Say "          expected fingerprint $expected" "Red"
        Say "          file has fingerprint $actual" "Red"
        Say "          Nothing was installed. Ask your administrator for a fresh package." "Red"
        exit 3
    }
    if (-not $LogPath) {
        Say "  [OK] Certificate verified: $($cert.Subject)" "Green"
        Say "       Fingerprint $(($actual -replace '(..)(?!$)', '$1:'))" "Gray"
    }
    if ($VerifyOnly) { exit 0 }
}

$isAdmin = Test-Admin
if (-not $isAdmin -and -not $NoElevate) {
    Write-Host "  Requesting administrator rights (installs for every user of this PC)..." -ForegroundColor Yellow
    $log = Join-Path ([IO.Path]::GetTempPath()) ("$appName-trust-" + [guid]::NewGuid().ToString("N") + ".log")
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-NoElevate", "-LogPath", "`"$log`"")
    if ($Remove) { $args += "-Remove" }
    $elevated = $false
    try {
        $p = Start-Process -FilePath "powershell.exe" -ArgumentList $args -Verb RunAs -Wait -PassThru
        $elevated = $true
        if (Test-Path $log) { Get-Content $log | ForEach-Object { Write-Host $_ -ForegroundColor $(if ($_ -match "\[ERROR\]") { "Red" } elseif ($_ -match "\[WARN\]") { "Yellow" } elseif ($_ -match "\[OK\]") { "Green" } else { "Gray" }) }; Remove-Item $log -Force -ErrorAction SilentlyContinue }
        else { Write-Host "  [WARN] The administrator step left no log; it may have been closed before it ran." -ForegroundColor Yellow }
        if ($p.ExitCode -ne 0) { Write-Host "  [ERROR] The administrator step failed (exit code $($p.ExitCode))." -ForegroundColor Red; exit $p.ExitCode }
    } catch {
        Write-Host "  Administrator rights were declined - installing for the current user only." -ForegroundColor Yellow
    }
    if ($elevated) {
        if (-not $Remove) {
            if (Test-Installed "LocalMachine") { Write-Host "  [OK] Verified: the certificate is in the computer's Trusted Root store." -ForegroundColor Green }
            else { Write-Host "  [ERROR] Verification failed: the certificate is NOT in the computer's Trusted Root store." -ForegroundColor Red; exit 4 }
            Write-Host ""
            Write-Host "  Done. Close the browser COMPLETELY (all windows; Chrome: type chrome://restart) and open $url" -ForegroundColor Cyan
        }
        exit 0
    }
}

$firefoxPolicy = "HKLM:\SOFTWARE\Policies\Mozilla\Firefox\Certificates"
if ($Remove) {
    $removed = 0
    foreach ($store in @("Cert:\LocalMachine\Root", "Cert:\CurrentUser\Root")) {
        try {
            Get-ChildItem $store | Where-Object { $_.Thumbprint -eq $expected } | ForEach-Object { Remove-Item $_.PSPath -Force; $removed++ }
        } catch { }
    }
    if ($isAdmin -and (Test-Path $firefoxPolicy)) {
        try { Remove-ItemProperty -Path $firefoxPolicy -Name "ImportEnterpriseRoots" -ErrorAction SilentlyContinue } catch { }
    }
    Say "  [OK] Removed $removed certificate entr$(if ($removed -eq 1) { 'y' } else { 'ies' }). $url will show a warning again." "Green"
    exit 0
}

$storeName = if ($isAdmin) { "LocalMachine" } else { "CurrentUser" }
if (Test-Installed $storeName) {
    Say "  [OK] Already trusted on this computer ($storeName\Root)." "Green"
} else {
    # certutil exists on every Windows edition; the -user variant shows the
    # Windows confirmation dialog, the machine variant needs the admin rights we have.
    $certutilArgs = if ($isAdmin) { @("-addstore", "-f", "Root", $certFile) } else { @("-user", "-addstore", "-f", "Root", $certFile) }
    $out = & certutil.exe @certutilArgs 2>&1
    if ($LASTEXITCODE -ne 0 -or -not (Test-Installed $storeName)) {
        Say "  [ERROR] certutil could not install the certificate (exit $LASTEXITCODE):" "Red"
        foreach ($line in $out) { Say "          $line" "Red" }
        exit 5
    }
    Say "  [OK] Installed into $storeName\Root (verified)." "Green"
}

if ($isAdmin) {
    try {
        if (-not (Test-Path $firefoxPolicy)) { New-Item -Path $firefoxPolicy -Force | Out-Null }
        New-ItemProperty -Path $firefoxPolicy -Name "ImportEnterpriseRoots" -Value 1 -PropertyType DWord -Force | Out-Null
        Say "  [OK] Firefox will use the Windows certificate store (policy ImportEnterpriseRoots)." "Green"
    } catch {
        Say "  [WARN] Firefox policy not set: $_" "Yellow"
    }
}

if (-not $LogPath) {
    Write-Host ""
    Write-Host "  Done. Close the browser COMPLETELY (all windows; Chrome: type chrome://restart) and open $url" -ForegroundColor Cyan
}
exit 0
'@
    $installer = $installer.Replace("__APP__", $appName).Replace("__URL__", $url).Replace("__CERT__", $certFileName).Replace("__THUMBPRINT__", $thumbprint).Replace("__FP__", $fingerprintPretty)
    [IO.File]::WriteAllText((Join-Path $PackagePath "Install-$appName-Trust.ps1"), $installer, [Text.UTF8Encoding]::new($true))

    $cmdInstall = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0Install-$appName-Trust.ps1`"`r`necho.`r`npause`r`n"
    [IO.File]::WriteAllText((Join-Path $PackagePath "Install-$appName-Trust.cmd"), $cmdInstall, [Text.ASCIIEncoding]::new())
    $cmdRemove = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0Install-$appName-Trust.ps1`" -Remove`r`necho.`r`npause`r`n"
    [IO.File]::WriteAllText((Join-Path $PackagePath "Uninstall-$appName-Trust.cmd"), $cmdRemove, [Text.ASCIIEncoding]::new())

    # ---- Apple configuration profile (iPhone / iPad / Mac) ----
    $b64 = [Convert]::ToBase64String($der, [Base64FormattingOptions]::InsertLineBreaks)
    $profile = @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadCertificateFileName</key>
      <string>$certFileName</string>
      <key>PayloadContent</key>
      <data>
$b64
      </data>
      <key>PayloadDescription</key>
      <string>Root certificate of the $appName office gateway ($url)</string>
      <key>PayloadDisplayName</key>
      <string>$appName LAN Root CA</string>
      <key>PayloadIdentifier</key>
      <string>at.quellex.lan.rootca.$($thumbprint.Substring(0, 8).ToLower())</string>
      <key>PayloadType</key>
      <string>com.apple.security.root</string>
      <key>PayloadUUID</key>
      <string>$([guid]::NewGuid().ToString().ToUpper())</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
    </dict>
  </array>
  <key>PayloadDescription</key>
  <string>Trusts $url on this device. Fingerprint (SHA-1): $fingerprintPretty</string>
  <key>PayloadDisplayName</key>
  <string>$appName LAN trust</string>
  <key>PayloadIdentifier</key>
  <string>at.quellex.lan.trust.$($thumbprint.Substring(0, 8).ToLower())</string>
  <key>PayloadOrganization</key>
  <string>$appName</string>
  <key>PayloadRemovalDisallowed</key>
  <false/>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>$([guid]::NewGuid().ToString().ToUpper())</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
</dict>
</plist>
"@
    [IO.File]::WriteAllText((Join-Path $PackagePath "$appName-LAN-Trust.mobileconfig"), $profile, [Text.UTF8Encoding]::new($false))

    # ---- README (German first, then English) ----
    $readme = @"
$appName - Zertifikat fuer $url  /  certificate for $url
======================================================================

Fingerabdruck / fingerprint (SHA-1):  $fingerprintPretty
Gueltig bis / valid until:            $validTo
Erstellt / created:                   $(Get-Date -Format "yyyy-MM-dd HH:mm")

DEUTSCH
-------
Damit der Browser $url ohne Warnung oeffnet, muss jeder Computer das
Stammzertifikat des $appName-Servers einmalig als vertrauenswuerdig einstufen.
Das Zertifikat enthaelt nur einen oeffentlichen Schluessel; es kann nichts
entschluesseln und gilt nur fuer $url.

Windows (einmal pro PC):
  1. Install-$appName-Trust.cmd doppelklicken.
  2. Die Windows-Abfrage "Aenderungen zulassen?" mit Ja bestaetigen.
  3. Browser komplett schliessen und $url oeffnen.
  Das Skript prueft vor der Installation den Fingerabdruck (siehe oben) und
  installiert nichts, wenn er nicht uebereinstimmt.
  Entfernen: Uninstall-$appName-Trust.cmd.

iPhone / iPad / Mac:
  $appName-LAN-Trust.mobileconfig oeffnen (per Mail oder AirDrop senden),
  Profil installieren. Auf iPhone/iPad danach: Einstellungen > Allgemein >
  Info > Zertifikatsvertrauenseinstellungen > "$appName LAN Root CA" aktivieren.
  Mac: Schluesselbundverwaltung > Zertifikat > Vertrauen > "Immer vertrauen".

Android:
  $certFileName oeffnen, als "CA-Zertifikat" installieren
  (Einstellungen > Sicherheit > Verschluesselung & Anmeldedaten > Zertifikat installieren).

Firefox:
  Wird vom Windows-Installer mit abgedeckt (Richtlinie ImportEnterpriseRoots).
  Sonst: Einstellungen > Datenschutz & Sicherheit > Zertifikate > Importieren.

ENGLISH
-------
Each computer has to trust the $appName server's root certificate once so the
browser opens $url without a warning. The certificate holds only a public key;
it cannot decrypt anything and is only valid for $url.

Windows (once per PC):  double-click Install-$appName-Trust.cmd, confirm the
  administrator prompt, close the browser completely and open $url.
  The script verifies the fingerprint above before installing anything.
  Remove again with Uninstall-$appName-Trust.cmd.
iPhone / iPad / Mac:    open $appName-LAN-Trust.mobileconfig and install the
  profile; on iPhone/iPad also enable it under Settings > General > About >
  Certificate Trust Settings. Mac: Keychain Access > certificate > Trust > Always Trust.
Android:                open $certFileName and install it as a CA certificate.
Firefox:                covered by the Windows installer (ImportEnterpriseRoots
  policy); otherwise import it under Settings > Privacy & Security > Certificates.

Sicherheitshinweis / security note:
  Dieses Paket nur von einer schreibgeschuetzten Freigabe oder direkt vom
  Administrator beziehen und den Fingerabdruck vergleichen.
  Only take this package from a read-only share or directly from the
  administrator, and compare the fingerprint.
"@
    [IO.File]::WriteAllText((Join-Path $PackagePath "README.txt"), $readme, [Text.UTF8Encoding]::new($true))

    Write-Host "  [OK] Trust package written to $PackagePath" -ForegroundColor Green
    Get-ChildItem $PackagePath | ForEach-Object { Write-Host "       $($_.Name)" -ForegroundColor Gray }
    Write-Host ""
    Write-Host "  Put the folder on a read-only share. On each Windows PC: double-click Install-$appName-Trust.cmd." -ForegroundColor Cyan
    Write-Host "  Phones/Macs: open $appName-LAN-Trust.mobileconfig. Details: README.txt" -ForegroundColor Cyan
}

# --- Optional: trust on this computer ---------------------------------------
if ($TrustOnThisComputer) {
    $tempDer = Join-Path ([IO.Path]::GetTempPath()) ("lan-root-ca-" + [guid]::NewGuid().ToString("N") + ".cer")
    [IO.File]::WriteAllBytes($tempDer, $der)
    try {
        $store = "Cert:\CurrentUser\Root"
        $already = Get-ChildItem $store | Where-Object { $_.Thumbprint -eq $thumbprint }
        if ($already) {
            Write-Host "  [OK] Already trusted on this computer ($store)" -ForegroundColor Green
        } else {
            Import-Certificate -FilePath $tempDer -CertStoreLocation $store | Out-Null
            Write-Host "  [OK] Imported into $store (confirm the Windows security dialog if shown)" -ForegroundColor Green
        }
    } finally {
        Remove-Item $tempDer -Force -ErrorAction SilentlyContinue
    }
}
exit 0
