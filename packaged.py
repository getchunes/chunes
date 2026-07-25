"""Whether this process is running from the MSIX package Chunes ships to the
Microsoft Store.

The Store build and the MSI build are the same executable, so the differences
between them are decided at runtime rather than at compile time. A packaged
process gets its updates from the Store and registers autostart through the
package manifest, so both of those features behave differently here.
"""

import ctypes
from ctypes import wintypes
import os


ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122
APPMODEL_ERROR_NO_PACKAGE = 15700

# Matches Package/Identity/@Name in installer/msix/AppxManifest.xml.
PACKAGE_IDENTITY_NAME = "dubsector.dev.Chunes"

_family_name = False


def _query_package_family_name():
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        query = kernel32.GetCurrentPackageFamilyName
    except (OSError, AttributeError):
        # Predates the app model entirely, so nothing can be packaged.
        return None
    query.argtypes = [ctypes.POINTER(ctypes.c_uint32), wintypes.LPWSTR]
    query.restype = wintypes.LONG

    length = ctypes.c_uint32(0)
    result = query(ctypes.byref(length), None)
    if result == APPMODEL_ERROR_NO_PACKAGE:
        return None
    if result != ERROR_INSUFFICIENT_BUFFER:
        # A success with a zero-length buffer, or anything else unexpected,
        # is not a package identity we are willing to act on.
        return None
    buffer = ctypes.create_unicode_buffer(length.value)
    result = query(ctypes.byref(length), buffer)
    if result != ERROR_SUCCESS:
        return None
    name = buffer.value
    return name or None


def package_family_name():
    """The package family name, or None when running unpackaged."""
    global _family_name
    if _family_name is False:
        _family_name = _query_package_family_name()
    return _family_name


def is_packaged():
    """True when running from an MSIX package rather than the MSI install."""
    return package_family_name() is not None
