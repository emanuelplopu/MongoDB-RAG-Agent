<#
.SYNOPSIS
    Deterministic routing for a server with two uplinks: the customer LAN and a separate Internet uplink.
.DESCRIPTION
    A Windows server plugged into two networks gets one DHCP default route per adapter,
    both with the same metric, so Internet traffic and the replies to clients on routed
    customer subnets leave through whichever adapter Windows happens to pick. This script
    pins the policy described in docs/21-LAN_ACCESS.md ("Two uplinks"):
      * -InternetInterface gets the lower interface metric and therefore the default
        route: Windows Update, docker pulls, Ollama / model downloads.
      * -LanInterface gets the higher metric plus one persistent static route per
        -RoutedSubnets entry via the LAN's own router, so clients on those subnets are
        reached through the customer LAN. If that uplink goes down the LAN default route
        takes over automatically.
      * The "<App> LAN - Gateway *" firewall rules created by Configure-LanAccess.ps1
        accept the routed subnets in addition to LocalSubnet.
    Idempotent: re-run after adapters, gateways or subnets change, and after
    Configure-LanAccess.ps1 (which recreates its rules with LocalSubnet only).
    Requires administrator privileges (self-elevates). -DryRun only reads.
.PARAMETER LanInterface
    Adapter alias of the customer LAN, the network other computers reach https://quellex.local on.
.PARAMETER RoutedSubnets
    Further customer subnets behind the LAN router, CIDR notation (e.g. 192.168.44.0/24).
    Several: -RoutedSubnets 192.168.44.0/24,192.168.45.0/24
.PARAMETER LanGateway
    Router address on the customer LAN. Default: the adapter's IPv4 default gateway (DHCP).
.PARAMETER InternetInterface
    Adapter alias of the uplink that should carry the default route (downloads, updates).
.PARAMETER LanMetric
    Interface metric for the customer LAN (default 50).
.PARAMETER InternetMetric
    Interface metric for the Internet uplink (default 10; the lower metric wins).
.PARAMETER Tenant
    Firewall rule set to widen (quellex / recallhub); default from the tray config.
.PARAMETER Remove
    Restore automatic metrics, delete the static routes and take the subnets out of the firewall rules.
.PARAMETER DryRun
    Show the current state and the planned changes without changing anything (no elevation).
.EXAMPLE
    .\Configure-Uplinks.ps1 -LanInterface Ethernet -RoutedSubnets 192.168.44.0/24 -InternetInterface "Ethernet 2"
.EXAMPLE
    .\Configure-Uplinks.ps1 -LanInterface Ethernet -RoutedSubnets 192.168.44.0/24 -InternetInterface "Ethernet 2" -DryRun
