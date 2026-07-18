"""Persistent per-user Chunes settings."""

import winreg


SETTINGS_KEY = r"Software\Chunes"
AUTO_UPDATE_VALUE = "AutomaticallyCheckForUpdates"
ARTWORK_VALUE = "LookUpOnlineCoverArt"


def get_bool(name, default=True):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SETTINGS_KEY) as key:
            value, value_type = winreg.QueryValueEx(key, name)
        if value_type == winreg.REG_DWORD and value in (0, 1):
            return value == 1
    except OSError:
        pass
    return default


def set_bool(name, enabled):
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, SETTINGS_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(bool(enabled)))


def automatic_updates_enabled():
    return get_bool(AUTO_UPDATE_VALUE)


def set_automatic_updates(enabled):
    set_bool(AUTO_UPDATE_VALUE, enabled)


def artwork_enabled():
    return get_bool(ARTWORK_VALUE)


def set_artwork_enabled(enabled):
    set_bool(ARTWORK_VALUE, enabled)
