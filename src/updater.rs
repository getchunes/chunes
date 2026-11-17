//! The GitHub release notifier.
//!
//! A port of `updater.py`: fetch the release list, pick the newest published
//! numeric release (prereleases included), and open its page in the default
//! browser. Nothing is downloaded or installed. Only the message box and the
//! browser launch touch the OS; every other step is platform independent so
//! it is tested on any host.

use std::fmt;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde_json::Value;

use crate::http;
use crate::log;

pub const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");
pub const RELEASES_URL: &str = "https://api.github.com/repos/getchunes/chunes/releases";
pub const RELEASE_PAGE_URL: &str = "https://github.com/getchunes/chunes/releases/tag/";
pub const MAX_API_BYTES: u64 = 5_000_000;
pub const MAX_RELEASE_PAGES: u32 = 10;
const RELEASES_PER_PAGE: usize = 100;
const API_TIMEOUT: Duration = Duration::from_secs(15);
/// How long after startup the automatic check runs.
pub const AUTOMATIC_CHECK_DELAY: Duration = Duration::from_secs(8);

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateError(pub String);

impl fmt::Display for UpdateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for UpdateError {}

/// A version string that is not a stable `x.y.z` triplet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VersionError(pub String);

impl fmt::Display for VersionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid stable version: '{}'", self.0)
    }
}

impl std::error::Error for VersionError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReleaseNotice {
    pub version: String,
    pub page_url: String,
}

/// A decimal component without leading zeros: `0` or `[1-9][0-9]*`.
fn component(part: &str) -> Option<u64> {
    let bytes = part.as_bytes();
    let canonical = match bytes {
        [] => false,
        [b'0'] => true,
        [first, ..] => *first != b'0' && bytes.iter().all(u8::is_ascii_digit),
    };
    if canonical {
        part.parse().ok()
    } else {
        None
    }
}

/// Parse a stable `x.y.z` version with an optional leading `v`.
pub fn parse_version(value: &str) -> Result<(u64, u64, u64), VersionError> {
    let invalid = || VersionError(value.to_owned());
    let trimmed = value.trim();
    let bare = trimmed.strip_prefix('v').unwrap_or(trimmed);
    let mut parts = bare.split('.');
    let mut next = || parts.next().and_then(component).ok_or_else(invalid);
    let version = (next()?, next()?, next()?);
    if parts.next().is_some() {
        return Err(invalid());
    }
    Ok(version)
}

pub fn is_newer_version(candidate: &str, current: &str) -> Result<bool, VersionError> {
    Ok(parse_version(candidate)? > parse_version(current)?)
}

/// Select the newest published numeric release, including prereleases.
pub fn select_release_notice(
    releases: &[Value],
    current: &str,
) -> Result<Option<ReleaseNotice>, VersionError> {
    let mut selected = None;
    let mut selected_version = parse_version(current)?;
    for release in releases {
        let Some(release) = release.as_object() else {
            continue;
        };
        if release.get("draft") != Some(&Value::Bool(false))
            || !matches!(release.get("prerelease"), Some(Value::Bool(_)))
        {
            continue;
        }
        let Some(tag) = release.get("tag_name").and_then(Value::as_str) else {
            continue;
        };
        let Ok(version) = parse_version(tag) else {
            continue;
        };
        if version <= selected_version {
            continue;
        }
        let (major, minor, patch) = version;
        let number = format!("{major}.{minor}.{patch}");
        let canonical_tag = if tag.trim().starts_with('v') {
            format!("v{number}")
        } else {
            number.clone()
        };
        selected = Some(ReleaseNotice {
            version: number,
            page_url: format!("{RELEASE_PAGE_URL}{canonical_tag}"),
        });
        selected_version = version;
    }
    Ok(selected)
}

/// Performs one HTTPS GET; `http::get` outside tests.
pub type HttpGet<'a> = &'a dyn Fn(&http::Request) -> Result<String, String>;

