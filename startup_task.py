"""Autostart for the packaged build.

The MSI build writes the Windows Run key, but a packaged build cannot: its
install path contains the package version, so a Run value baked at install
time stops resolving after the next Store update. Packaged autostart is
declared in the manifest instead and toggled through the StartupTask API,
which also lets Windows Settings and Task Manager override the app.

Every entry point degrades to "unavailable" rather than raising, so a tray
callback can fall back to opening the Startup apps settings page.
"""

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


class StartupTaskUnavailable(RuntimeError):
    """The StartupTask API could not be reached for this process."""


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
    try:
        task = StartupTask.get_async(TASK_ID).get()
    except OSError as error:
        raise StartupTaskUnavailable(f"StartupTask {TASK_ID} is unavailable") from error
    if task is None:
        raise StartupTaskUnavailable(f"StartupTask {TASK_ID} was not declared")
    return task


def state():
    """The current StartupTaskState name, e.g. ``"enabled"``."""
    name = _state_name(_task().state)
    if name is None:
        raise StartupTaskUnavailable("StartupTask reported an unreadable state")
    return name


def is_enabled():
    try:
        return state() in ENABLED_STATES
    except StartupTaskUnavailable:
        return False


def enable():
    """Ask Windows to enable autostart and report the resulting state.

    Windows silently keeps the task disabled when the user or a policy turned
    it off, so the caller has to check the returned state rather than assume
    the request took effect.
    """
    task = _task()
    result = _state_name(task.request_enable_async().get())
    if result is None:
        raise StartupTaskUnavailable("StartupTask reported an unreadable state")
    return result


def disable():
    task = _task()
    current = _state_name(task.state)
    if current in LOCKED_STATES:
        # Disable() throws for policy-managed tasks and would clear a user's
        # own choice for the rest.
        return current
    task.disable()
    return _state_name(task.state) or DISABLED
