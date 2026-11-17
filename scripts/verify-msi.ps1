[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [Parameter(Mandatory = $true)]
    [string]$ExpectedVersion
)

# Checks that a built MSI identifies itself as this Chunes release.
#
# The build and release workflows run this against every MSI they produce, so
# an installer with the wrong product, version, manufacturer, or UpgradeCode
# never reaches a release. Throws on any mismatch.

$ErrorActionPreference = "Stop"

$expectedProductName = "Chunes"
$expectedManufacturer = "Chunes"
$expectedUpgradeCode = "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}"

function Format-Value($value) {
    if ($null -eq $value) {
        return "None"
    }
    return "'$value'"
}

function Get-NormalizedVersion([string]$value) {
    # The pattern rejects leading zeros, so dropping an optional "v" is the
    # whole normalization.
    if (-not ($value.Trim() -cmatch '^v?((0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*))$')) {
        throw "invalid stable version: $(Format-Value $value)"
    }
    return $Matches[1]
}

function Invoke-ComMember($target, [string]$name, [Reflection.BindingFlags]$kind, [object[]]$arguments) {
    return $target.GetType().InvokeMember($name, $kind, $null, $target, $arguments)
}

function Read-MsiProperties([string]$msiPath) {
    # Reads the MSI Property table through Windows Installer in read-only mode.
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "MSI identity verification is only available on Windows"
    }
    if (-not (Test-Path -LiteralPath $msiPath -PathType Leaf)) {
        throw "Not an MSI"
    }
    $resolved = (Resolve-Path -LiteralPath $msiPath).ProviderPath
    if ([IO.Path]::GetExtension($resolved) -ne ".msi") {
        throw "Not an MSI"
    }

    $invoke = [Reflection.BindingFlags]::InvokeMethod
    $get = [Reflection.BindingFlags]::GetProperty
    $installer = $null
    $database = $null
    $view = $null
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        try {
            # 0 is msiOpenDatabaseModeReadOnly.
            $database = Invoke-ComMember $installer "OpenDatabase" $invoke @($resolved, 0)
        } catch {
            throw "Windows Installer could not open the package ($($_.Exception.Message))"
        }
        # Property names are case-sensitive, unlike a default hashtable's keys.
        $properties = [System.Collections.Hashtable]::new([System.StringComparer]::Ordinal)
        try {
            $view = Invoke-ComMember $database "OpenView" $invoke @('SELECT `Property`, `Value` FROM `Property`')
            Invoke-ComMember $view "Execute" $invoke $null | Out-Null
            while ($true) {
                $record = Invoke-ComMember $view "Fetch" $invoke $null
                if ($null -eq $record) {
                    break
                }
                try {
                    $name = [string](Invoke-ComMember $record "StringData" $get @(1))
                    $value = [string](Invoke-ComMember $record "StringData" $get @(2))
                    $properties[$name] = $value
                } finally {
                    [void][Runtime.InteropServices.Marshal]::ReleaseComObject($record)
                }
            }
        } catch {
            throw "Windows Installer could not read package identity ($($_.Exception.Message))"
        }
        return $properties
    } finally {
        if ($null -ne $view) {
            try {
                Invoke-ComMember $view "Close" $invoke $null | Out-Null
            } catch {
                # Closing a view that failed to open is not an identity error.
                $null = $_
            }
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($view)
        }
        if ($null -ne $database) {
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($database)
        }
        if ($null -ne $installer) {
            [void][Runtime.InteropServices.Marshal]::ReleaseComObject($installer)
        }
    }
}

$version = Get-NormalizedVersion $ExpectedVersion
$expected = [ordered]@{
    ProductName = $expectedProductName
    ProductVersion = $version
    Manufacturer = $expectedManufacturer
    UpgradeCode = $expectedUpgradeCode
}
$properties = Read-MsiProperties $Path
foreach ($name in $expected.Keys) {
    $actual = $null
    if ($properties.ContainsKey($name)) {
        $actual = $properties[$name]
    }
    if ($actual -cne $expected[$name]) {
        throw "Installer $name is $(Format-Value $actual), expected $(Format-Value $expected[$name])"
    }
}

Write-Host "MSI identity verified: $expectedProductName $version"
