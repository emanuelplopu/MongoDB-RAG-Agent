<#
.SYNOPSIS
    Detects and fixes the NVIDIA "NV-Failsafe" display (640x480, no EDID read from the monitor) on any Windows PC.

.DESCRIPTION
    When an NVIDIA GPU cannot read a monitor's EDID over DisplayPort/HDMI (flaky DDC line, "DisplayPort deep sleep"
    panels, bad cable, KVM), the driver exposes a synthetic "Generic Monitor (NV-Failsafe)" that only offers 640x480.
    This script finds such outputs through the NVIDIA driver API (NVAPI) and forces a real EDID onto them
    (NvAPI_GPU_SetEDID), which is what the NVIDIA Control Panel's "EDID management" does on professional GPUs.
    The monitor then shows up with its own name and all of its modes.

    Actions (one per run):
      -Status        Show GPUs, outputs, their EDID state and the EDID candidates stored in the registry. No changes.
      -Apply         Force an EDID onto every NV-Failsafe output (or onto -DisplayId). Needs admin (self-elevates).
      -Reset         Remove the forced EDID from -DisplayId, or from every output with -All. Needs admin.
      -ExportEdid    Write the EDID the driver currently reports for -DisplayId (or the first real monitor) to a file.
      -Install       Copy this script to Program Files, save the chosen EDID next to it and register a logon task
                     that re-applies it whenever an output comes up as NV-Failsafe. Needs admin. Runs a self-test.
      -Uninstall     Remove the task and the installed files (the forced EDID stays unless -ResetOnUninstall).

    EDID sources for -Apply / -Install (pick one):
      -EdidFile <path>      Binary EDID (128 or 256 bytes) or a hex text dump as exported by the NVIDIA Control Panel.
      -EdidBase64 <text>    The same as base64.
      -Auto                 The EDID of the monitor Windows saw most recently (registry + PnP arrival date).
                            Shown for confirmation unless -Force. Prefer this when the panel was detected before.
      -Generic1080p         A built-in generic 1920x1080@60 EDID that every current monitor accepts.

    Interactive -Apply offers a "keep or revert" countdown (like Windows' own display-settings prompt): if nothing is
    pressed within -RevertSeconds, the forced EDID is removed again. -Force or -Unattended skips it.

.PARAMETER DisplayId
    NVAPI display id (hex, e.g. 0x80061083) as printed by -Status. Default: every NV-Failsafe output.

.PARAMETER Unattended
    Used by the scheduled task: no prompts, no elevation attempt, retries for a while until the driver is ready.

.EXAMPLE
    .\Fix-NvFailsafeDisplay.ps1 -Status
    .\Fix-NvFailsafeDisplay.ps1 -Apply -Auto
    .\Fix-NvFailsafeDisplay.ps1 -Apply -Generic1080p -Force
    .\Fix-NvFailsafeDisplay.ps1 -Install -EdidFile .\monitor.edid.bin
    .\Fix-NvFailsafeDisplay.ps1 -Reset -All

.NOTES
    Requires: Windows 10/11 x64, NVIDIA driver (nvapi64.dll), Windows PowerShell 5.1 or PowerShell 7.
    Exit codes: 0 ok / nothing to do, 1 error, 2 no NVIDIA driver or GPU, 3 administrator rights needed,
    4 invalid EDID, 5 the test after -Install failed.
    A forced EDID is removed with -Reset; the NVIDIA Control Panel (System Topology > EDID > Unload) does the same.
#>
[CmdletBinding(DefaultParameterSetName = 'Status')]
param(
    [Parameter(ParameterSetName = 'Status')]    [switch]$Status,
    [Parameter(ParameterSetName = 'Apply')]     [switch]$Apply,
    [Parameter(ParameterSetName = 'Reset')]     [switch]$Reset,
    [Parameter(ParameterSetName = 'Export')]    [string]$ExportEdid,
    [Parameter(ParameterSetName = 'Install')]   [switch]$Install,
    [Parameter(ParameterSetName = 'Uninstall')] [switch]$Uninstall,

    [Parameter(ParameterSetName = 'Apply')] [Parameter(ParameterSetName = 'Install')] [string]$EdidFile,
    [Parameter(ParameterSetName = 'Apply')] [Parameter(ParameterSetName = 'Install')] [string]$EdidBase64,
    [Parameter(ParameterSetName = 'Apply')] [Parameter(ParameterSetName = 'Install')] [switch]$Auto,
    [Parameter(ParameterSetName = 'Apply')] [Parameter(ParameterSetName = 'Install')] [switch]$Generic1080p,

    [Parameter(ParameterSetName = 'Apply')] [Parameter(ParameterSetName = 'Reset')] [Parameter(ParameterSetName = 'Export')] [string]$DisplayId,
    [Parameter(ParameterSetName = 'Reset')] [Parameter(ParameterSetName = 'Uninstall')] [switch]$All,
    [Parameter(ParameterSetName = 'Uninstall')] [switch]$ResetOnUninstall,
    [Parameter(ParameterSetName = 'Install')] [switch]$SkipTest,
    [Parameter(ParameterSetName = 'Install')] [Parameter(ParameterSetName = 'Uninstall')] [string]$TaskName = 'NvFailsafeFix',

    [switch]$Force,
    [switch]$Unattended,
    [int]$RevertSeconds = 25,
    [int]$RetryAttempts = 8,
    [int]$RetrySeconds = 10,
    [string]$LogPath
)

Set-StrictMode -Version 2
$ErrorActionPreference = 'Stop'
$script:InstallDir = Join-Path $env:ProgramFiles 'NvFailsafeFix'
$script:DataDir = Join-Path $env:ProgramData 'NvFailsafeFix'
if (-not $LogPath) { $LogPath = Join-Path $script:DataDir 'NvFailsafeFix.log' }
$script:ExitCode = 0
$script:BoundParams = $PSBoundParameters   # the script's own parameters (functions see their own, empty, table)

# ----------------------------- logging -----------------------------

function Write-Log {
    param([string]$Message, [ValidateSet('INFO', 'WARN', 'ERROR', 'OK')] [string]$Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    try {
        $dir = Split-Path -Parent $LogPath
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
    } catch { }
    switch ($Level) {
        'ERROR' { Write-Host $line -ForegroundColor Red }
        'WARN'  { Write-Host $line -ForegroundColor Yellow }
        'OK'    { Write-Host $line -ForegroundColor Green }
        default { Write-Host $line }
    }
}

function Exit-Script([int]$Code) { $script:ExitCode = $Code; exit $Code }

# ----------------------------- environment -----------------------------

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-RelaunchArguments {
    # Rebuild the command line from the bound parameters so an elevated / 64-bit child runs the same action.
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($kv in $script:BoundParams.GetEnumerator()) {
        $v = $kv.Value
        if ($v -is [switch]) { if ($v.IsPresent) { $parts.Add("-$($kv.Key)") } }
        elseif ($null -ne $v -and "$v" -ne '') { $parts.Add("-$($kv.Key) `"$v`"") }
    }
    if (-not $script:BoundParams.ContainsKey('LogPath')) { $parts.Add("-LogPath `"$LogPath`"") }
    return ($parts -join ' ')
}

function Invoke-Relaunch([string]$Reason, [bool]$Elevate) {
    $exe = (Get-Process -Id $PID).Path
    if (-not [Environment]::Is64BitProcess) {
        $sysnative = Join-Path $env:windir 'sysnative\WindowsPowerShell\v1.0\powershell.exe'
        if (Test-Path $sysnative) { $exe = $sysnative }
    }
    $args = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" " + (Get-RelaunchArguments)
    Write-Log "$Reason - relaunching: $exe $args"
    $sp = @{ FilePath = $exe; ArgumentList = $args; PassThru = $true; Wait = $true }
    if ($Elevate) { $sp.Verb = 'RunAs' }
    try { $p = Start-Process @sp } catch { Write-Log "relaunch failed: $($_.Exception.Message)" 'ERROR'; Exit-Script 3 }
    Write-Log "child exit code $($p.ExitCode); log: $LogPath"
    Exit-Script $p.ExitCode
}

function Assert-Environment([bool]$NeedsAdmin) {
    if (-not [Environment]::Is64BitOperatingSystem) { Write-Log 'This tool needs 64-bit Windows.' 'ERROR'; Exit-Script 2 }
    if (-not [Environment]::Is64BitProcess) {
        if ($Unattended) { Write-Log 'Run this from 64-bit PowerShell (nvapi64.dll).' 'ERROR'; Exit-Script 2 }
        Invoke-Relaunch '32-bit PowerShell' $NeedsAdmin
    }
    if (-not (Test-Path (Join-Path $env:SystemRoot 'System32\nvapi64.dll'))) {
        Write-Log 'nvapi64.dll not found: no NVIDIA driver on this PC, nothing this tool can do.' 'ERROR'; Exit-Script 2
    }
    if ($NeedsAdmin -and -not (Test-Admin)) {
        if ($Unattended) { Write-Log 'Administrator rights are required for this action.' 'ERROR'; Exit-Script 3 }
        Invoke-Relaunch 'administrator rights needed' $true
    }
}

# ----------------------------- NVAPI interop -----------------------------

$script:NvApiSource = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class NvFailsafe
{
    [DllImport("nvapi64.dll", EntryPoint = "nvapi_QueryInterface", CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr QueryInterface(uint id);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int InitializeD();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int UnloadD();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int EnumPhysicalGPUsD([Out] IntPtr[] handles, out int count);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int GetFullNameD(IntPtr gpu, StringBuilder name);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int GetDisplayIdByNameD([MarshalAs(UnmanagedType.LPStr)] string name, out uint displayId);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int GetConnectedDisplayIdsD(IntPtr gpu, IntPtr ids, ref uint count, uint flags);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int EdidD(IntPtr gpu, uint displayId, ref NV_EDID edid);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int GetErrorMessageD(int status, StringBuilder msg);

    [StructLayout(LayoutKind.Sequential)]
    public struct NV_EDID { public uint version; [MarshalAs(UnmanagedType.ByValArray, SizeConst = 256)] public byte[] data; public uint sizeofEDID; public uint edidId; public uint offset; }
    [StructLayout(LayoutKind.Sequential)]
    public struct NV_GPU_DISPLAYIDS { public uint version; public int connectorType; public uint displayId; public uint flags; }

    public class Output
    {
        public IntPtr Gpu; public string GpuName; public uint DisplayId; public int ConnectorType; public uint Flags;
        public bool IsActive { get { return (Flags & 4) != 0; } }
        public bool IsPhysicallyConnected { get { return (Flags & 0x20000) != 0; } }
        public byte[] Edid; public int EdidStatus;
        public bool IsFailsafe { get { return Edid != null && Edid.Length >= 10 && Edid[8] == 0x3A && Edid[9] == 0xC4; } }
    }

    static Delegate Fn(uint id, Type t)
    {
        IntPtr p = QueryInterface(id);
        if (p == IntPtr.Zero) throw new Exception("nvapi_QueryInterface failed for 0x" + id.ToString("X8") + " (driver too old?)");
        return Marshal.GetDelegateForFunctionPointer(p, t);
    }
    static uint EdidVersion() { return (uint)(Marshal.SizeOf(typeof(NV_EDID)) | (3 << 16)); }

    public static string ErrorText(int status)
    {
        try { var d = (GetErrorMessageD)Fn(0x6C2D048C, typeof(GetErrorMessageD)); var sb = new StringBuilder(128); d(status, sb); return status + " (" + sb + ")"; }
        catch { return status.ToString(); }
    }
    public static int Initialize() { return ((InitializeD)Fn(0x0150E828, typeof(InitializeD)))(); }
    public static int Unload() { try { return ((UnloadD)Fn(0xD22BDD7E, typeof(UnloadD)))(); } catch { return -1; } }

    public static IntPtr[] EnumGpus()
    {
        var d = (EnumPhysicalGPUsD)Fn(0xE5AC921F, typeof(EnumPhysicalGPUsD)); var hs = new IntPtr[64]; int n;
        int r = d(hs, out n); if (r != 0) throw new Exception("NvAPI_EnumPhysicalGPUs: " + ErrorText(r));
        var o = new IntPtr[n]; Array.Copy(hs, o, n); return o;
    }
    public static string GpuName(IntPtr gpu)
    {
        var d = (GetFullNameD)Fn(0xCEEE8E9F, typeof(GetFullNameD)); var sb = new StringBuilder(64); return d(gpu, sb) == 0 ? sb.ToString() : "NVIDIA GPU";
    }
    public static byte[] GetEdid(uint displayId, out int status)
    {
        var g = (EdidD)Fn(0x37D32E69, typeof(EdidD)); var e = new NV_EDID(); e.version = EdidVersion(); e.data = new byte[256];
        status = g(IntPtr.Zero, displayId, ref e);
        if (status != 0) return new byte[0];
        int n = (int)Math.Min(e.sizeofEDID, 256u); var r = new byte[n]; Array.Copy(e.data, r, n); return r;
    }
    // edid == null or empty removes a forced EDID.
    public static int SetEdid(uint displayId, byte[] edid)
    {
        var s = (EdidD)Fn(0xE83D6456, typeof(EdidD)); var e = new NV_EDID(); e.version = EdidVersion(); e.data = new byte[256];
        e.sizeofEDID = (uint)(edid == null ? 0 : edid.Length); e.edidId = 0; e.offset = 0;
        if (edid != null) Array.Copy(edid, e.data, Math.Min(edid.Length, 256));
        return s(IntPtr.Zero, displayId, ref e);
    }
    public static uint DisplayIdByName(string name, out int status)
    {
        var d = (GetDisplayIdByNameD)Fn(0xAE457190, typeof(GetDisplayIdByNameD)); uint id; status = d(name, out id); return status == 0 ? id : 0;
    }
    public static List<Output> ConnectedOutputs(bool uncached)
    {
        var list = new List<Output>();
        var c = (GetConnectedDisplayIdsD)Fn(0x0078DBA2, typeof(GetConnectedDisplayIdsD));
        foreach (IntPtr gpu in EnumGpus())
        {
            string gname = GpuName(gpu); uint cnt = 0; uint flags = uncached ? 1u : 0u;
            int r = c(gpu, IntPtr.Zero, ref cnt, flags); if (r != 0 || cnt == 0) continue;
            int sz = Marshal.SizeOf(typeof(NV_GPU_DISPLAYIDS)); IntPtr buf = Marshal.AllocHGlobal(sz * (int)cnt);
            try
            {
                for (int i = 0; i < cnt; i++) Marshal.WriteInt32(buf, i * sz, sz | (3 << 16));
                r = c(gpu, buf, ref cnt, flags); if (r != 0) continue;
                for (int i = 0; i < cnt; i++)
                {
                    var d = (NV_GPU_DISPLAYIDS)Marshal.PtrToStructure(new IntPtr(buf.ToInt64() + i * sz), typeof(NV_GPU_DISPLAYIDS));
                    var o = new Output { Gpu = gpu, GpuName = gname, DisplayId = d.displayId, ConnectorType = d.connectorType, Flags = d.flags };
                    int st; o.Edid = GetEdid(d.displayId, out st); o.EdidStatus = st; list.Add(o);
                }
            }
            finally { Marshal.FreeHGlobal(buf); }
        }
        return list;
    }
}
'@

function Initialize-NvApi {
    if (-not ('NvFailsafe' -as [type])) { Add-Type -TypeDefinition $script:NvApiSource -ErrorAction Stop }
    $r = [NvFailsafe]::Initialize()
    if ($r -ne 0) { throw "NvAPI_Initialize failed: $([NvFailsafe]::ErrorText($r)) - no NVIDIA GPU, or the driver is not running." }
}

function Get-ConnectorName([int]$Type) {
    switch ($Type) { 1 { 'VGA' } 2 { 'Component' } 3 { 'S-Video' } 4 { 'HDMI' } 5 { 'DVI' } 6 { 'LVDS' } 7 { 'DisplayPort' } 8 { 'Composite' } default { "type $Type" } }
}

# ----------------------------- EDID helpers -----------------------------

function ConvertFrom-Edid([byte[]]$Bytes) {
    # Minimal decode of block 0; returns $null when the header is wrong.
    if (-not $Bytes -or $Bytes.Length -lt 128) { return $null }
    $hdr = [byte[]](0, 255, 255, 255, 255, 255, 255, 0)
    for ($i = 0; $i -lt 8; $i++) { if ($Bytes[$i] -ne $hdr[$i]) { return $null } }
    $m = ([int]$Bytes[8] -shl 8) -bor $Bytes[9]
    $mfg = -join @([char](64 + (($m -shr 10) -band 0x1F)), [char](64 + (($m -shr 5) -band 0x1F)), [char](64 + ($m -band 0x1F)))
    $name = ''; $serialText = ''
    foreach ($off in 54, 72, 90, 108) {
        if ($Bytes[$off] -eq 0 -and $Bytes[$off + 1] -eq 0 -and $Bytes[$off + 2] -eq 0) {
            $txt = ([System.Text.Encoding]::ASCII.GetString($Bytes, $off + 5, 13) -split "`n")[0].Trim()
            if ($Bytes[$off + 3] -eq 0xFC) { $name = $txt } elseif ($Bytes[$off + 3] -eq 0xFF) { $serialText = $txt }
        }
    }
    $pref = ''
    foreach ($off in 54, 72, 90, 108) {
        $pix = [int]$Bytes[$off] -bor ([int]$Bytes[$off + 1] -shl 8)
        if ($pix -eq 0) { continue }
        $h = [int]$Bytes[$off + 2] -bor (([int]$Bytes[$off + 4] -shr 4) -shl 8); $v = [int]$Bytes[$off + 5] -bor (([int]$Bytes[$off + 7] -shr 4) -shl 8)
        $hb = [int]$Bytes[$off + 3] -bor (([int]$Bytes[$off + 4] -band 0xF) -shl 8); $vb = [int]$Bytes[$off + 6] -bor (([int]$Bytes[$off + 7] -band 0xF) -shl 8)
        $hz = 0; if (($h + $hb) -gt 0 -and ($v + $vb) -gt 0) { $hz = [math]::Round($pix * 10000.0 / (($h + $hb) * ($v + $vb)), 1) }
        $pref = "${h}x${v}@${hz}Hz"; break
    }
    $blocks = [math]::Floor($Bytes.Length / 128); $ok = $true
    for ($b = 0; $b -lt $blocks; $b++) { $sum = 0; for ($i = 0; $i -lt 128; $i++) { $sum += $Bytes[$b * 128 + $i] }; if (($sum % 256) -ne 0) { $ok = $false } }
    [pscustomobject]@{
        Manufacturer = $mfg; Product = ('{0:X4}' -f ([int]$Bytes[10] -bor ([int]$Bytes[11] -shl 8))); Name = $name; Serial = $serialText
        Year = (1990 + $Bytes[17]); Preferred = $pref; Extensions = [int]$Bytes[126]; Length = $Bytes.Length; ChecksumsOk = $ok
        IsFailsafe = ($mfg -eq 'NVD')
    }
}

function Format-EdidInfo($Info) {
    if (-not $Info) { return 'not an EDID' }
    $n = $Info.Name; if (-not $n) { $n = "$($Info.Manufacturer) $($Info.Product)" }
    "{0} [{1} {2}, {3}, {4} bytes, {5} ext, checksum {6}]" -f $n, $Info.Manufacturer, $Info.Product, $Info.Preferred, $Info.Length, $Info.Extensions, $(if ($Info.ChecksumsOk) { 'ok' } else { 'BAD' })
}

function Repair-EdidBlock0([byte[]]$Bytes) {
    # Recompute the block-0 checksum (after truncation / edits).
    $sum = 0; for ($i = 0; $i -lt 127; $i++) { $sum += $Bytes[$i] }
    $Bytes[127] = [byte]((256 - ($sum % 256)) % 256)
    return $Bytes
}

function Test-EdidOrThrow([byte[]]$Bytes, [string]$Source) {
    if (-not $Bytes -or $Bytes.Length -lt 128) { throw "EDID from $Source is shorter than 128 bytes." }
    if (($Bytes.Length % 128) -ne 0) { throw "EDID from $Source has $($Bytes.Length) bytes, not a multiple of 128." }
    $info = ConvertFrom-Edid $Bytes
    if (-not $info) { throw "EDID from $Source has no valid header." }
    if ($info.IsFailsafe) { throw "EDID from $Source is NVIDIA's own failsafe EDID; refusing to force that." }
    if ($Bytes.Length -gt 256) {
        Write-Log "EDID from $Source has $($Bytes.Length) bytes; the driver API takes 256, keeping block 0 and the first extension." 'WARN'
        $Bytes = $Bytes[0..255]; $Bytes[126] = 1; $Bytes = Repair-EdidBlock0 $Bytes; $info = ConvertFrom-Edid $Bytes
    }
    if (-not $info.ChecksumsOk) { throw "EDID from $Source has a bad checksum." }
    return , $Bytes
}

function Read-EdidFile([string]$Path) {
    if (-not (Test-Path $Path)) { throw "EDID file not found: $Path" }
    $raw = [IO.File]::ReadAllBytes($Path)
    if ($raw.Length -ge 128 -and $raw[0] -eq 0 -and $raw[1] -eq 0xFF) { return , $raw }
    # Hex text (NVIDIA Control Panel export, "00 FF FF ..." lines, offsets allowed)
    $text = [System.Text.Encoding]::ASCII.GetString($raw)
    $hex = New-Object System.Collections.Generic.List[byte]
    foreach ($line in ($text -split "`r?`n")) {
        $l = $line.Trim(); if (-not $l) { continue }
        if ($l -match '^\s*[0-9A-Fa-f]{1,4}[:|]\s*(.*)$') { $l = $Matches[1] }
        foreach ($tok in ($l -split '[\s,]+')) { if ($tok -match '^(0x)?([0-9A-Fa-f]{2})$') { $hex.Add([Convert]::ToByte($Matches[2], 16)) } }
    }
    if ($hex.Count -lt 128) { throw "Could not read an EDID from $Path (binary 128/256 bytes or hex text expected)." }
    return , $hex.ToArray()
}

function New-GenericEdid1080p {
    # EDID 1.3, digital input, 1920x1080@60 preferred (CTA-861 timing, 148.5 MHz), accepted by every current monitor.
    $b = New-Object byte[] 128
    $hdr = 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00; for ($i = 0; $i -lt 8; $i++) { $b[$i] = $hdr[$i] }
    $b[8] = 0x1C; $b[9] = 0xAE                     # manufacturer "GEN"
    $b[10] = 0x01; $b[11] = 0x00                   # product 0x0001
    $b[12] = 0x01; $b[13] = 0; $b[14] = 0; $b[15] = 0   # serial 1
    $b[16] = 1; $b[17] = 30                        # week 1 of 2020
    $b[18] = 1; $b[19] = 3                         # EDID 1.3
    $b[20] = 0x80                                  # digital input
    $b[21] = 53; $b[22] = 30                       # 53x30 cm
    $b[23] = 120                                   # gamma 2.2
    $b[24] = 0x0A                                  # sRGB, preferred timing in DTD 1
    $chroma = 0xEE, 0x91, 0xA3, 0x54, 0x4C, 0x99, 0x26, 0x0F, 0x50, 0x54; for ($i = 0; $i -lt 10; $i++) { $b[25 + $i] = $chroma[$i] }
    $b[35] = 0x21; $b[36] = 0x08; $b[37] = 0x00    # established: 640x480@60, 800x600@60, 1024x768@60
    $b[38] = 0xD1; $b[39] = 0xC0                   # standard timing 1920x1080@60
    for ($i = 40; $i -lt 54; $i++) { $b[$i] = 0x01 }
    $dtd = 0x02, 0x3A, 0x80, 0x18, 0x71, 0x38, 0x2D, 0x40, 0x58, 0x2C, 0x45, 0x00, 0x13, 0x2B, 0x21, 0x00, 0x00, 0x1E
    for ($i = 0; $i -lt 18; $i++) { $b[54 + $i] = $dtd[$i] }
    $d2 = 0x00, 0x00, 0x00, 0xFF, 0x00 + [byte[]][char[]]'GENERIC1080P' + 0x0A
    for ($i = 0; $i -lt 18; $i++) { $b[72 + $i] = $d2[$i] }
    $d3 = 0x00, 0x00, 0x00, 0xFD, 0x00, 0x32, 0x4B, 0x1E, 0x53, 0x11, 0x00, 0x0A, 0x20, 0x20, 0x20, 0x20, 0x20, 0x20
    for ($i = 0; $i -lt 18; $i++) { $b[90 + $i] = $d3[$i] }
    $d4 = 0x00, 0x00, 0x00, 0xFC, 0x00 + [byte[]][char[]]'Generic 1080p'
    for ($i = 0; $i -lt 18; $i++) { $b[108 + $i] = $d4[$i] }
    $b[126] = 0
    return , (Repair-EdidBlock0 $b)
}

function Get-EdidCandidates {
    # Every monitor Windows has ever enumerated keeps its EDID in the registry. Rank by last PnP arrival.
    $out = @()
    $root = 'HKLM:\SYSTEM\CurrentControlSet\Enum\DISPLAY'
    foreach ($mfgKey in (Get-ChildItem $root -ErrorAction SilentlyContinue)) {
        if ($mfgKey.PSChildName -match '^(NVD0000|Default_Monitor)$') { continue }
        foreach ($inst in (Get-ChildItem $mfgKey.PSPath -ErrorAction SilentlyContinue)) {
            $edid = $null
            try { $edid = (Get-ItemProperty (Join-Path $inst.PSPath 'Device Parameters') -ErrorAction Stop).EDID } catch { }
            if (-not $edid -or $edid.Length -lt 128) { continue }
            $info = ConvertFrom-Edid $edid
            if (-not $info -or $info.IsFailsafe) { continue }
            $instanceId = "DISPLAY\$($mfgKey.PSChildName)\$($inst.PSChildName)"
            $arrival = $null; $present = $false
            try {
                $arrival = (Get-PnpDeviceProperty -InstanceId $instanceId -KeyName 'DEVPKEY_Device_LastArrivalDate' -ErrorAction Stop).Data
                $present = ((Get-PnpDevice -InstanceId $instanceId -ErrorAction Stop).Status -eq 'OK')
            } catch { }
            $out += [pscustomobject]@{ InstanceId = $instanceId; Info = $info; Edid = $edid; LastArrival = $arrival; Present = $present }
        }
    }
    return $out | Sort-Object -Property @{ Expression = { if ($_.LastArrival) { $_.LastArrival } else { [datetime]::MinValue } }; Descending = $true }
}

function Resolve-EdidSource {
    # Returns the EDID bytes to force, from the chosen source option.
    if ($EdidFile) { $bytes = Read-EdidFile $EdidFile; Write-Log "EDID from file ${EdidFile}: $(Format-EdidInfo (ConvertFrom-Edid $bytes))"; return , (Test-EdidOrThrow $bytes "file $EdidFile") }
    if ($EdidBase64) { $bytes = [Convert]::FromBase64String($EdidBase64); Write-Log "EDID from base64: $(Format-EdidInfo (ConvertFrom-Edid $bytes))"; return , (Test-EdidOrThrow $bytes 'base64') }
    if ($Generic1080p) { $bytes = New-GenericEdid1080p; Write-Log "EDID: built-in generic $(Format-EdidInfo (ConvertFrom-Edid $bytes))"; return , (Test-EdidOrThrow $bytes 'generic') }
    if ($Auto) {
        $cands = @(Get-EdidCandidates)
        if ($cands.Count -eq 0) { throw 'No monitor EDID found in the registry; use -EdidFile or -Generic1080p.' }
        $pick = $cands[0]
        Write-Log "Auto-selected (most recently seen monitor): $(Format-EdidInfo $pick.Info) from $($pick.InstanceId), last arrival $($pick.LastArrival)"
        if ($cands.Count -gt 1) { foreach ($c in $cands[1..($cands.Count - 1)]) { Write-Log "  other candidate: $(Format-EdidInfo $c.Info) ($($c.InstanceId), $($c.LastArrival))" } }
        if (-not $Force -and -not $Unattended) {
            $answer = Read-Host "Force this EDID? The monitor must really be the one listed, or the screen may go dark. [y/N]"
            if ($answer -notmatch '^[yYjJ]') { throw 'Cancelled.' }
        }
        return , (Test-EdidOrThrow $pick.Edid $pick.InstanceId)
    }
    throw 'Choose an EDID source: -EdidFile, -EdidBase64, -Auto or -Generic1080p.'
}

# ----------------------------- display inventory -----------------------------

$script:DispApiSource = @'
using System;
using System.Runtime.InteropServices;
public static class DispMode {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Ansi)]
    public struct DEVMODE {
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmDeviceName;
        public short dmSpecVersion, dmDriverVersion, dmSize, dmDriverExtra; public int dmFields;
        public int dmPositionX, dmPositionY, dmDisplayOrientation, dmDisplayFixedOutput;
        public short dmColor, dmDuplex, dmYResolution, dmTTOption, dmCollate;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string dmFormName;
        public short dmLogPixels; public int dmBitsPerPel, dmPelsWidth, dmPelsHeight, dmDisplayFlags, dmDisplayFrequency;
        public int dmICMMethod, dmICMIntent, dmMediaType, dmDitherType, dmReserved1, dmReserved2, dmPanningWidth, dmPanningHeight;
    }
    [DllImport("user32.dll", CharSet = CharSet.Ansi)] static extern bool EnumDisplaySettingsA(string deviceName, int modeNum, ref DEVMODE devMode);
    public static string Current(string device) {
        var dm = new DEVMODE(); dm.dmSize = (short)Marshal.SizeOf(dm);
        if (!EnumDisplaySettingsA(device, -1, ref dm)) return null;
        int modes = 0; var m = new DEVMODE(); m.dmSize = dm.dmSize; while (EnumDisplaySettingsA(device, modes, ref m)) modes++;
        return dm.dmPelsWidth + "x" + dm.dmPelsHeight + "@" + dm.dmDisplayFrequency + "Hz (" + modes + " modes)";
    }
}
'@

function Get-OutputInventory {
    if (-not ('DispMode' -as [type])) { Add-Type -TypeDefinition $script:DispApiSource -ErrorAction Stop }
    $outputs = [NvFailsafe]::ConnectedOutputs($true)
    $names = @{}
    for ($i = 1; $i -le 32; $i++) {
        $st = 0; $id = [NvFailsafe]::DisplayIdByName("\\.\DISPLAY$i", [ref]$st)
        if ($st -eq 0 -and $id -ne 0 -and -not $names.ContainsKey($id)) { $names[$id] = "\\.\DISPLAY$i" }
    }
    $rows = @()
    foreach ($o in $outputs) {
        $gdi = $null; if ($names.ContainsKey($o.DisplayId)) { $gdi = $names[$o.DisplayId] }
        $mode = $null; if ($gdi) { $mode = [DispMode]::Current($gdi) }
        $rows += [pscustomobject]@{
            Gpu = $o.GpuName; DisplayId = ('0x{0:X8}' -f $o.DisplayId); Connector = (Get-ConnectorName $o.ConnectorType)
            Active = $o.IsActive; Physical = $o.IsPhysicallyConnected; Failsafe = $o.IsFailsafe
            Edid = $(if ($o.EdidStatus -eq 0) { Format-EdidInfo (ConvertFrom-Edid $o.Edid) } else { "no EDID: $([NvFailsafe]::ErrorText($o.EdidStatus))" })
            EdidBytes = $o.Edid; Gdi = $gdi; Mode = $mode; Raw = $o
        }
    }
    return $rows
}

function Select-TargetOutputs($Inventory, [bool]$FailsafeOnly) {
    if ($DisplayId) {
        $want = [Convert]::ToUInt32(($DisplayId -replace '^0x', ''), 16)
        $hit = @($Inventory | Where-Object { $_.Raw.DisplayId -eq $want })
        if ($hit.Count -eq 0) { throw "Display id $DisplayId is not a connected NVIDIA output (see -Status)." }
        return $hit
    }
    if ($FailsafeOnly) { return @($Inventory | Where-Object { $_.Failsafe }) }
    return @($Inventory)
}

# ----------------------------- actions -----------------------------

function Show-Status {
    Initialize-NvApi
    $inv = @(Get-OutputInventory)
    Write-Log "NVIDIA outputs: $($inv.Count)"
    foreach ($r in $inv) {
        $lvl = 'INFO'; if ($r.Failsafe) { $lvl = 'WARN' }
        Write-Log ("  {0} {1} on {2}: active={3} physical={4} gdi={5} mode={6}`n      EDID: {7}{8}" -f $r.DisplayId, $r.Connector, $r.Gpu, $r.Active, $r.Physical, $r.Gdi, $r.Mode, $r.Edid, $(if ($r.Failsafe) { '  <-- NV-FAILSAFE' } else { '' })) $lvl
    }
    $fs = @($inv | Where-Object { $_.Failsafe })
    if ($fs.Count -gt 0) { Write-Log "$($fs.Count) output(s) in failsafe: run -Apply with an EDID source." 'WARN' } else { Write-Log 'No failsafe output; nothing to fix.' 'OK' }
    Write-Log 'Monitor EDIDs known to Windows (most recently seen first):'
    foreach ($c in @(Get-EdidCandidates)) { Write-Log ("  {0}  present={1}  lastArrival={2}  {3}" -f (Format-EdidInfo $c.Info), $c.Present, $c.LastArrival, $c.InstanceId) }
    try { $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop; Write-Log "Logon task '$TaskName': $($t.State) (installed in $script:InstallDir)" } catch { Write-Log "Logon task '$TaskName': not installed" }
    [NvFailsafe]::Unload() | Out-Null
}

function Invoke-Apply {
    Assert-Environment $true
    $bytes = Resolve-EdidSource
    $attempt = 0; $applied = 0
    while ($true) {
        $attempt++
        try {
            Initialize-NvApi
            $inv = @(Get-OutputInventory)
            $targets = @(Select-TargetOutputs $inv $true)
            if ($targets.Count -eq 0) {
                $real = @($inv | Where-Object { $_.Raw.EdidStatus -eq 0 })
                if ($real.Count -gt 0 -or -not $Unattended) { Write-Log 'No NV-Failsafe output found; nothing to do.' 'OK'; [NvFailsafe]::Unload() | Out-Null; return 0 }
                throw 'No outputs readable yet (driver still starting?)'
            }
            foreach ($t in $targets) {
                $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $bytes)
                if ($r -eq 0) { $applied++; Write-Log "Forced EDID onto $($t.DisplayId) ($($t.Connector), was: $($t.Edid))" 'OK' }
                else { Write-Log "SetEDID on $($t.DisplayId) failed: $([NvFailsafe]::ErrorText($r))" 'ERROR' }
            }
            [NvFailsafe]::Unload() | Out-Null
            if ($applied -gt 0) { break }
            throw 'SetEDID failed on every target.'
        } catch {
            if ($attempt -ge [math]::Max(1, $(if ($Unattended) { $RetryAttempts } else { 1 }))) { Write-Log $_.Exception.Message 'ERROR'; return 1 }
            Write-Log "$($_.Exception.Message) - retrying in $RetrySeconds s ($attempt/$RetryAttempts)" 'WARN'
            Start-Sleep -Seconds $RetrySeconds
        }
    }
    Start-Sleep -Seconds 4
    Initialize-NvApi
    $after = @(Get-OutputInventory)
    foreach ($t in $targets) {
        $now = $after | Where-Object { $_.Raw.DisplayId -eq $t.Raw.DisplayId }
        if ($now) { Write-Log "  $($t.DisplayId) now: $($now.Edid) mode=$($now.Mode)" }
    }
    [NvFailsafe]::Unload() | Out-Null
    if (-not $Force -and -not $Unattended -and $RevertSeconds -gt 0) {
        Write-Host ''
        Write-Host "Keep the new display settings? Press Y within $RevertSeconds s, anything else or no key reverts." -ForegroundColor Cyan
        $keep = $false; $deadline = (Get-Date).AddSeconds($RevertSeconds)
        try {
            while ((Get-Date) -lt $deadline) {
                if ($host.UI.RawUI.KeyAvailable) { $k = $host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown'); if ($k.Character -match '[yYjJ]') { $keep = $true }; break }
                Start-Sleep -Milliseconds 200
            }
        } catch { $keep = $true; Write-Log 'No interactive console; keeping the settings.' 'WARN' }
        if (-not $keep) {
            Write-Log 'Reverting: removing the forced EDID again.' 'WARN'
            Initialize-NvApi
            foreach ($t in $targets) { $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $null); Write-Log "  reset $($t.DisplayId): $([NvFailsafe]::ErrorText($r))" }
            [NvFailsafe]::Unload() | Out-Null
            return 0
        }
        Write-Log 'Kept.' 'OK'
    }
    return 0
}

function Invoke-Reset {
    Assert-Environment $true
    if (-not $DisplayId -and -not $All) { throw 'Say which output: -DisplayId 0x... (see -Status) or -All.' }
    Initialize-NvApi
    $inv = @(Get-OutputInventory)
    $targets = @(Select-TargetOutputs $inv $false)
    foreach ($t in $targets) {
        $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $null)
        if ($r -eq 0) { Write-Log "Removed forced EDID from $($t.DisplayId) ($($t.Connector), was: $($t.Edid))" 'OK' } else { Write-Log "Reset on $($t.DisplayId): $([NvFailsafe]::ErrorText($r))" 'WARN' }
    }
    [NvFailsafe]::Unload() | Out-Null
    return 0
}

function Invoke-Export {
    Assert-Environment $false
    Initialize-NvApi
    $inv = @(Get-OutputInventory)
    $src = $null
    if ($DisplayId) { $src = @(Select-TargetOutputs $inv $false)[0] } else { $src = $inv | Where-Object { -not $_.Failsafe -and $_.Raw.EdidStatus -eq 0 } | Select-Object -First 1 }
    if (-not $src) { throw 'No output with a real EDID to export (use -DisplayId to pick one).' }
    [IO.File]::WriteAllBytes($ExportEdid, $src.EdidBytes)
    Write-Log "Wrote $($src.EdidBytes.Length) bytes from $($src.DisplayId) ($($src.Edid)) to $ExportEdid" 'OK'
    [NvFailsafe]::Unload() | Out-Null
    return 0
}

function Get-TaskArgument([string]$ScriptPath, [string]$EdidPath) {
    "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" -Apply -Unattended -EdidFile `"$EdidPath`" -LogPath `"$(Join-Path $script:DataDir 'NvFailsafeFix.log')`""
}

function Invoke-Install {
    Assert-Environment $true
    $bytes = Resolve-EdidSource
    New-Item -ItemType Directory -Path $script:InstallDir -Force | Out-Null
    New-Item -ItemType Directory -Path $script:DataDir -Force | Out-Null
    $scriptDest = Join-Path $script:InstallDir 'Fix-NvFailsafeDisplay.ps1'
    $edidDest = Join-Path $script:InstallDir 'edid.bin'
    if ((Resolve-Path $PSCommandPath).Path -ne $scriptDest) { Copy-Item $PSCommandPath $scriptDest -Force }
    [IO.File]::WriteAllBytes($edidDest, $bytes)
    Write-Log "Installed $scriptDest and $edidDest ($(Format-EdidInfo (ConvertFrom-Edid $bytes)))" 'OK'

    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (Get-TaskArgument $scriptDest $edidDest)
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $user; $logon.Delay = 'PT15S'
    $triggers = @($logon)
    try {
        $cls = Get-CimClass -ClassName MSFT_TaskSessionStateChangeTrigger -Namespace Root/Microsoft/Windows/TaskScheduler -ErrorAction Stop
        $unlock = New-CimInstance -CimClass $cls -ClientOnly; $unlock.StateChange = 8; $unlock.UserId = $user; $unlock.Delay = 'PT10S'; $unlock.Enabled = $true
        $triggers += $unlock
    } catch { Write-Log "Could not add the session-unlock trigger ($($_.Exception.Message)); logon trigger only." 'WARN' }
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force `
        -Description "Re-applies the saved monitor EDID when an NVIDIA output comes up as NV-Failsafe (640x480). Files: $script:InstallDir" | Out-Null
    Write-Log "Registered scheduled task '$TaskName' for $user (at logon and unlock, highest privileges)." 'OK'

    if ($SkipTest) { return 0 }
    # Self-test: run the task now. If the display is healthy only because of a forced EDID equal to ours, drop it
    # first so the task has a real failsafe to fix. A panel whose DDC works will not fail over, so that case just checks the task runs.
    Initialize-NvApi
    $inv = @(Get-OutputInventory)
    $forcedByUs = @($inv | Where-Object { $_.Raw.EdidStatus -eq 0 -and -not $_.Failsafe -and $_.EdidBytes.Length -ge 128 -and [Convert]::ToBase64String($_.EdidBytes[0..127]) -eq [Convert]::ToBase64String($bytes[0..127]) })
    foreach ($t in $forcedByUs) { $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $null); Write-Log "self-test: removed our EDID from $($t.DisplayId) to provoke the failsafe: $([NvFailsafe]::ErrorText($r))" }
    [NvFailsafe]::Unload() | Out-Null
    if ($forcedByUs.Count -gt 0) { Start-Sleep -Seconds 6 }
    Start-ScheduledTask -TaskName $TaskName
    $deadline = (Get-Date).AddSeconds(150)
    do { Start-Sleep -Seconds 3; $st = (Get-ScheduledTask -TaskName $TaskName).State } while ($st -eq 'Running' -and (Get-Date) -lt $deadline)
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Log "self-test: task state=$st lastResult=$($info.LastTaskResult)"
    Start-Sleep -Seconds 4
    Initialize-NvApi
    $after = @(Get-OutputInventory)
    [NvFailsafe]::Unload() | Out-Null
    $stillFailsafe = @($after | Where-Object { $_.Failsafe })
    foreach ($r in $after) { Write-Log "  after: $($r.DisplayId) $($r.Connector) failsafe=$($r.Failsafe) mode=$($r.Mode) EDID=$($r.Edid)" }
    if ($info.LastTaskResult -ne 0 -or $stillFailsafe.Count -gt 0) {
        Write-Log 'Self-test FAILED: the task did not clear the failsafe. Applying directly as a fallback.' 'ERROR'
        Initialize-NvApi
        foreach ($t in $stillFailsafe) { $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $bytes); Write-Log "  fallback apply on $($t.DisplayId): $([NvFailsafe]::ErrorText($r))" }
        [NvFailsafe]::Unload() | Out-Null
        return 5
    }
    Write-Log "Self-test passed; task log: $(Join-Path $script:DataDir 'NvFailsafeFix.log')" 'OK'
    return 0
}

function Invoke-Uninstall {
    Assert-Environment $true
    try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop; Write-Log "Removed task '$TaskName'." 'OK' } catch { Write-Log "Task '$TaskName' was not installed." }
    if ($ResetOnUninstall) {
        Initialize-NvApi
        foreach ($t in @(Get-OutputInventory)) { if ($t.Raw.EdidStatus -eq 0 -and -not $t.Failsafe) { $r = [NvFailsafe]::SetEdid($t.Raw.DisplayId, $null); Write-Log "reset $($t.DisplayId): $([NvFailsafe]::ErrorText($r))" } }
        [NvFailsafe]::Unload() | Out-Null
    }
    if (Test-Path $script:InstallDir) { [IO.Directory]::Delete($script:InstallDir, $true); Write-Log "Removed $script:InstallDir" 'OK' }
    return 0
}

