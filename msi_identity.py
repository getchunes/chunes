"""Check that a built MSI identifies itself as this Chunes release.

The build and release workflows run this against every MSI they produce, so
an installer with the wrong product, version, manufacturer, or UpgradeCode
never reaches a release.
"""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re


EXPECTED_PRODUCT_NAME = "Chunes"
EXPECTED_MANUFACTURER = "Chunes"
EXPECTED_UPGRADE_CODE = "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}"
_VERSION_RE = re.compile(
    r"(?:v)?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)


class MsiIdentityError(RuntimeError):
    pass


def _normalized_version(value):
    match = _VERSION_RE.fullmatch(value.strip()) if isinstance(value, str) else None
    if not match:
        raise MsiIdentityError(f"invalid stable version: {value!r}")
    return ".".join(str(int(part)) for part in match.groups())


def _read_msi_properties(path):
    """Read the MSI Property table through Windows Installer in read-only mode."""
    if os.name != "nt":
        raise MsiIdentityError("MSI identity verification is only available on Windows")
    path = Path(path).resolve()
    if path.suffix.lower() != ".msi" or not path.is_file():
        raise MsiIdentityError("Not an MSI")

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
            raise MsiIdentityError(
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
        "open the package",
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
    version = _normalized_version(expected_version)
    expected = {
        "ProductName": EXPECTED_PRODUCT_NAME,
        "ProductVersion": version,
        "Manufacturer": EXPECTED_MANUFACTURER,
        "UpgradeCode": EXPECTED_UPGRADE_CODE,
    }
    properties = _read_msi_properties(path)
    for name, value in expected.items():
        if properties.get(name) != value:
            raise MsiIdentityError(
                f"Installer {name} is {properties.get(name)!r}, expected {value!r}"
            )
    return expected
