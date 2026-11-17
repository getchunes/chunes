"""GitHub release notifier for Chunes."""

from dataclasses import dataclass
import ctypes
import json
import os
import re
import threading
import time
import urllib.request
import webbrowser

import settings
from version import __version__


RELEASES_URL = "https://api.github.com/repos/getchunes/chunes/releases"
MAX_API_BYTES = 5_000_000
MAX_RELEASE_PAGES = 10
_VERSION_RE = re.compile(
    r"(?:v)?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleaseNotice:
    version: str
    page_url: str


def parse_version(value):
    if not isinstance(value, str):
        raise ValueError("version must be a string")
    match = _VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"invalid stable version: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer_version(candidate, current=__version__):
    return parse_version(candidate) > parse_version(current)


def select_release_notice(releases, current=__version__):
    """Select the newest published numeric release, including prereleases."""
    if not isinstance(releases, list):
        raise ValueError("invalid releases response")
    current_tuple = parse_version(current)
    selected = None
    selected_tuple = current_tuple
    for release in releases:
        prerelease = (
            release.get("prerelease") if isinstance(release, dict) else None
        )
        if (
            not isinstance(release, dict)
            or release.get("draft") is not False
            or (prerelease is not False and prerelease is not True)
        ):
            continue
        tag = release.get("tag_name")
        try:
            version_tuple = parse_version(tag)
        except ValueError:
            continue
        if version_tuple <= selected_tuple:
            continue
        version = ".".join(str(part) for part in version_tuple)
        canonical_tag = f"v{version}" if str(tag).strip().startswith("v") else version
        selected = ReleaseNotice(
            version=version,
            page_url=(
                "https://github.com/getchunes/chunes/releases/tag/"
                f"{canonical_tag}"
            ),
        )
        selected_tuple = version_tuple
    return selected


def fetch_releases():
    releases = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        page_url = f"{RELEASES_URL}?per_page=100&page={page}"
        request = urllib.request.Request(
            page_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
                "User-Agent": f"Chunes/{__version__}",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.geturl() != page_url:
                raise UpdateError("Release check redirected off GitHub")
            body = response.read(MAX_API_BYTES + 1)
        if len(body) > MAX_API_BYTES:
            raise UpdateError("GitHub releases response is too large")
        try:
            page_releases = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise UpdateError("GitHub returned an invalid releases response") from exc
        if not isinstance(page_releases, list):
            raise UpdateError("GitHub returned an invalid releases response")
        releases.extend(page_releases)
        if len(page_releases) < 100:
            return releases
    raise UpdateError("GitHub release history exceeds the supported page limit")


def open_release_page(notice):
    if not webbrowser.open(notice.page_url, new=2):
        raise UpdateError("Windows could not open the Chunes release page")


def _message(text, flags):
    if os.name != "nt":
        print(text)
        return 0
    return ctypes.windll.user32.MessageBoxW(None, text, "Chunes", flags)


class UpdateController:
    def __init__(self, current_version=__version__):
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
        try:
            if delay:
                time.sleep(delay)
            if not manual and not settings.automatic_updates_enabled():
                print("Automatic update check canceled because it was turned off.")
                return
            print("Checking GitHub for Chunes updates...")
            notice = select_release_notice(fetch_releases(), self.current_version)
            if notice is None:
                print("Chunes is up to date.")
                if manual:
                    _message(
                        f"Chunes {self.current_version} is up to date.",
                        0x00000040,
                    )
                return
            print(
                f"Chunes {notice.version} is available; opening "
                f"{notice.page_url}."
            )
            open_release_page(notice)
        except Exception as exc:
            message = f"Update failed: {type(exc).__name__}: {exc}"
            print(message)
            if manual:
                _message(message, 0x00000010)
        finally:
            with self._lock:
                self._checking = False