# ----------------------------- main -----------------------------

try {
    Write-Log ("-- Fix-NvFailsafeDisplay {0} (user={1}, admin={2}, 64-bit={3}, unattended={4})" -f $PSCmdlet.ParameterSetName, $env:USERNAME, (Test-Admin), [Environment]::Is64BitProcess, $Unattended.IsPresent)
    switch ($PSCmdlet.ParameterSetName) {
        'Apply'     { $code = Invoke-Apply }
        'Reset'     { $code = Invoke-Reset }
        'Export'    { $code = Invoke-Export }
        'Install'   { $code = Invoke-Install }
        'Uninstall' { $code = Invoke-Uninstall }
        default     { Assert-Environment $false; Show-Status; $code = 0 }
    }
    Exit-Script $code
} catch {
    if ($_.FullyQualifiedErrorId -eq 'System.Management.Automation.ExitException') { throw }
    $msg = $_.Exception.Message
    $code = 1
    if ($msg -match 'EDID') { $code = 4 }
    if ($msg -match 'NvAPI_Initialize|nvapi64') { $code = 2 }
    Write-Log "FAILED: $msg" 'ERROR'
    if ($VerbosePreference -ne 'SilentlyContinue') { Write-Log $_.ScriptStackTrace }
    Exit-Script $code
}
