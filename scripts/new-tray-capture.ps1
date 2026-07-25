[CmdletBinding()]
param(
    [string]$Output = "assets/store/captures/tray-menu.png",
    [int]$TimeoutSeconds = 120
)

# Waits for the Chunes tray menu to open, then saves that popup window and
# nothing else. Popup menus are their own top-level window (class #32768), so
# the crop is exact and no part of the desktop behind it is captured.
#
# Run the script, then right-click the Chunes icon in the notification area.

$ErrorActionPreference = "Stop"
$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$OutputPath = Join-Path $Root $Output
$OutputDirectory = Split-Path -Parent $OutputPath
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    $null = New-Item -ItemType Directory -Path $OutputDirectory
}

Add-Type -AssemblyName System.Drawing

if (-not ("Native.Win" -as [type])) {
    Add-Type -Namespace Native -Name Win -MemberDefinition @'
[StructLayout(LayoutKind.Sequential)]
public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
public static extern IntPtr FindWindowW(string className, string windowName);

[DllImport("user32.dll")]
public static extern bool IsWindowVisible(IntPtr hWnd);

[DllImport("user32.dll")]
public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);

[DllImport("dwmapi.dll")]
public static extern int DwmGetWindowAttribute(IntPtr hWnd, int attribute, out RECT value, int size);

[DllImport("user32.dll")]
public static extern bool SetProcessDPIAware();
'@
}

# Without this the capture rectangle is expressed in scaled coordinates and the
# crop drifts on any display that is not at 100 percent.
$null = [Native.Win]::SetProcessDPIAware()

# DWMWA_EXTENDED_FRAME_BOUNDS: the visible frame, excluding the drop shadow
# that GetWindowRect includes.
$DwmExtendedFrameBounds = 9

Write-Host "Waiting for the tray menu. Right-click the Chunes icon in the notification area."

$Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$Handle = [IntPtr]::Zero
while ((Get-Date) -lt $Deadline) {
    $Candidate = [Native.Win]::FindWindowW("#32768", $null)
    if ($Candidate -ne [IntPtr]::Zero -and [Native.Win]::IsWindowVisible($Candidate)) {
        $Handle = $Candidate
        break
    }
    Start-Sleep -Milliseconds 100
}

if ($Handle -eq [IntPtr]::Zero) {
    throw "No popup menu appeared within $TimeoutSeconds seconds."
}

# Let the menu finish its open animation before the pixels are read.
Start-Sleep -Milliseconds 350

$Rect = New-Object Native.Win+RECT
if ([Native.Win]::DwmGetWindowAttribute($Handle, $DwmExtendedFrameBounds, [ref]$Rect, 16) -ne 0) {
    $null = [Native.Win]::GetWindowRect($Handle, [ref]$Rect)
}

$Width = $Rect.Right - $Rect.Left
$Height = $Rect.Bottom - $Rect.Top
if ($Width -le 0 -or $Height -le 0) {
    throw "The menu reported an empty rectangle."
}

$Bitmap = New-Object Drawing.Bitmap($Width, $Height, [Drawing.Imaging.PixelFormat]::Format24bppRgb)
$Graphics = [Drawing.Graphics]::FromImage($Bitmap)
try {
    $Graphics.CopyFromScreen($Rect.Left, $Rect.Top, 0, 0, (New-Object Drawing.Size($Width, $Height)))
    $Bitmap.Save($OutputPath, [Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $Graphics.Dispose()
    $Bitmap.Dispose()
}

"Created $OutputPath ($Width x $Height)"
