"""Autostart for the packaged build.

The MSI build writes the Windows Run key, but a packaged build cannot: its
install path contains the package version, so a Run value baked at install
time stops resolving after the next Store update. Packaged autostart is
declared in the manifest instead and toggled through the StartupTask API,
which also lets Windows Settings and Task Manager override the app.

Every entry point degrades to "unavailable" rather than raising, so a tray
callback can fall back to opening the Startup apps settings page.
"""

import threading
import time


# Matches uap5:StartupTask/@TaskId in installer/msix/AppxManifest.xml.
TASK_ID = "ChunesStartupTask"

SETTINGS_URI = "ms-settings:startupapps"

# Windows.ApplicationModel.StartupTaskState
DISABLED = "disabled"
DISABLED_BY_USER = "disabledByUser"
ENABLED = "enabled"
DISABLED_BY_POLICY = "disabledByPolicy"
ENABLED_BY_POLICY = "enabledByPolicy"

ENABLED_STATES = frozenset({ENABLED, ENABLED_BY_POLICY})
# States the app is not allowed to change; only the user or an administrator
# can move out of them.
LOCKED_STATES = frozenset({DISABLED_BY_USER, DISABLED_BY_POLICY, ENABLED_BY_POLICY})

CALL_TIMEOUT_SECONDS = 10
# pystray rebuilds the whole menu, and re-reads the checkbox, whenever the
# playing track changes. Windows Settings can change the state behind the
# app's back, so the answer is cached briefly rather than indefinitely.
CACHE_SECONDS = 5

_cache_lock = threading.Lock()
_cached = None


class StartupTaskUnavailable(RuntimeError):
    """The StartupTask API could not be reached for this process."""


def _off_tray_thread(call):
    """Run a StartupTask call on a thread that has no apartment yet.

    pystray's tray thread is a single-threaded apartment and the WinRT
    projection refuses to block there. A fresh thread lets the projection
    initialize it as multithreaded instead.
    """
    outcome = {}

    def run():
        try:
            outcome["value"] = call()
        except Exception as error:  # noqa: BLE001 - reported through outcome
            outcome["error"] = error

    worker = threading.Thread(target=run, name="Chunes startup task", daemon=True)
    worker.start()
    worker.join(CALL_TIMEOUT_SECONDS)
    if worker.is_alive():
        raise StartupTaskUnavailable("StartupTask did not answer in time")
    error = outcome.get("error")
    if error is not None:
        if isinstance(error, StartupTaskUnavailable):
            raise error
        raise StartupTaskUnavailable(f"StartupTask call failed: {error}") from error
    return outcome["value"]


def _state_name(state):
    name = getattr(state, "name", None)
    if not isinstance(name, str) or not name:
        return None
    # Projections spell the enumerators DISABLED_BY_USER; the manifest and the
    # rest of this module use the WinRT camelCase spelling.
    head, *rest = name.lower().split("_")
    return head + "".join(part.capitalize() for part in rest)


def _task():
    try:
        from winrt.windows.applicationmodel import StartupTask
    except ImportError as error:
        raise StartupTaskUnavailable("StartupTask projection is missing") from error
    task = StartupTask.get_async(TASK_ID).get()
    if task is None:
        raise StartupTaskUnavailable(f"StartupTask {TASK_ID} was not declared")
    return task


def _remember(name):
    global _cached
    with _cache_lock:
        _cached = (time.monotonic(), name)
    return name


def _recall():
    with _cache_lock:
        if _cached is None:
            return None
        recorded, name = _cached
    if time.monotonic() - recorded > CACHE_SECONDS:
        return None
    return name


def state():
    """The current StartupTaskState name, e.g. ``"enabled"``."""
    name = _off_tray_thread(lambda: _state_name(_task().state))
    if name is None:
        raise StartupTaskUnavailable("StartupTask reported an unreadable state")
    return _remember(name)


def is_enabled():
    """Whether autostart is on, answered from cache when it is fresh."""
    name = _recall()
    if name is None:
        try:
            name = state()
        except StartupTaskUnavailable:
            return False
    return name in ENABLED_STATES


def enable():
    """Ask Windows to enable autostart and report the resulting state.

    Windows silently keeps the task disabled when the user or a policy turned
    it off, so the caller has to check the returned state rather than assume
    the request took effect.
    """

    def request():
        return _state_name(_task().request_enable_async().get())

    name = _off_tray_thread(request)
    if name is None:
        raise StartupTaskUnavailable("StartupTask reported an unreadable state")
    return _remember(name)


def disable():
    def request():
        task = _task()
        current = _state_name(task.state)
        if current in LOCKED_STATES:
            # Disable() throws for policy-managed tasks and would clear a
            # user's own choice for the rest.
            return current
        task.disable()
        return _state_name(task.state) or DISABLED

    return _remember(_off_tray_thread(request))
