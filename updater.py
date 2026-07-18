"""Secure GitHub release updater for Chunes."""

import base64
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

import settings
from version import __version__


LATEST_RELEASE_URL = "https://api.github.com/repos/getchunes/chunes/releases/latest"
EXPECTED_PUBLISHER = "SignPath Foundation"
EXPECTED_PRODUCT_NAME = "Chunes"
EXPECTED_MANUFACTURER = "Chunes"
EXPECTED_UPGRADE_CODE = "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}"
MAX_API_BYTES = 1_000_000
MAX_DOWNLOAD_BYTES = 250 * 1024 * 1024
_VERSION_RE = re.compile(
    r"(?:v)?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
_DIGEST_RE = re.compile(r"sha256:([0-9a-fA-F]{64})")
_DOWNLOAD_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}

_UPDATE_HELPER_SCRIPT = r'''
$ErrorActionPreference = "Stop"
$verifiedInstaller = $false

function Write-ChunesUpdateLog([string]$message) {
    try {
        $directory = Join-Path $env:LOCALAPPDATA "Chunes"
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        Add-Content -LiteralPath (Join-Path $directory "chunes.log") -Encoding UTF8 -Value "Update helper: $message"
    } catch {
    }
}

function Start-PreviousChunes {
    foreach ($parentIdText in ($env:CHUNES_PARENT_PIDS -split ',')) {
        $runningParent = Get-Process -Id ([int]$parentIdText) -ErrorAction SilentlyContinue
        if ($runningParent) {
            Write-ChunesUpdateLog "Previous Chunes process is still running; no duplicate was started."
            return
        }
    }
    if (-not (Test-Path -LiteralPath $env:CHUNES_OLD_EXE -PathType Leaf)) {
        return
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $env:CHUNES_OLD_EXE).Hash.ToLowerInvariant()
    if ($actual -eq $env:CHUNES_OLD_EXE_SHA256) {
        Start-Process -FilePath $env:CHUNES_OLD_EXE
    } else {
        Write-ChunesUpdateLog "Previous executable changed; it was not restarted."
    }
}

try {
    foreach ($parentIdText in ($env:CHUNES_PARENT_PIDS -split ',')) {
        $parentId = 0
        if (-not [int]::TryParse($parentIdText, [ref]$parentId)) {
            throw "Invalid parent process identifier"
        }
        $parentProcess = Get-Process -Id $parentId -ErrorAction SilentlyContinue
        if ($parentProcess -and -not $parentProcess.WaitForExit(60000)) {
            throw "Chunes did not exit before installation"
        }
    }

    $msi = [IO.Path]::GetFullPath($env:CHUNES_UPDATE_MSI)
    $expectedName = "Chunes-$($env:CHUNES_UPDATE_VERSION)-x64.msi"
    if ([IO.Path]::GetFileName($msi) -cne $expectedName) {
        throw "Unexpected installer filename"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $msi).Hash.ToLowerInvariant()
    if ($actualHash -cne $env:CHUNES_UPDATE_SHA256) {
        throw "Installer SHA-256 changed after verification"
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $msi
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Installer Authenticode signature is not trusted: $($signature.Status)"
    }
    $publisher = $signature.SignerCertificate.GetNameInfo(
        [System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
        $false
    )
    if ($publisher -cne $env:CHUNES_EXPECTED_PUBLISHER) {
        throw "Unexpected installer publisher"
    }

    $installer = $null
    $database = $null
    $view = $null
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        $database = $installer.OpenDatabase($msi, 0)
        $view = $database.OpenView('SELECT `Property`, `Value` FROM `Property`')
        $view.Execute()
        $properties = @{}
        while ($record = $view.Fetch()) {
            $properties[$record.StringData(1)] = $record.StringData(2)
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($record)
        }
        $expected = @{
            ProductName = $env:CHUNES_EXPECTED_PRODUCT_NAME
            ProductVersion = $env:CHUNES_UPDATE_VERSION
            Manufacturer = $env:CHUNES_EXPECTED_MANUFACTURER
            UpgradeCode = $env:CHUNES_EXPECTED_UPGRADE_CODE
        }
        foreach ($name in $expected.Keys) {
            if ($properties[$name] -cne $expected[$name]) {
                throw "Unexpected installer $name"
            }
        }
    } finally {
        if ($view) {
            $view.Close()
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($view)
        }
        if ($database) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($database)
        }
        if ($installer) {
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
        }
    }
    $verifiedInstaller = $true

    $msiexec = Join-Path $env:SystemRoot "System32\msiexec.exe"
    $arguments = '/i "{0}" /quiet /norestart' -f $msi
    $process = Start-Process -FilePath $msiexec -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Windows Installer exited with code $($process.ExitCode)"
    }

    $installPath = (Get-ItemProperty -LiteralPath "HKCU:\Software\Chunes" -Name InstallPath).InstallPath
    $newExecutable = Join-Path $installPath "Chunes.exe"
    if (-not (Test-Path -LiteralPath $newExecutable -PathType Leaf)) {
        throw "Installed Chunes executable was not found"
    }
    Start-Process -FilePath $newExecutable
    Write-ChunesUpdateLog "Installation succeeded and Chunes was restarted."
    exit 0
} catch {
    $failure = $_.Exception.Message
    Write-ChunesUpdateLog $failure
    if (-not $verifiedInstaller) {
        Remove-Item -LiteralPath $env:CHUNES_UPDATE_MSI -Force -ErrorAction SilentlyContinue
    }
    try {
        Start-PreviousChunes
    } catch {
        Write-ChunesUpdateLog "Could not restart the previous Chunes executable."
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            "The Chunes update did not complete. Chunes attempted to restart the previous version.`n`n$failure",
            "Chunes update",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    } catch {
    }
    exit 1
}
'''


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    version: str
    filename: str
    url: str
    sha256: str
    size: int


