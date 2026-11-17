[CmdletBinding()]
param(
    [switch]$SkipExecutable,
    [switch]$UnsignedManualRelease,
    [string]$WixBin
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$version = ([string](& (Join-Path $PSScriptRoot "get-version.ps1"))).Trim()

if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Cargo.toml does not contain a stable semantic version"
}

if (-not $SkipExecutable) {
    & (Join-Path $PSScriptRoot "build-exe.ps1")
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

$candleDefinitions = @()
if ($UnsignedManualRelease) {
    $candleDefinitions += "-dUnsignedManualRelease=1"
}

& $candle `
    -nologo `
    -arch x64 `
    -ext WixUtilExtension `
    "-dProductVersion=$version" `
    "-dSourceDir=$(Join-Path $root 'dist')" `
    "-dProjectDir=$root" `
    @candleDefinitions `
    -out $wixObject `
    (Join-Path $root "installer\Chunes.wxs")
if ($LASTEXITCODE -ne 0) {
    throw "WiX candle failed with exit code $LASTEXITCODE"
}

& $light `
    -nologo `
    -ext WixUIExtension `
    -ext WixUtilExtension `
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
