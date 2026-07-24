[CmdletBinding()]
param(
    [switch]$SkipExecutable,
    [string]$MakeAppxPath
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$version = (& python -c "from version import __version__; print(__version__)").Trim()

if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "version.py does not contain a stable semantic version"
}
if (-not [Environment]::Is64BitProcess) {
    throw "Chunes must be built with 64-bit Python"
}

# Step 1: Build PyInstaller executable if not skipped
if (-not $SkipExecutable) {
    & python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath (Join-Path $root "dist") `
        --workpath (Join-Path $root "build\pyinstaller") `
        (Join-Path $root "Chunes.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}

$exePath = Join-Path $root "dist\Chunes.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Executable missing at $exePath"
}

# Step 2: Prepare MSIX Staging Directory
$stagingDir = Join-Path $root "build\msix_staging"
if (Test-Path -LiteralPath $stagingDir) {
    Remove-Item -Recurse -Force $stagingDir
}
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null

# Copy Chunes.exe
Copy-Item -LiteralPath $exePath -Destination (Join-Path $stagingDir "Chunes.exe")

# Prepare Assets directory inside staging
$assetsStaging = Join-Path $stagingDir "Assets"
New-Item -ItemType Directory -Path $assetsStaging -Force | Out-Null

# Generate required MSIX tiles using Python PIL
$logoSource = Join-Path $root "assets\logo-512.png"
& python -c "
from PIL import Image
import sys

src = sys.argv[1]
out_dir = sys.argv[2]
img = Image.open(src).convert('RGBA')

img.resize((50, 50), Image.Resampling.LANCZOS).save(f'{out_dir}/StoreLogo.png')
img.resize((150, 150), Image.Resampling.LANCZOS).save(f'{out_dir}/Square150x150Logo.png')
img.resize((44, 44), Image.Resampling.LANCZOS).save(f'{out_dir}/Square44x44Logo.png')

# Wide tile (310x150) centered
wide = Image.new('RGBA', (310, 150), (43, 45, 54, 255))
logo_s = img.resize((120, 120), Image.Resampling.LANCZOS)
wide.paste(logo_s, ((310 - 120)//2, (150 - 120)//2), logo_s)
wide.save(f'{out_dir}/Wide310x150Logo.png')
" $logoSource $assetsStaging

# Step 3: Populate AppxManifest.xml
$manifestTemplate = Get-Content -LiteralPath (Join-Path $root "installer\AppxManifest.xml") -Raw
$fourPartVersion = "$version.0"
$manifestContent = $manifestTemplate -replace '__VERSION__', $fourPartVersion
$manifestStagingPath = Join-Path $stagingDir "AppxManifest.xml"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($manifestStagingPath, $manifestContent, $utf8NoBom)

# Step 4: Find makeappx.exe
if (-not $MakeAppxPath) {
    $found = Get-ChildItem 'C:\Program Files (x86)\Windows Kits\10\bin\*\x64\makeappx.exe' -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($found) {
        $MakeAppxPath = $found.FullName
    } else {
        $MakeAppxPath = (Get-Command makeappx.exe -ErrorAction SilentlyContinue).Source
    }
}

if (-not $MakeAppxPath -or -not (Test-Path -LiteralPath $MakeAppxPath)) {
    throw "makeappx.exe not found. Please install Windows SDK."
}

# Step 5: Pack MSIX
$msixOutput = Join-Path $root "dist\Chunes-$version-x64.msix"
& $MakeAppxPath pack /d $stagingDir /p $msixOutput /o
if ($LASTEXITCODE -ne 0) {
    throw "makeappx.exe failed with exit code $LASTEXITCODE"
}

Write-Host "Successfully built MSIX package: $msixOutput" -ForegroundColor Green
$msixOutput
