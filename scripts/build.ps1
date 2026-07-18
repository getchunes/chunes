[CmdletBinding()]
param(
    [switch]$SkipExecutable,
    [string]$WixBin
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

$wixBinProvided = $PSBoundParameters.ContainsKey("WixBin")
if (-not $wixBinProvided) {
    $WixBin = Join-Path ${env:ProgramFiles(x86)} "WiX Toolset v3.14\bin"
}
$candle = $null
$light = $null
if (-not $wixBinProvided) {
    $candle = (Get-Command candle.exe -ErrorAction SilentlyContinue).Source
    $light = (Get-Command light.exe -ErrorAction SilentlyContinue).Source
}
if (-not $candle -and (Test-Path -LiteralPath (Join-Path $WixBin "candle.exe") -PathType Leaf)) {
    $candle = Join-Path $WixBin "candle.exe"
}
if (-not $light -and (Test-Path -LiteralPath (Join-Path $WixBin "light.exe") -PathType Leaf)) {
    $light = Join-Path $WixBin "light.exe"
}
if (-not $candle -or -not $light) {
    throw "WiX Toolset 3.14 candle.exe and light.exe are required"
}

$installerBuild = Join-Path $root "build\installer"
New-Item -ItemType Directory -Path $installerBuild -Force | Out-Null
$wixObject = Join-Path $installerBuild "Chunes.wixobj"
$msi = Join-Path $root "dist\Chunes-$version-x64.msi"

& $candle `
    -nologo `
    -arch x64 `
    "-dProductVersion=$version" `
    "-dSourceDir=$(Join-Path $root 'dist')" `
    "-dProjectDir=$root" `
    -out $wixObject `
    (Join-Path $root "installer\Chunes.wxs")
if ($LASTEXITCODE -ne 0) {
    throw "WiX candle failed with exit code $LASTEXITCODE"
}

& $light `
    -nologo `
    -ext WixUIExtension `
    -cultures:en-us `
    -sice:ICE91 `
    -out $msi `
    $wixObject
if ($LASTEXITCODE -ne 0) {
    throw "WiX light failed with exit code $LASTEXITCODE"
}

if (-not (Test-Path -LiteralPath $msi)) {
    throw "Expected MSI was not produced: $msi"
}

$msi