def parse_version(value):
    if not isinstance(value, str):
        raise ValueError("version must be a string")
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid stable version: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate, current=__version__):
    return parse_version(candidate) > parse_version(current)


def asset_filename(version):
    normalized = ".".join(str(part) for part in parse_version(version))
    return f"Chunes-{normalized}-x64.msi"


def parse_digest(value):
    if not isinstance(value, str):
        raise ValueError("release asset has no SHA-256 digest")
    match = _DIGEST_RE.fullmatch(value.strip())
    if not match:
        raise ValueError("release asset has an invalid SHA-256 digest")
    return match.group(1).lower()


def _validate_asset_url(url, version, filename):
    if not isinstance(url, str):
        raise ValueError("release asset has no download URL")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("release asset has an unexpected download URL")
    parts = parsed.path.split("/")
    if (
        len(parts) != 7
        or parts[1:5] != ["getchunes", "chunes", "releases", "download"]
        or parts[5] not in {version, f"v{version}"}
        or parts[6] != filename
    ):
        raise ValueError("release asset has an unexpected download URL")
    return url


def select_release_asset(release, current=__version__):
    """Select the one expected MSI from a newer stable GitHub release."""
    if not isinstance(release, dict):
        raise ValueError("invalid release response")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("latest release is not stable")

    version_tuple = parse_version(release.get("tag_name"))
    version = ".".join(str(part) for part in version_tuple)
    if version_tuple <= parse_version(current):
        return None
    if release.get("immutable") is not True:
        raise ValueError("newer release is not immutable")

    expected_name = asset_filename(version)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release has no asset list")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise ValueError(f"release must contain exactly one {expected_name} asset")

    asset = matches[0]
    if asset.get("state") != "uploaded":
        raise ValueError("release asset is not ready to download")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_DOWNLOAD_BYTES:
        raise ValueError("release asset has an invalid size")
    return ReleaseAsset(
        version=version,
        filename=expected_name,
        url=_validate_asset_url(
            asset.get("browser_download_url"), version, expected_name
        ),
        sha256=parse_digest(asset.get("digest")),
        size=size,
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path, expected):
    try:
        normalized = parse_digest(f"sha256:{expected}")
    except ValueError as exc:
        raise UpdateError("Invalid expected SHA-256 digest") from exc
    actual = sha256_file(path)
    if not hmac.compare_digest(actual, normalized):
        raise UpdateError(
            "Downloaded installer SHA-256 does not match the release API"
        )
    return actual