/// The whole release history, a page of 100 at a time.
pub fn fetch_releases(get: HttpGet) -> Result<Vec<Value>, UpdateError> {
    let user_agent = format!("Chunes/{CURRENT_VERSION}");
    let headers = [
        ("Accept", "application/vnd.github+json"),
        ("X-GitHub-Api-Version", "2026-03-10"),
        ("User-Agent", user_agent.as_str()),
    ];
    let mut releases = Vec::new();
    for page in 1..=MAX_RELEASE_PAGES {
        let page_url = format!("{RELEASES_URL}?per_page={RELEASES_PER_PAGE}&page={page}");
        let body = get(&http::Request {
            url: &page_url,
            headers: &headers,
            timeout: API_TIMEOUT,
            max_bytes: MAX_API_BYTES,
            no_redirects: true,
        })
        .map_err(UpdateError)?;
        if body.len() as u64 > MAX_API_BYTES {
            return Err(UpdateError("GitHub releases response is too large".into()));
        }
        let invalid = || UpdateError("GitHub returned an invalid releases response".into());
        let Value::Array(page_releases) = serde_json::from_str(&body).map_err(|_| invalid())?
        else {
            return Err(invalid());
        };
        let full = page_releases.len() >= RELEASES_PER_PAGE;
        releases.extend(page_releases);
        if !full {
            return Ok(releases);
        }
    }
    Err(UpdateError(
        "GitHub release history exceeds the supported page limit".into(),
    ))
}

/// Open the release page with `open_url`, which reports whether it worked.
pub fn open_release_page(
    notice: &ReleaseNotice,
    open_url: &dyn Fn(&str) -> bool,
) -> Result<(), UpdateError> {
    if open_url(&notice.page_url) {
        Ok(())
    } else {
        Err(UpdateError(
            "Windows could not open the Chunes release page".into(),
        ))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    Information,
    Error,
}

/// Open `url` in the default browser.
#[cfg(windows)]
pub fn open_in_browser(url: &str) -> bool {
    use windows::core::{w, HSTRING, PCWSTR};
    use windows::Win32::UI::Shell::ShellExecuteW;
    use windows::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    let url = HSTRING::from(url);
    // SAFETY: every string argument outlives the call.
    let result = unsafe {
        ShellExecuteW(
            None,
            w!("open"),
            &url,
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        )
    };
    // ShellExecuteW reports success with a value above 32.
    result.0 as isize > 32
}

#[cfg(not(windows))]
pub fn open_in_browser(url: &str) -> bool {
    log!("Not opening {url}: there is no default browser to hand it to off Windows.");
    false
}

/// Show a "Chunes" message box.
#[cfg(windows)]
pub fn show_message(text: &str, icon: Icon) {
    use windows::core::{w, HSTRING};
    use windows::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_ICONINFORMATION};

    let style = match icon {
        Icon::Information => MB_ICONINFORMATION,
        Icon::Error => MB_ICONERROR,
    };
    // SAFETY: the text outlives the call.
    unsafe {
        MessageBoxW(None, &HSTRING::from(text), w!("Chunes"), style);
    }
}

#[cfg(not(windows))]
pub fn show_message(text: &str, _icon: Icon) {
    log!("{text}");
}

pub type EnabledHook = Box<dyn Fn() -> bool + Send + Sync>;
pub type FetchHook = Box<dyn Fn() -> Result<Vec<Value>, UpdateError> + Send + Sync>;
/// Opens a URL and reports whether it worked.
pub type OpenUrlHook = Box<dyn Fn(&str) -> bool + Send + Sync>;
pub type MessageHook = Box<dyn Fn(&str, Icon) + Send + Sync>;
pub type SleepHook = Box<dyn Fn(Duration) + Send + Sync>;

/// The controller's outside effects, injectable so tests stay offline.
pub struct Hooks {
    pub automatic_updates_enabled: EnabledHook,
    pub fetch_releases: FetchHook,
    pub open_url: OpenUrlHook,
    pub message: MessageHook,
    pub sleep: SleepHook,
}

impl Hooks {
    /// The real network, browser and message box.
    pub fn system(automatic_updates_enabled: fn() -> bool) -> Self {
        Self {
            automatic_updates_enabled: Box::new(automatic_updates_enabled),
            fetch_releases: Box::new(|| fetch_releases(&http::get)),
            open_url: Box::new(open_in_browser),
            message: Box::new(show_message),
            sleep: Box::new(thread::sleep),
        }
    }
}

