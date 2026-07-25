[CmdletBinding()]
param(
    [switch]$SkipExecutable,
    [switch]$SelfSign,
    [string]$SdkBin
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

# The Microsoft Store reserves the fourth part of a package version.
$packageVersion = "$version.0"

function Get-SdkTool([string]$name) {
    if ($SdkBin) {
        $candidate = Join-Path $SdkBin $name
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
        throw "$name is not in the requested SDK bin directory: $SdkBin"
    }
    $roots = @(
        (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
        (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
    $found = @(
        $roots | ForEach-Object {
            Get-ChildItem -LiteralPath $_ -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^10\.\d+\.\d+\.\d+$' } |
                ForEach-Object {
                    $tool = Join-Path $_.FullName "x64\$name"
                    if (Test-Path -LiteralPath $tool -PathType Leaf) {
                        [pscustomobject]@{ Version = [version]$_.Name; Path = $tool }
                    }
                }
        }
    )
    if ($found.Count -eq 0) {
        throw "$name was not found; install the Windows 10 SDK packaging tools"
    }
    return ($found | Sort-Object Version -Descending | Select-Object -First 1).Path
}

$makeappx = Get-SdkTool "makeappx.exe"
$makepri = Get-SdkTool "makepri.exe"

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

$executable = Join-Path $root "dist\Chunes.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Expected a built executable at $executable"
}

$msixBuild = Join-Path $root "build\msix"
$staging = Join-Path $msixBuild "package"
Remove-Item -LiteralPath $msixBuild -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $staging | Out-Null

Copy-Item -LiteralPath $executable -Destination (Join-Path $staging "Chunes.exe")
Copy-Item -LiteralPath (Join-Path $root "installer\msix\assets") `
    -Destination (Join-Path $staging "assets") -Recurse

$manifestSource = Join-Path $root "installer\msix\AppxManifest.xml"
[xml]$manifest = Get-Content -LiteralPath $manifestSource -Raw
$identity = $manifest.Package.Identity
if (-not $identity) {
    throw "The source manifest has no Identity element"
}
$identity.Version = $packageVersion
$stagedManifest = Join-Path $staging "AppxManifest.xml"
$manifest.Save($stagedManifest)

# Scale and target-size qualified logos only resolve through a resource index.
$priConfig = Join-Path $msixBuild "priconfig.xml"
& $makepri createconfig /ConfigXml $priConfig /Default en-US /Overwrite | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "makepri createconfig failed with exit code $LASTEXITCODE"
}

# The default config splits each scale into its own resource pack, which only
# a bundle can carry. Chunes ships one package, so index every scale together.
[xml]$priDocument = Get-Content -LiteralPath $priConfig -Raw
$packagingNode = $priDocument.resources.packaging
if ($packagingNode) {
    $priDocument.resources.RemoveChild($packagingNode) | Out-Null
    $priDocument.Save($priConfig)
}

& $makepri new /ProjectRoot $staging /ConfigXml $priConfig `
    /OutputFile (Join-Path $staging "resources.pri") /Overwrite | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "makepri new failed with exit code $LASTEXITCODE"
}

$msix = Join-Path $root "dist\Chunes-$version-x64.msix"
Remove-Item -LiteralPath $msix -Force -ErrorAction SilentlyContinue
& $makeappx pack /d $staging /p $msix /o | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "makeappx pack failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path -LiteralPath $msix -PathType Leaf)) {
    throw "Expected MSIX was not produced: $msix"
}

if ($SelfSign) {
    # Development sideloading only. Store submissions are signed by Partner
    # Center, and a package signed here would be rejected on upload.
    $signtool = Get-SdkTool "signtool.exe"
    $subject = $manifest.Package.Identity.Publisher
    $certificate = Get-ChildItem -Path Cert:\CurrentUser\My |
        Where-Object { $_.Subject -ceq $subject } |
        Sort-Object NotAfter -Descending |
        Select-Object -First 1
    if (-not $certificate) {
        $certificate = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $subject `
            -KeyUsage DigitalSignature `
            -CertStoreLocation Cert:\CurrentUser\My `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    }
    & $signtool sign /fd SHA256 /sha1 $certificate.Thumbprint $msix | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "signtool failed with exit code $LASTEXITCODE"
    }
    $export = Join-Path $root "dist\Chunes-devtest.cer"
    Export-Certificate -Cert $certificate -FilePath $export -Force | Out-Null
    Write-Host "Development certificate exported to $export"
    Write-Host "Trust it once from an elevated prompt, then install the MSIX:"
    Write-Host "  Import-Certificate -FilePath '$export' -CertStoreLocation Cert:\LocalMachine\TrustedPeople"
}

$msix
