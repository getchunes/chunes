"""Chunes tray app: runs the presence engine in the background with a
system-tray icon for status, autostart, and quitting."""

import asyncio
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import threading
import time
import winreg
from pathlib import Path

from PIL import Image
import pystray

import presence
import settings
from updater import UpdateController

APP_NAME = "Chunes"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "chunes.log"


def _open_log():
    # Windowed mode has no console; keep a small rolling log instead.
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
            LOG_PATH.replace(LOG_PATH.with_suffix(".log.old"))
    except OSError:
        # A short-lived installer helper may share the active process log.
        pass
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f


_updater = None
_tray_stop = threading.Event()
_instance_mutex = None


def acquire_single_instance():
    global _instance_mutex
    if os.name != "nt":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = create_mutex(None, False, r"Local\Chunes.SingleInstance")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        close_handle(handle)
        return False
    _instance_mutex = handle
    return True


def resource_path(*parts):
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def _launch_argv():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).resolve()
    return [str(pythonw), str(script)]


def _launch_command():
    return subprocess.list2cmdline(_launch_argv())


def _command_parts(command):
    if not isinstance(command, str) or not command.strip():
        return []
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    parse = shell32.CommandLineToArgvW
    parse.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    parse.restype = ctypes.POINTER(wintypes.LPWSTR)
    argc = ctypes.c_int()
    argv = parse(command, ctypes.byref(argc))
    if not argv:
        return []
    try:
        return [argv[index] for index in range(argc.value)]
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        local_free = kernel32.LocalFree
        local_free.argtypes = [wintypes.HLOCAL]
        local_free.restype = wintypes.HLOCAL
        local_free(ctypes.cast(argv, wintypes.HLOCAL))


def _normalized_path(value):
    return os.path.normcase(
        os.path.realpath(os.path.abspath(os.path.expandvars(value)))
    )


def _command_is_current(command):
    actual = _command_parts(command)
    expected = _launch_argv()
    return len(actual) == len(expected) and all(
        _normalized_path(left) == _normalized_path(right)
        for left, right in zip(actual, expected)
    )


def _command_is_chunes_owned(command):
    parts = _command_parts(command)
    if len(parts) == 1:
        return Path(parts[0]).name.lower() == "chunes.exe"
    return (
        len(parts) == 2
        and Path(parts[0]).name.lower() in {"python.exe", "pythonw.exe"}
        and Path(parts[1]).name.lower() == "chunes.py"
    )


def _read_autostart_command():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, value_type = winreg.QueryValueEx(key, APP_NAME)
    except OSError:
        return None
    if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
        return value
    # Preserve a same-named value with an unrelated registry type.
    return ""


def _set_autostart_command():
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())


def _delete_autostart_command():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
    except OSError:
        pass


def autostart_enabled():
    return _command_is_current(_read_autostart_command())


def migrate_legacy_autostart():
    command = _read_autostart_command()
    if (
        getattr(sys, "frozen", False)
        and command is not None
        and not _command_is_current(command)
        and _command_is_chunes_owned(command)
    ):
        _set_autostart_command()
        print("Migrated the Chunes autostart command to the installed executable.")
        return True
    return False


def remove_owned_autostart():
    command = _read_autostart_command()
    if _command_is_current(command) or _command_is_chunes_owned(command):
        _delete_autostart_command()
        return True
    return False


def toggle_autostart(icon, item):
    command = _read_autostart_command()
    if _command_is_current(command):
        _delete_autostart_command()
    elif command is None or _command_is_chunes_owned(command):
        _set_autostart_command()
    else:
        print("Not replacing an unrelated Windows Run value named Chunes.")
    icon.update_menu()


def make_icon_image():
    with Image.open(resource_path("assets", "chunes-tray-64.png")) as image:
        return image.convert("RGBA").copy()


def current_track_text(item=None):
    track = presence.status_snapshot()["track"]
    return track if track else "Nothing playing"


def extension_state_text(item=None):
    enabled = presence.status_snapshot()["extension_enabled"]
    if enabled is None:
        return "Chune ID: not connected"
    return f"Chune ID: {'on' if enabled else 'off'}"


def open_log(icon, item):
    os.startfile(LOG_PATH)


def toggle_automatic_updates(icon, item):
    settings.set_automatic_updates(not settings.automatic_updates_enabled())
    icon.update_menu()


def check_for_updates(icon, item):
    if _updater is not None:
        _updater.check_now()


def toggle_artwork(icon, item):
    settings.set_artwork_enabled(not settings.artwork_enabled())
    icon.update_menu()


def quit_app(icon, item):
    _tray_stop.set()
    icon.stop()


def refresh_dynamic_menu(icon, stop_event, interval=0.5):
    previous = presence.status_snapshot()
    while not stop_event.wait(interval):
        current = presence.status_snapshot()
        if current != previous:
            previous = current
            try:
                icon.update_menu()
            except (OSError, RuntimeError):
                return


def _register_close_messages(icon):
    if not hasattr(icon, "_message_handlers"):
        return
    
    def on_close(hwnd, msg, wparam, lparam):
        _tray_stop.set()
        icon.stop()
        ctypes.windll.user32.DestroyWindow(hwnd)
        return 0

    def on_queryendsession(hwnd, msg, wparam, lparam):
        return 1

    def on_endsession(hwnd, msg, wparam, lparam):
        if wparam:
            _tray_stop.set()
            icon.stop()
            ctypes.windll.user32.DestroyWindow(hwnd)
        return 0

    icon._message_handlers[0x0010] = on_close
    icon._message_handlers[0x0011] = on_queryendsession
    icon._message_handlers[0x0016] = on_endsession


def setup_tray(icon):
    icon.visible = True
    _register_close_messages(icon)
    threading.Thread(
        target=refresh_dynamic_menu,
        args=(icon, _tray_stop),
        daemon=True,
        name="Chunes tray status",
    ).start()


def run_engine():
    while True:
        try:
            asyncio.run(presence.main())
        except (Exception, SystemExit) as e:
            print(f"Engine stopped ({type(e).__name__}: {e}), restarting in 30s")
        finally:
            presence.set_status(track=None, extension_enabled=None)
        time.sleep(30)


def _handle_installer_command():
    if sys.argv[1:] == ["--migrate-autostart"]:
        migrate_legacy_autostart()
        return True
    if sys.argv[1:] == ["--remove-autostart-if-owned"]:
        remove_owned_autostart()
        return True
    return False


def main():
    global _updater
    _open_log()
    if _handle_installer_command():
        return
    if not acquire_single_instance():
        print("Chunes is already running; the second instance is exiting.")
        return
    migrate_legacy_autostart()
    _tray_stop.clear()
    threading.Thread(target=run_engine, daemon=True).start()
    icon = pystray.Icon(
        APP_NAME,
        make_icon_image(),
        APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem(current_track_text, None, enabled=False),
            pystray.MenuItem(extension_state_text, None, enabled=False),
            pystray.MenuItem("Start with Windows", toggle_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Automatically check for updates",
                toggle_automatic_updates,
                checked=lambda item: settings.automatic_updates_enabled(),
            ),
            pystray.MenuItem("Check for updates now", check_for_updates),
            pystray.MenuItem(
                "Look up online album art",
                toggle_artwork,
                checked=lambda item: settings.artwork_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open log", open_log),
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    _updater = UpdateController(on_install=lambda: quit_app(icon, None))
    if settings.automatic_updates_enabled():
        _updater.start_automatic_check()
    try:
        icon.run(setup=setup_tray)
    finally:
        _tray_stop.set()


if __name__ == "__main__":
    main()
