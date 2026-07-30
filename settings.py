"""Persistent per-user Chunes settings."""

import winreg


SETTINGS_KEY = r"Software\Chunes"
AUTO_UPDATE_VALUE = "AutomaticallyCheckForUpdates"
ARTWORK_VALUE = "LookUpOnlineCoverArt"
TRACK_BUTTON_VALUE = "ShowTrackButton"
GET_CHUNES_BUTTON_VALUE = "ShowGetChunesButton"
TRAY_INTRO_VALUE = "TrayIntroShown"


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


def track_button_enabled():
    return get_bool(TRACK_BUTTON_VALUE)


def set_track_button_enabled(enabled):
    set_bool(TRACK_BUTTON_VALUE, enabled)


# Off by default: this one advertises Chunes to whoever opens the profile
# rather than describing what is playing, so it stays the user's own choice.
def get_chunes_button_enabled():
    return get_bool(GET_CHUNES_BUTTON_VALUE, default=False)


def set_get_chunes_button_enabled(enabled):
    set_bool(GET_CHUNES_BUTTON_VALUE, enabled)


# The app has no window, so the first launch shows a one-time notification
# pointing at the tray icon.
def tray_intro_shown():
    return get_bool(TRAY_INTRO_VALUE, default=False)


def set_tray_intro_shown():
    set_bool(TRAY_INTRO_VALUE, True)