def fetch_latest_release():
    request = urllib.request.Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": f"Chunes/{__version__}",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response_url = urllib.parse.urlsplit(response.geturl())
        if (
            response_url.scheme != "https"
            or response_url.hostname != "api.github.com"
        ):
            raise UpdateError("Release check redirected off GitHub")
        body = response.read(MAX_API_BYTES + 1)
    if len(body) > MAX_API_BYTES:
        raise UpdateError("GitHub release response is too large")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("GitHub returned an invalid release response") from exc


def download_asset(asset):
    update_dir = (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Chunes" / "Updates"
    )
    update_dir.mkdir(parents=True, exist_ok=True)
    destination = update_dir / asset.filename
    partial = destination.with_suffix(".msi.part")
    partial.unlink(missing_ok=True)

    request = urllib.request.Request(
        asset.url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": f"Chunes/{__version__}",
        },
    )
    written = 0
    try:
        with (
            urllib.request.urlopen(request, timeout=30) as response,
            open(partial, "wb") as output,
        ):
            response_url = urllib.parse.urlsplit(response.geturl())
            if (
                response_url.scheme != "https"
                or response_url.hostname not in _DOWNLOAD_HOSTS
            ):
                raise UpdateError("Installer download redirected off GitHub")
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) != asset.size
            ):
                raise UpdateError("Installer download has an invalid size")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > asset.size:
                    raise UpdateError("Installer download is larger than expected")
                output.write(chunk)
        if written != asset.size:
            raise UpdateError(
                "Installer download size does not match the release API"
            )
        os.replace(partial, destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def _certificate_name(crypt32, certificate, name_type):
    get_name = crypt32.CertGetNameStringW
    get_name.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_name.restype = wintypes.DWORD
    length = get_name(certificate, name_type, 0, None, None, 0)
    if length <= 1:
        return ""
    buffer = ctypes.create_unicode_buffer(length)
    if not get_name(certificate, name_type, 0, None, buffer, length):
        raise UpdateError("Could not read the installer signing certificate")
    return buffer.value


def verify_authenticode(path, expected_publisher=EXPECTED_PUBLISHER):
    """Verify MSI trust, signed hash, and exact signer through Windows APIs."""
    if os.name != "nt":
        raise UpdateError("Authenticode verification is only available on Windows")
    path = Path(path).resolve()
    if path.suffix.lower() != ".msi" or not path.is_file():
        raise UpdateError("Update asset is not an MSI")

    msi = ctypes.WinDLL("msi", use_last_error=True)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    get_signature = msi.MsiGetFileSignatureInformationW
    get_signature.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPBYTE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_signature.restype = ctypes.c_long
    free_certificate = crypt32.CertFreeCertificateContext
    free_certificate.argtypes = [wintypes.LPVOID]
    free_certificate.restype = wintypes.BOOL

    certificate = wintypes.LPVOID()
    result = get_signature(str(path), 0x1, ctypes.byref(certificate), None, None)
    if result != 0:
        code = ctypes.c_uint32(result).value
        raise UpdateError(
            f"Installer Authenticode trust verification failed (0x{code:08X})"
        )
    if not certificate.value:
        raise UpdateError("Installer has no Authenticode signer certificate")
    try:
        publisher = _certificate_name(crypt32, certificate, 4)
        if publisher != expected_publisher:
            raise UpdateError(
                f"Installer publisher is {publisher!r}, "
                f"expected {expected_publisher!r}"
            )
    finally:
        free_certificate(certificate)
    return publisher


def _read_msi_properties(path):
    """Read the MSI Property table through Windows Installer in read-only mode."""
    if os.name != "nt":
        raise UpdateError("MSI identity verification is only available on Windows")
    path = Path(path).resolve()
    if path.suffix.lower() != ".msi" or not path.is_file():
        raise UpdateError("Update asset is not an MSI")

    msi = ctypes.WinDLL("msi", use_last_error=True)
    open_database = msi.MsiOpenDatabaseW
    open_database.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.UINT),
    ]
    open_database.restype = wintypes.UINT
    open_view = msi.MsiDatabaseOpenViewW
    open_view.argtypes = [
        wintypes.UINT,
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.UINT),
    ]
    open_view.restype = wintypes.UINT
    execute_view = msi.MsiViewExecute
    execute_view.argtypes = [wintypes.UINT, wintypes.UINT]
    execute_view.restype = wintypes.UINT
    fetch_record = msi.MsiViewFetch
    fetch_record.argtypes = [wintypes.UINT, ctypes.POINTER(wintypes.UINT)]
    fetch_record.restype = wintypes.UINT
    get_string = msi.MsiRecordGetStringW
    get_string.argtypes = [
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_string.restype = wintypes.UINT
    close_handle = msi.MsiCloseHandle
    close_handle.argtypes = [wintypes.UINT]
    close_handle.restype = wintypes.UINT

    def require_success(result, operation):
        if result != 0:
            raise UpdateError(
                f"Windows Installer could not {operation} (error {result})"
            )

    def record_string(record, field):
        length = wintypes.DWORD(0)
        result = get_string(record, field, None, ctypes.byref(length))
        if result not in (0, 234):
            require_success(result, "read package identity")
        buffer = ctypes.create_unicode_buffer(length.value + 1)
        capacity = wintypes.DWORD(len(buffer))
        require_success(
            get_string(record, field, buffer, ctypes.byref(capacity)),
            "read package identity",
        )
        return buffer.value

    database = wintypes.UINT()
    view = wintypes.UINT()
    require_success(
        open_database(str(path), None, ctypes.byref(database)),
        "open the update package",
    )
    try:
        require_success(
            open_view(
                database.value,
                "SELECT `Property`, `Value` FROM `Property`",
                ctypes.byref(view),
            ),
            "query package identity",
        )
        try:
            require_success(execute_view(view.value, 0), "query package identity")
            properties = {}
            while True:
                record = wintypes.UINT()
                result = fetch_record(view.value, ctypes.byref(record))
                if result == 259:
                    break
                require_success(result, "read package identity")
                try:
                    properties[record_string(record.value, 1)] = record_string(
                        record.value, 2
                    )
                finally:
                    close_handle(record.value)
            return properties
        finally:
            if view.value:
                close_handle(view.value)
    finally:
        if database.value:
            close_handle(database.value)


def verify_msi_identity(path, expected_version):
    version = ".".join(str(part) for part in parse_version(expected_version))
    expected = {
        "ProductName": EXPECTED_PRODUCT_NAME,
        "ProductVersion": version,
        "Manufacturer": EXPECTED_MANUFACTURER,
        "UpgradeCode": EXPECTED_UPGRADE_CODE,
    }
    properties = _read_msi_properties(path)
    for name, value in expected.items():
        if properties.get(name) != value:
            raise UpdateError(
                f"Installer {name} is {properties.get(name)!r}, expected {value!r}"
            )
    return expected


def launch_install_helper(asset, path):
    """Hand a verified MSI to a trusted system helper before this app exits."""
    if not getattr(sys, "frozen", False):
        raise UpdateError("Updates can only be installed by packaged Chunes")
    path = Path(path).resolve()
    old_executable = Path(sys.executable).resolve()
    if (
        path.name != asset.filename
        or path.name != asset_filename(asset.version)
        or not path.is_file()
        or not old_executable.is_file()
    ):
        raise UpdateError("Verified update files could not be found")

    powershell = (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    if not powershell.is_file():
        raise UpdateError("Windows PowerShell could not be found")

    parent_ids = [os.getpid()]
    bootloader_parent = os.getppid()
    if bootloader_parent not in parent_ids:
        parent_ids.append(bootloader_parent)
    environment = os.environ.copy()
    environment.update(
        {
            "CHUNES_UPDATE_MSI": str(path),
            "CHUNES_UPDATE_SHA256": parse_digest(f"sha256:{asset.sha256}"),
            "CHUNES_UPDATE_VERSION": asset.version,
            "CHUNES_OLD_EXE": str(old_executable),
            "CHUNES_OLD_EXE_SHA256": sha256_file(old_executable),
            "CHUNES_PARENT_PIDS": ",".join(str(value) for value in parent_ids),
            "CHUNES_EXPECTED_PUBLISHER": EXPECTED_PUBLISHER,
            "CHUNES_EXPECTED_PRODUCT_NAME": EXPECTED_PRODUCT_NAME,
            "CHUNES_EXPECTED_MANUFACTURER": EXPECTED_MANUFACTURER,
            "CHUNES_EXPECTED_UPGRADE_CODE": EXPECTED_UPGRADE_CODE,
        }
    )
    encoded_script = base64.b64encode(
        _UPDATE_HELPER_SCRIPT.encode("utf-16-le")
    ).decode("ascii")
    subprocess.Popen(
        [
            str(powershell),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_script,
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        env=environment,
    )


def verify_and_launch(asset, path):
    """Start the update helper only after all independent checks pass."""
    verify_sha256(path, asset.sha256)
    publisher = verify_authenticode(path)
    verify_msi_identity(path, asset.version)
    print(
        f"Verified {asset.filename} SHA-256, trusted publisher {publisher}, "
        "and Chunes MSI identity; preparing Windows Installer."
    )
    launch_install_helper(asset, path)


def _message(text, flags):
    if os.name != "nt":
        print(text)
        return 0
    return ctypes.windll.user32.MessageBoxW(None, text, "Chunes", flags)


def _ask_download(asset):
    result = _message(
        f"Chunes {asset.version} is available.\n\n"
        "This future update must pass SignPath Foundation verification "
        "before Chunes will install it.\n\n"
        "Download and install the update now?",
        0x00000004 | 0x00000020 | 0x00000100,
    )
    return result == 6


class UpdateController:
    def __init__(self, on_install, current_version=__version__):
        self.on_install = on_install
        self.current_version = current_version
        self._lock = threading.Lock()
        self._checking = False

    def start_automatic_check(self, delay=8):
        self._start(manual=False, delay=delay)

    def check_now(self):
        if not self._start(manual=True, delay=0):
            _message("An update check is already in progress.", 0x00000040)

    def _start(self, manual, delay):
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        threading.Thread(
            target=self._run,
            args=(manual, delay),
            daemon=True,
            name="Chunes updater",
        ).start()
        return True

    def _run(self, manual, delay):
        installer = None
        handed_off = False
        try:
            if delay:
                time.sleep(delay)
            if not manual and not settings.automatic_updates_enabled():
                print("Automatic update check canceled because it was turned off.")
                return
            print("Checking GitHub for Chunes updates...")
            asset = select_release_asset(
                fetch_latest_release(), self.current_version
            )
            if asset is None:
                print("Chunes is up to date.")
                if manual:
                    _message(
                        f"Chunes {self.current_version} is up to date.",
                        0x00000040,
                    )
                return
            if not _ask_download(asset):
                print(f"Update {asset.version} declined.")
                return

            print(f"Downloading {asset.filename}...")
            installer = download_asset(asset)
            verify_and_launch(asset, installer)
            handed_off = True
            self.on_install()
        except Exception as exc:
            if installer and not handed_off:
                try:
                    Path(installer).unlink(missing_ok=True)
                except OSError:
                    pass
            message = f"Update failed: {type(exc).__name__}: {exc}"
            print(message)
            if manual:
                _message(message, 0x00000010)
        finally:
            with self._lock:
                self._checking = False
