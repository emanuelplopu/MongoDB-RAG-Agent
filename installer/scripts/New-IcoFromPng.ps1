<#
.SYNOPSIS
    Builds a multi-resolution Windows .ico from a PNG (no external tools).
.DESCRIPTION
    Writes an ICO container whose entries are PNG-compressed images (supported
    since Windows Vista), one per requested size. Non-square sources are
    centred on a transparent square canvas. Used to create the tenant icons in
    installer\assets (e.g. quellex.ico from the frontend's quellex-q-icon.png).
.EXAMPLE
    .\New-IcoFromPng.ps1 -Source ..\..\frontend\public\tenants\quellex\assets\quellex-q-icon.png -Destination ..\assets\quellex.ico
#>
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [int[]]$Sizes = @(16, 20, 24, 32, 40, 48, 64, 128, 256)
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $Source)) { throw "Source PNG not found: $Source" }
$Sizes = @($Sizes | Where-Object { $_ -ge 1 -and $_ -le 256 } | Sort-Object -Unique)
if ($Sizes.Count -eq 0) { throw "No valid sizes (1-256) given" }

$original = [System.Drawing.Image]::FromFile((Resolve-Path $Source).Path)
try {
    $pngBlobs = New-Object System.Collections.Generic.List[byte[]]
    foreach ($size in $Sizes) {
        $bitmap = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality

            # Fit the source into the square, keeping its aspect ratio
            $scale = [Math]::Min($size / $original.Width, $size / $original.Height)
            $w = [Math]::Max(1, [int][Math]::Round($original.Width * $scale))
            $h = [Math]::Max(1, [int][Math]::Round($original.Height * $scale))
            $x = [int](($size - $w) / 2)
            $y = [int](($size - $h) / 2)
            $graphics.DrawImage($original, $x, $y, $w, $h)
        } finally {
            $graphics.Dispose()
        }

        $stream = New-Object System.IO.MemoryStream
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngBlobs.Add($stream.ToArray())
        $stream.Dispose()
        $bitmap.Dispose()
    }
} finally {
    $original.Dispose()
}

# ICO container: 6-byte header, 16-byte directory entry per image, then the blobs
$count = $Sizes.Count
$headerSize = 6 + 16 * $count
$output = New-Object System.IO.MemoryStream
$writer = New-Object System.IO.BinaryWriter($output)
$writer.Write([uint16]0)        # reserved
$writer.Write([uint16]1)        # type: icon
$writer.Write([uint16]$count)

$offset = $headerSize
for ($i = 0; $i -lt $count; $i++) {
    $size = $Sizes[$i]
    $dim = if ($size -ge 256) { 0 } else { $size }   # 0 means 256
    $writer.Write([byte]$dim)                         # width
    $writer.Write([byte]$dim)                         # height
    $writer.Write([byte]0)                            # colour count (0 = no palette)
    $writer.Write([byte]0)                            # reserved
    $writer.Write([uint16]1)                          # colour planes
    $writer.Write([uint16]32)                         # bits per pixel
    $writer.Write([uint32]$pngBlobs[$i].Length)       # bytes in resource
    $writer.Write([uint32]$offset)                    # offset of the image data
    $offset += $pngBlobs[$i].Length
}
foreach ($blob in $pngBlobs) { $writer.Write($blob) }
$writer.Flush()

$destDir = Split-Path -Parent $Destination
if ($destDir -and -not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }
[System.IO.File]::WriteAllBytes($Destination, $output.ToArray())
$writer.Dispose()

Write-Host "  [OK] Wrote $Destination ($count sizes: $($Sizes -join ', '))" -ForegroundColor Green
