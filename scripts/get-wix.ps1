[CmdletBinding()]
param(
    [string]$Destination = (Join-Path ([IO.Path]::GetTempPath()) "chunes-wix-3.14.1")
)

$ErrorActionPreference = "Stop"
$url = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"
$expectedHash = "6AC824E1642D6F7277D0ED7EA09411A508F6116BA6FAE0AA5F2C7DAA2FF43D31"
$parent = Split-Path -Parent $Destination

if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "WiX destination parent does not exist: $parent"
}
if (Test-Path -LiteralPath $Destination) {
    throw "WiX destination already exists: $Destination"
}

$archive = "$Destination.zip"
try {
    Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
    if ($actualHash -cne $expectedHash) {
        throw "WiX archive SHA-256 mismatch: $actualHash"
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $Destination
} finally {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
}

foreach ($tool in ("candle.exe", "light.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $Destination $tool) -PathType Leaf)) {
        throw "Verified WiX archive did not contain $tool"
    }
}

$Destination