.EXAMPLE
    .\Configure-Uplinks.ps1 -LanInterface Ethernet -RoutedSubnets 192.168.44.0/24 -InternetInterface "Ethernet 2" -Remove
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$LanInterface,
    [string[]]$RoutedSubnets = @(),
    [string]$LanGateway = "",
    [Parameter(Mandatory = $true)][string]$InternetInterface,
    [ValidateRange(1, 9999)][int]$LanMetric = 50,
    [ValidateRange(1, 9999)][int]$InternetMetric = 10,
    [ValidateSet("", "recallhub", "quellex")][string]$Tenant = "",
    [switch]$Remove,
    [switch]$DryRun,
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not $DryRun -and -not (Test-Administrator)) {
    if ($NoElevate) {
        Write-Host "  [ERROR] Administrator privileges are required to change routes, metrics and firewall rules." -ForegroundColor Red
        exit 1
    }
    Write-Host "  Administrator privileges are required - requesting elevation..." -ForegroundColor Yellow
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$($MyInvocation.MyCommand.Path)`"", "-NoElevate")
    foreach ($kv in $PSBoundParameters.GetEnumerator()) {
        if ($kv.Value -is [switch]) { if ($kv.Value) { $argList += "-$($kv.Key)" } }
        elseif ($kv.Value -is [array]) { $argList += @("-$($kv.Key)", "`"$($kv.Value -join ',')`"") }
        else { $argList += @("-$($kv.Key)", "`"$($kv.Value)`"") }
    }
    $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $proc.ExitCode
}

# --- Inputs ------------------------------------------------------------------
# "-File" passes every argument as one string, so a comma separated list arrives
# as a single element; split it here instead of relying on the parser.
$subnets = @($RoutedSubnets | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
foreach ($s in $subnets) {
    if ($s -notmatch '^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$') {
        Write-Host "  [ERROR] Not an IPv4 CIDR subnet: $s (expected e.g. 192.168.44.0/24)" -ForegroundColor Red
        exit 1
    }
}
if ($LanInterface -eq $InternetInterface) {
    Write-Host "  [ERROR] -LanInterface and -InternetInterface must be different adapters." -ForegroundColor Red
    exit 1
}

$trayDir = Join-Path $env:LOCALAPPDATA "RecallHub"
$configPath = Join-Path $trayDir "config.json"
$config = $null
if (Test-Path $configPath) {
    try { $config = Get-Content $configPath -Raw | ConvertFrom-Json } catch { $config = $null }
}
if (-not $Tenant) {
    $Tenant = if ($config -and $config.app -and $config.app.tenant) { [string]$config.app.tenant } else { "recallhub" }
}
$appName = if ($Tenant -eq "quellex") { "Quellex" } else { "RecallHub" }
$prefix  = "$appName LAN"
$mode    = if ($DryRun) { "DRY RUN" } elseif ($Remove) { "REMOVE" } else { "APPLY" }

function Invoke-Change([string]$what, [scriptblock]$action) {
    if ($DryRun) {
        Write-Host "  [DRY] would $what" -ForegroundColor Yellow
    } else {
        & $action
        Write-Host "  [OK] $what" -ForegroundColor Green
    }
}

function Get-Uplink([string]$alias) {
    $ipIf = Get-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue
    if (-not $ipIf) {
        $names = (Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { $_.Name }) -join ', '
        Write-Host "  [ERROR] No IPv4 interface named '$alias'. Adapters that are up: $names" -ForegroundColor Red
        exit 1
    }
    $addr = Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
    $gw = Get-NetRoute -InterfaceAlias $alias -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Sort-Object RouteMetric | Select-Object -First 1
    [pscustomobject]@{
        Alias     = $alias
        Address   = if ($addr) { "$($addr.IPAddress)/$($addr.PrefixLength)" } else { "(no address)" }
        Gateway   = if ($gw) { [string]$gw.NextHop } else { "" }
        Metric    = [int]$ipIf.InterfaceMetric
        Automatic = [string]$ipIf.AutomaticMetric
        State     = [string]$ipIf.ConnectionState
    }
}

$lan = Get-Uplink $LanInterface
$inet = Get-Uplink $InternetInterface
if (-not $LanGateway) { $LanGateway = $lan.Gateway }
if (-not $Remove -and $subnets.Count -gt 0 -and -not $LanGateway) {
    Write-Host "  [ERROR] '$LanInterface' has no IPv4 default gateway; pass -LanGateway <router address>." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "  $appName uplinks ($mode)" -ForegroundColor Cyan
Write-Host ("  customer LAN : {0}  {1}  gateway {2}  metric {3} ({4})" -f $lan.Alias, $lan.Address, $(if ($LanGateway) { $LanGateway } else { "-" }), $lan.Metric, $lan.Automatic) -ForegroundColor Gray
Write-Host ("  Internet     : {0}  {1}  gateway {2}  metric {3} ({4})" -f $inet.Alias, $inet.Address, $(if ($inet.Gateway) { $inet.Gateway } else { "-" }), $inet.Metric, $inet.Automatic) -ForegroundColor Gray
Write-Host ("  routed subnets: {0}" -f $(if ($subnets.Count) { $subnets -join ', ' } else { "(none)" })) -ForegroundColor Gray
if (-not $inet.Gateway -and -not $Remove) {
    Write-Host "  [WARN] '$InternetInterface' has no default gateway right now; the metric is set anyway and applies once it has one." -ForegroundColor Yellow
}

# --- 1. Interface metrics ----------------------------------------------------
Write-Host ""
Write-Host "  Interface metrics" -ForegroundColor Cyan
function Set-UplinkMetric($uplink, [int]$metric) {
    if ($Remove) {
        if ($uplink.Automatic -eq 'Enabled') {
            Write-Host "  [OK] $($uplink.Alias): automatic metric already" -ForegroundColor Green
        } else {
            $alias = $uplink.Alias
            Invoke-Change "restore automatic metric on '$alias' (was $($uplink.Metric))" {
                Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -AutomaticMetric Enabled
            }
        }
    } else {
        if ($uplink.Automatic -eq 'Disabled' -and $uplink.Metric -eq $metric) {
            Write-Host "  [OK] $($uplink.Alias): metric $metric already" -ForegroundColor Green
        } else {
            $alias = $uplink.Alias
            Invoke-Change "set metric $metric on '$alias' (was $($uplink.Metric), $($uplink.Automatic.ToLower()))" {
                Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -InterfaceMetric $metric
            }
        }
    }
}
Set-UplinkMetric $inet $InternetMetric
Set-UplinkMetric $lan $LanMetric

# --- 2. Static routes for the routed customer subnets ------------------------
Write-Host ""
Write-Host "  Static routes" -ForegroundColor Cyan
if ($subnets.Count -eq 0) { Write-Host "  (no routed subnets given)" -ForegroundColor Gray }
foreach ($s in $subnets) {
    $existing = @(Get-NetRoute -DestinationPrefix $s -AddressFamily IPv4 -ErrorAction SilentlyContinue)
    $onLink = $existing | Where-Object { $_.NextHop -eq '0.0.0.0' } | Select-Object -First 1
    if ($Remove) {
        $ours = @($existing | Where-Object { $_.NextHop -ne '0.0.0.0' })
        if ($ours.Count -eq 0) {
            Write-Host "  [OK] $s : no static route present" -ForegroundColor Green
        } else {
            Invoke-Change "delete route $s via $($ours[0].NextHop) on '$($ours[0].InterfaceAlias)'" {
                foreach ($store in 'ActiveStore', 'PersistentStore') {
                    Get-NetRoute -DestinationPrefix $s -AddressFamily IPv4 -PolicyStore $store -ErrorAction SilentlyContinue |
                        Where-Object { $_.NextHop -ne '0.0.0.0' } | Remove-NetRoute -Confirm:$false
                }
            }
        }
        continue
    }
    if ($onLink) {
        Write-Host "  [OK] $s : on-link via '$($onLink.InterfaceAlias)', no route needed" -ForegroundColor Green
        continue
    }
    $match = $existing | Where-Object { $_.InterfaceAlias -eq $LanInterface -and $_.NextHop -eq $LanGateway } | Select-Object -First 1
    if ($match) {
        Write-Host "  [OK] $s : via $LanGateway on '$LanInterface' already present" -ForegroundColor Green
        continue
    }
    $stale = @($existing | Where-Object { $_.NextHop -ne '0.0.0.0' })
    $verb = if ($stale.Count) { "replace route $s (was via $($stale[0].NextHop) on '$($stale[0].InterfaceAlias)') with" } else { "add route $s" }
    Invoke-Change "$verb via $LanGateway on '$LanInterface'" {
        foreach ($store in 'ActiveStore', 'PersistentStore') {
            Get-NetRoute -DestinationPrefix $s -AddressFamily IPv4 -PolicyStore $store -ErrorAction SilentlyContinue |
                Where-Object { $_.NextHop -ne '0.0.0.0' } | Remove-NetRoute -Confirm:$false
        }
        New-NetRoute -DestinationPrefix $s -InterfaceAlias $LanInterface -NextHop $LanGateway -RouteMetric 1 | Out-Null
    }
}

# --- 3. Firewall scope of the gateway rules ----------------------------------
Write-Host ""
Write-Host "  Firewall ($prefix - Gateway *)" -ForegroundColor Cyan
$rules = @(Get-NetFirewallRule -DisplayName "$prefix - Gateway *" -ErrorAction SilentlyContinue)
if ($rules.Count -eq 0) {
    Write-Host "  [WARN] No '$prefix - Gateway *' rules found; run Configure-LanAccess.ps1 first, then this script again." -ForegroundColor Yellow
}
# Windows stores "192.168.44.0/24" as "192.168.44.0/255.255.255.0" (and a /32 as the
# bare address), so compare in that form to stay idempotent.
function ConvertTo-FirewallAddress([string]$cidr) {
    if ($cidr -notmatch '^(\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})$') { return $cidr }
    $bits = [int]$Matches[2]
    if ($bits -ge 32) { return $Matches[1] }
    $maskValue = if ($bits -eq 0) { [uint32]0 } else { [uint32]([math]::Pow(2, 32) - [math]::Pow(2, 32 - $bits)) }
    $mask = ($maskValue -shr 24), (($maskValue -shr 16) -band 255), (($maskValue -shr 8) -band 255), ($maskValue -band 255)
    return "$($Matches[1])/$($mask -join '.')"
}
$subnetsFw = @($subnets | ForEach-Object { ConvertTo-FirewallAddress $_ })
foreach ($rule in $rules) {
    $current = @(($rule | Get-NetFirewallAddressFilter).RemoteAddress)
    if ($Remove) {
        $new = @($current | Where-Object { $_ -notin $subnetsFw })
        if ($new.Count -eq 0) { $new = @('LocalSubnet') }
    } elseif ($current -contains 'Any') {
        Write-Host "  [OK] $($rule.DisplayName): accepts Any already" -ForegroundColor Green
        continue
    } else {
        $new = @($current) + @($subnetsFw | Where-Object { $_ -notin $current })
    }
    if (($new -join ',') -eq ($current -join ',')) {
        Write-Host "  [OK] $($rule.DisplayName): remote = $($current -join ',')" -ForegroundColor Green
        continue
    }
    $ruleName = $rule.Name
    Invoke-Change "$($rule.DisplayName): remote $($current -join ',') -> $($new -join ',')" {
        Set-NetFirewallRule -Name $ruleName -RemoteAddress $new
    }
}

# --- 4. Show the resulting paths ----------------------------------------------
Write-Host ""
Write-Host "  Resulting paths" -ForegroundColor Cyan
function Show-Path([string]$label, [string]$probe) {
    $route = Find-NetRoute -RemoteIPAddress $probe -ErrorAction SilentlyContinue | Where-Object { $_.DestinationPrefix } | Select-Object -First 1
    if ($route) {
        $via = if ($route.NextHop -eq '0.0.0.0') { "on-link" } else { "via $($route.NextHop)" }
        Write-Host ("  {0,-28} -> '{1}' {2}  [{3}]" -f $label, $route.InterfaceAlias, $via, $route.DestinationPrefix) -ForegroundColor Gray
    } else {
        Write-Host ("  {0,-28} -> no route" -f $label) -ForegroundColor Yellow
    }
}
Show-Path "Internet (1.1.1.1)" "1.1.1.1"
foreach ($s in $subnets) {
    $parts = ($s -split '/')[0] -split '\.'
    $parts[3] = [string]([int]$parts[3] + 1)
    Show-Path "clients in $s" ($parts -join '.')
}
if ($DryRun) {
    Write-Host ""
    Write-Host "  Dry run: nothing changed. The paths above are the current ones." -ForegroundColor Yellow
} else {
    Write-Host ""
    Write-Host "  Done. Re-run this script after Configure-LanAccess.ps1 (it resets the rule scope to LocalSubnet)." -ForegroundColor Green
}
exit 0