/// How one check ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// An automatic check found automatic updates turned off.
    Canceled,
    UpToDate,
    Opened(ReleaseNotice),
    /// The logged `Update failed: ...` line.
    Failed(String),
}

struct Shared {
    current_version: String,
    checking: Mutex<bool>,
    hooks: Hooks,
}

/// Runs release checks on a background thread, one at a time.
#[derive(Clone)]
pub struct UpdateController {
    shared: Arc<Shared>,
}

impl UpdateController {
    /// A controller for this build with the real network, browser and
    /// message box. `automatic_updates_enabled` is asked again just before
    /// an automatic check goes out.
    pub fn new(automatic_updates_enabled: fn() -> bool) -> Self {
        Self::with_hooks(CURRENT_VERSION, Hooks::system(automatic_updates_enabled))
    }

    pub fn with_hooks(current_version: &str, hooks: Hooks) -> Self {
        Self {
            shared: Arc::new(Shared {
                current_version: current_version.to_owned(),
                checking: Mutex::new(false),
                hooks,
            }),
        }
    }

    pub fn current_version(&self) -> &str {
        &self.shared.current_version
    }

    /// Check silently after `AUTOMATIC_CHECK_DELAY`.
    pub fn start_automatic_check(&self) {
        self.start(false, AUTOMATIC_CHECK_DELAY);
    }

    /// Check now and report the result, from the tray's "Check for updates".
    pub fn check_now(&self) {
        if !self.start(true, Duration::ZERO) {
            (self.shared.hooks.message)(
                "An update check is already in progress.",
                Icon::Information,
            );
        }
    }

    fn start(&self, manual: bool, delay: Duration) -> bool {
        {
            let mut checking = self.lock_checking();
            if *checking {
                return false;
            }
            *checking = true;
        }
        let controller = self.clone();
        let spawned = thread::Builder::new()
            .name("Chunes updater".into())
            .spawn(move || {
                controller.run(manual, delay);
            });
        if let Err(error) = spawned {
            log!("Update failed: could not start the update thread: {error}");
            *self.lock_checking() = false;
        }
        true
    }

    fn lock_checking(&self) -> std::sync::MutexGuard<'_, bool> {
        self.shared
            .checking
            .lock()
            .unwrap_or_else(|e| e.into_inner())
    }

    fn run(&self, manual: bool, delay: Duration) -> Outcome {
        let outcome = match self.check(manual, delay) {
            Ok(outcome) => outcome,
            Err(failure) => {
                let message = format!("Update failed: {failure}");
                log!("{message}");
                if manual {
                    (self.shared.hooks.message)(&message, Icon::Error);
                }
                Outcome::Failed(message)
            }
        };
        *self.lock_checking() = false;
        outcome
    }

    /// One check; a failure is `<error type>: <message>` as Python named it.
    fn check(&self, manual: bool, delay: Duration) -> Result<Outcome, String> {
        let hooks = &self.shared.hooks;
        if !delay.is_zero() {
            (hooks.sleep)(delay);
        }
        if !manual && !(hooks.automatic_updates_enabled)() {
            log!("Automatic update check canceled because it was turned off.");
            return Ok(Outcome::Canceled);
        }
        log!("Checking GitHub for Chunes updates...");
        let releases = (hooks.fetch_releases)().map_err(|e| format!("UpdateError: {e}"))?;
        let notice = select_release_notice(&releases, &self.shared.current_version)
            .map_err(|e| format!("ValueError: {e}"))?;
        let Some(notice) = notice else {
            log!("Chunes is up to date.");
            if manual {
                (hooks.message)(
                    &format!("Chunes {} is up to date.", self.shared.current_version),
                    Icon::Information,
                );
            }
            return Ok(Outcome::UpToDate);
        };
        log!(
            "Chunes {} is available; opening {}.",
            notice.version,
            notice.page_url
        );
        open_release_page(&notice, &*hooks.open_url).map_err(|e| format!("UpdateError: {e}"))?;
        Ok(Outcome::Opened(notice))
    }
}

#[cfg(test)]
mod tests;
