[CmdletBinding()]
param(
    [string]$Capture = "assets/store/captures/tray-menu.png",
    [string]$Output = "assets/store/screenshots/tray-menu-1920x1080.png"
)

# Composes a Microsoft Store screenshot from a real capture of the Chunes tray
# menu. The capture is never redrawn or approximated here; it is placed on a
# card at a whole-number scale so the menu text stays pixel crisp. Retake the
# capture from the build the submission ships, because the packaged build hides
# the two update commands that the MSI build shows.

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$CapturePath = Join-Path $Root $Capture
$OutputPath = Join-Path $Root $Output

if (-not (Test-Path -LiteralPath $CapturePath -PathType Leaf)) {
    throw "Capture not found: $CapturePath"
}

$OutputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $OutputDirectory
}

Add-Type -AssemblyName System.Drawing

$W = 1920
$H = 1080

# 24bpp keeps the saved PNG free of an alpha channel.
$Canvas = New-Object Drawing.Bitmap($W, $H, [Drawing.Imaging.PixelFormat]::Format24bppRgb)
$G = [Drawing.Graphics]::FromImage($Canvas)
$Shot = [Drawing.Image]::FromFile($CapturePath)
$Logo = [Drawing.Image]::FromFile((Join-Path $Root "assets/logo-512.png"))

$Blurple = [Drawing.Color]::FromArgb(255, 88, 101, 242)
$White = [Drawing.Color]::FromArgb(255, 238, 240, 248)
$Muted = [Drawing.Color]::FromArgb(255, 150, 155, 178)
$Eyebrow = [Drawing.Color]::FromArgb(255, 128, 136, 168)

$TitleFont = New-Object Drawing.Font("Segoe UI Semibold", 78, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
$TagFont = New-Object Drawing.Font("Segoe UI", 32, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)
$EyebrowFont = New-Object Drawing.Font("Segoe UI", 22, [Drawing.FontStyle]::Bold, [Drawing.GraphicsUnit]::Pixel)
$FeatureFont = New-Object Drawing.Font("Segoe UI", 27, [Drawing.FontStyle]::Regular, [Drawing.GraphicsUnit]::Pixel)

try {
    $G.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $G.TextRenderingHint = [Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $BgRect = New-Object Drawing.Rectangle(0, 0, $W, $H)
    $BgBrush = New-Object Drawing.Drawing2D.LinearGradientBrush(
        $BgRect,
        [Drawing.Color]::FromArgb(255, 34, 37, 50),
        [Drawing.Color]::FromArgb(255, 12, 13, 19),
        [Drawing.Drawing2D.LinearGradientMode]::ForwardDiagonal)
    $G.FillRectangle($BgBrush, $BgRect)
    $BgBrush.Dispose()

    # Purple halo behind the capture card, echoing the promo tile. A path
    # gradient falls off smoothly to fully transparent; stacked translucent
    # ellipses would band and leave a hard outer edge.
    $HaloCx = 1385
    $HaloCy = 540
    $HaloRadius = 660
    $HaloPath = New-Object Drawing.Drawing2D.GraphicsPath
    $HaloPath.AddEllipse(($HaloCx - $HaloRadius), ($HaloCy - $HaloRadius), ($HaloRadius * 2), ($HaloRadius * 2))
    $Halo = New-Object Drawing.Drawing2D.PathGradientBrush($HaloPath)
    $Halo.CenterPoint = New-Object Drawing.PointF($HaloCx, $HaloCy)
    $Halo.CenterColor = [Drawing.Color]::FromArgb(120, 88, 101, 242)
    $Halo.SurroundColors = @([Drawing.Color]::FromArgb(0, 88, 101, 242))
    # The default interpolation runs the centre colour out to a transparent
    # boundary. A blend or bell shape inverts that, because a path gradient
    # measures position from the boundary inwards, and the halo then paints as
    # a hard disc.
    $G.FillPath($Halo, $HaloPath)
    $Halo.Dispose()
    $HaloPath.Dispose()

    $G.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $G.DrawImage($Logo, 128, 118, 104, 104)

    $EyebrowBrush = New-Object Drawing.SolidBrush($Eyebrow)
    $TitleBrush = New-Object Drawing.SolidBrush($White)
    $TagBrush = New-Object Drawing.SolidBrush($Muted)
    $BulletBrush = New-Object Drawing.SolidBrush($Blurple)

    $eyebrowText = ("CHUNES FOR WINDOWS".ToCharArray() -join [char]0x2009)
    $G.DrawString($eyebrowText, $EyebrowFont, $EyebrowBrush, 254, 155)
    $G.DrawString("Your music,`non your profile.", $TitleFont, $TitleBrush, 123, 300)
    $G.DrawString("Lives in the notification area, out of your way.", $TagFont, $TagBrush, 128, 530)

    $Features = @(
        "SoundCloud, YouTube Music, and Apple Music",
        "Album art and a progress bar that keeps up",
        "No account, no ads, no telemetry"
    )
    $Y = 640
    foreach ($Feature in $Features) {
        $G.FillEllipse($BulletBrush, 132, $Y + 13, 11, 11)
        $G.DrawString($Feature, $FeatureFont, $TitleBrush, 166, $Y)
        $Y += 64
    }

    $EyebrowBrush.Dispose()
    $TagBrush.Dispose()
    $BulletBrush.Dispose()

    # Whole-number scale keeps menu text crisp; nearest neighbour avoids the
    # blur that bicubic would put on single-pixel separators.
    $Pad = 34
    $MaxWidth = 900
    $MaxHeight = 820
    $Scale = [Math]::Floor([Math]::Min(($MaxWidth - 2 * $Pad) / $Shot.Width, ($MaxHeight - 2 * $Pad) / $Shot.Height))
    if ($Scale -lt 1) {
        throw "Capture is larger than the card; supply a tighter crop."
    }
    $ShotWidth = [int]($Shot.Width * $Scale)
    $ShotHeight = [int]($Shot.Height * $Scale)
    $CardWidth = $ShotWidth + 2 * $Pad
    $CardHeight = $ShotHeight + 2 * $Pad
    $CardX = $HaloCx - [int]($CardWidth / 2)
    $CardY = $HaloCy - [int]($CardHeight / 2)

    for ($i = 18; $i -ge 1; $i--) {
        $spread = $i * 2
        $a = [int](7 * ((19 - $i) / 18.0))
        $shadow = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb($a, 0, 0, 0))
        $G.FillRectangle($shadow, ($CardX - $spread), ($CardY - $spread + 10), ($CardWidth + 2 * $spread), ($CardHeight + 2 * $spread))
        $shadow.Dispose()
    }

    $CardBrush = New-Object Drawing.SolidBrush([Drawing.Color]::FromArgb(255, 255, 255, 255))
    $G.FillRectangle($CardBrush, $CardX, $CardY, $CardWidth, $CardHeight)
    $CardBrush.Dispose()

    $G.InterpolationMode = [Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $G.PixelOffsetMode = [Drawing.Drawing2D.PixelOffsetMode]::Half
    $G.DrawImage($Shot, ($CardX + $Pad), ($CardY + $Pad), $ShotWidth, $ShotHeight)

    $TitleBrush.Dispose()
    $Canvas.Save($OutputPath, [Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $FeatureFont.Dispose()
    $EyebrowFont.Dispose()
    $TagFont.Dispose()
    $TitleFont.Dispose()
    $Logo.Dispose()
    $Shot.Dispose()
    $G.Dispose()
    $Canvas.Dispose()
}

"Created $OutputPath"
