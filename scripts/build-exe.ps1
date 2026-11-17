[CmdletBinding()]
param()

# Builds the x64 release Chunes.exe with cargo and copies it to dist\Chunes.exe,
# the one file the MSI and the MSIX package.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

# rustup picks the pinned toolchain from rust-toolchain.toml in the current
# directory, so cargo has to run from the repository root.
Push-Location -LiteralPath $root
try {
    & cargo build --locked --release
    if ($LASTEXITCODE -ne 0) {
        throw "cargo build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$built = Join-Path $root "target\release\Chunes.exe"
if (-not (Test-Path -LiteralPath $built -PathType Leaf)) {
    throw "Expected a built executable at $built"
}

# IMAGE_FILE_MACHINE_AMD64 in the PE file header.
$bytes = [IO.File]::ReadAllBytes($built)
if ($bytes.Length -lt 64 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) {
    throw "$built is not a Windows executable"
}
$peOffset = [BitConverter]::ToInt32($bytes, 0x3C)
if ($peOffset -lt 0 -or ($peOffset + 6) -gt $bytes.Length) {
    throw "$built has no PE header"
}
if ([BitConverter]::ToUInt32($bytes, $peOffset) -ne 0x00004550) {
    throw "$built has no PE signature"
}
$machine = [BitConverter]::ToUInt16($bytes, $peOffset + 4)
if ($machine -ne 0x8664) {
    throw ("Chunes.exe must be an x64 executable, not machine type 0x{0:X4}" -f $machine)
}

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Path $dist -Force | Out-Null
Copy-Item -LiteralPath $built -Destination (Join-Path $dist "Chunes.exe") -Force
