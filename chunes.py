"""Chunes tray app: runs the presence engine in the background with a
system-tray icon for status, autostart, and quitting."""

import asyncio
import os
import sys
import threading
import time
import winreg
from pathlib import Path

from PIL import Image, ImageDraw
import pystray

APP_NAME = "Chunes"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

LOG_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / APP_NAME
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "chunes.log"


def _open_log():
    # Windowed mode has no console; keep a small rolling log instead.
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1_000_000:
        LOG_PATH.replace(LOG_PATH.with_suffix(".log.old"))
    f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f


_open_log()

import presence  # noqa: E402  (after stdout redirect so its prints land in the log)


def _launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).resolve()
    return f'"{pythonw}" "{script}"'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
        return True
    except OSError:
        return False


def toggle_autostart(icon, item):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as k:
        if autostart_enabled():
            winreg.DeleteValue(k, APP_NAME)
        else:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ,
                              _launch_command())


def make_icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Eighth note: two filled heads, stems, and a beam.
    d.ellipse((10, 42, 26, 54), fill=(88, 101, 242, 255))
    d.ellipse((38, 38, 54, 50), fill=(88, 101, 242, 255))
    d.rectangle((22, 14, 26, 48), fill=(88, 101, 242, 255))
    d.rectangle((50, 10, 54, 44), fill=(88, 101, 242, 255))
    d.polygon((22, 14, 54, 10, 54, 20, 22, 24), fill=(88, 101, 242, 255))
    return img


def current_track_text(item=None):
    track = presence.status.get("track")
    return track if track else "Nothing playing"


def open_log(icon, item):
    os.startfile(LOG_PATH)


def quit_app(icon, item):
    icon.stop()
    os._exit(0)


def run_engine():
    while True:
        try:
            asyncio.run(presence.main())
        except (Exception, SystemExit) as e:
            print(f"Engine stopped ({type(e).__name__}: {e}), restarting in 30s")
        time.sleep(30)


def main():
    threading.Thread(target=run_engine, daemon=True).start()
    icon = pystray.Icon(
        APP_NAME,
        make_icon_image(),
        APP_NAME,
        menu=pystray.Menu(
            pystray.MenuItem(current_track_text, None, enabled=False),
            pystray.MenuItem("Start with Windows", toggle_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.MenuItem("Open log", open_log),
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()
