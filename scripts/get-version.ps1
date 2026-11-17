[CmdletBinding()]
param()

# Prints the [package] version from Cargo.toml, the single version source for
# Chunes.exe, the MSI, and the MSIX.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$cargoManifest = Join-Path $root "Cargo.toml"

$inPackage = $false
$version = $null
foreach ($line in @(Get-Content -LiteralPath $cargoManifest)) {
    if ($line -match '^\s*\[') {
        $inPackage = $line -cmatch '^\s*\[package\]\s*(#.*)?$'
        continue
    }
    if ($inPackage -and $line -cmatch '^\s*version\s*=\s*"([^"]*)"\s*(#.*)?$') {
        $version = $Matches[1]
        break
    }
}

if (-not $version) {
    throw "Cargo.toml has no [package] version"
}
if ($version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Cargo.toml does not contain a stable semantic version"
}

$version
