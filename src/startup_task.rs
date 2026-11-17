//! Autostart for the packaged build.
//!
//! A port of `startup_task.py`. The MSI build writes the Windows Run key, but
//! a packaged build cannot: its install path contains the package version, so
//! a Run value baked at install time stops resolving after the next Store
//! update. Packaged autostart is declared in the manifest instead and toggled
//! through the StartupTask API, which also lets Windows Settings and Task
//! Manager override the app.
//!
//! Every entry point degrades to "unavailable" rather than panicking, so a
//! tray action can fall back to opening the Startup apps settings page.

use std::fmt;
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

/// Matches uap5:StartupTask/@TaskId in installer/msix/AppxManifest.xml.
pub const TASK_ID: &str = "ChunesStartupTask";
pub const SETTINGS_URI: &str = "ms-settings:startupapps";
pub const CALL_TIMEOUT: Duration = Duration::from_secs(10);
/// The tray re-reads the checkbox every time the menu opens. Windows Settings
/// can change the state behind the app's back, so the answer is cached
/// briefly rather than indefinitely.
pub const CACHE_FOR: Duration = Duration::from_secs(5);

/// `Windows.ApplicationModel.StartupTaskState`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Disabled,
    DisabledByUser,
    Enabled,
    DisabledByPolicy,
    EnabledByPolicy,
}

impl State {
    /// The WinRT enumerator value, or `None` for one this build predates.
    pub fn from_raw(value: i32) -> Option<Self> {
        Some(match value {
            0 => State::Disabled,
            1 => State::DisabledByUser,
            2 => State::Enabled,
            3 => State::DisabledByPolicy,
            4 => State::EnabledByPolicy,
            _ => return None,
        })
    }

    /// The WinRT camelCase spelling the manifest and the log use.
    pub fn name(self) -> &'static str {
        match self {
            State::Disabled => "disabled",
            State::DisabledByUser => "disabledByUser",
            State::Enabled => "enabled",
            State::DisabledByPolicy => "disabledByPolicy",
            State::EnabledByPolicy => "enabledByPolicy",
        }
    }

    pub fn is_enabled(self) -> bool {
        matches!(self, State::Enabled | State::EnabledByPolicy)
    }

    /// States the app is not allowed to change; only the user or an
    /// administrator can move out of them.
    pub fn is_locked(self) -> bool {
        matches!(
            self,
            State::DisabledByUser | State::DisabledByPolicy | State::EnabledByPolicy
        )
    }
}

impl fmt::Display for State {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

/// The StartupTask API could not be reached for this process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unavailable(pub String);

impl fmt::Display for Unavailable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// One declared startup task, as the projection exposes it.
pub trait Task {
    fn state(&self) -> Result<State, Unavailable>;
    fn request_enable(&self) -> Result<State, Unavailable>;
    fn disable(&self) -> Result<(), Unavailable>;
}

type Open = dyn Fn() -> Result<Box<dyn Task>, Unavailable> + Send + Sync;

/// The packaged autostart switch, with its short-lived cache.
pub struct StartupTask {
    open: Arc<Open>,
    timeout: Duration,
    cached: Mutex<Option<(Instant, State)>>,
}

impl StartupTask {
    pub fn new(
        open: impl Fn() -> Result<Box<dyn Task>, Unavailable> + Send + Sync + 'static,
        timeout: Duration,
    ) -> Self {
        Self {
            open: Arc::new(open),
            timeout,
            cached: Mutex::new(None),
        }
    }

    /// The real task, through the WinRT projection.
    #[cfg(windows)]
    pub fn windows() -> Self {
        Self::new(winrt::open, CALL_TIMEOUT)
    }

    /// Run a StartupTask call on a fresh thread.
    ///
    /// The tray thread is a single-threaded apartment, and blocking on a
    /// WinRT async call there can deadlock. A fresh thread joins the
    /// multithreaded apartment instead, and a hung call gives up after the
    /// timeout rather than freezing the tray.
    fn off_tray_thread<T: Send + 'static>(
        &self,
        call: impl FnOnce(&dyn Task) -> Result<T, Unavailable> + Send + 'static,
    ) -> Result<T, Unavailable> {
        let open = self.open.clone();
        let (send, receive) = mpsc::channel();
        std::thread::Builder::new()
            .name("Chunes startup task".into())
            .spawn(move || {
                let outcome = open().and_then(|task| call(task.as_ref()));
                let _ = send.send(outcome);
            })
            .map_err(|e| Unavailable(format!("StartupTask call failed: {e}")))?;
        match receive.recv_timeout(self.timeout) {
            Ok(outcome) => outcome,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                Err(Unavailable("StartupTask did not answer in time".into()))
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err(Unavailable("StartupTask call failed".into()))
            }
        }
    }

    fn remember(&self, state: State) -> State {
        *self.cached.lock().unwrap_or_else(|e| e.into_inner()) = Some((Instant::now(), state));
        state
    }

    fn recall(&self) -> Option<State> {
        let cached = *self.cached.lock().unwrap_or_else(|e| e.into_inner());
        cached
            .filter(|(at, _)| at.elapsed() <= CACHE_FOR)
            .map(|(_, state)| state)
    }

    /// Forget the cached answer, as if it had expired.
    pub fn expire(&self) {
        *self.cached.lock().unwrap_or_else(|e| e.into_inner()) = None;
    }

    /// The current state, read fresh.
    pub fn state(&self) -> Result<State, Unavailable> {
        let state = self.off_tray_thread(|task| task.state())?;
        Ok(self.remember(state))
    }

    /// Whether autostart is on, answered from cache when it is fresh. An
    /// unreachable task reads as off.
    pub fn is_enabled(&self) -> bool {
        self.recall()
            .map(Ok)
            .unwrap_or_else(|| self.state())
            .is_ok_and(State::is_enabled)
    }

    /// Ask Windows to enable autostart and report the resulting state.
    ///
    /// Windows silently keeps the task disabled when the user or a policy
    /// turned it off, so the caller has to check the returned state rather
    /// than assume the request took effect.
    pub fn enable(&self) -> Result<State, Unavailable> {
        let state = self.off_tray_thread(|task| task.request_enable())?;
        Ok(self.remember(state))
    }

    pub fn disable(&self) -> Result<State, Unavailable> {
        let state = self.off_tray_thread(|task| {
            let current = task.state()?;
            if current.is_locked() {
                // Disable() throws for policy-managed tasks and would clear a
                // user's own choice for the rest.
                return Ok(current);
            }
            task.disable()?;
            Ok(task.state().unwrap_or(State::Disabled))
        })?;
        Ok(self.remember(state))
    }
}

#[cfg(windows)]
mod winrt {
    use super::{State, Task, Unavailable, TASK_ID};
    use windows::core::HSTRING;
    use windows::ApplicationModel::StartupTask as WinRtTask;

    fn failed(error: windows::core::Error) -> Unavailable {
        Unavailable(format!("StartupTask call failed: {error}"))
    }

    fn state(raw: windows::ApplicationModel::StartupTaskState) -> Result<State, Unavailable> {
        State::from_raw(raw.0)
            .ok_or_else(|| Unavailable("StartupTask reported an unreadable state".into()))
    }

    struct Declared(WinRtTask);

    impl Task for Declared {
        fn state(&self) -> Result<State, Unavailable> {
            state(self.0.State().map_err(failed)?)
        }

        fn request_enable(&self) -> Result<State, Unavailable> {
            state(
                self.0
                    .RequestEnableAsync()
                    .and_then(|operation| operation.get())
                    .map_err(failed)?,
            )
        }

        fn disable(&self) -> Result<(), Unavailable> {
            self.0.Disable().map_err(failed)
        }
    }

    pub fn open() -> Result<Box<dyn Task>, Unavailable> {
        let task = WinRtTask::GetAsync(&HSTRING::from(TASK_ID))
            .and_then(|operation| operation.get())
            .map_err(|_| Unavailable(format!("StartupTask {TASK_ID} was not declared")))?;
        Ok(Box::new(Declared(task)))
    }
}

#[cfg(test)]
mod tests {
    //! Ported from `StartupTaskTests` in `tests/test_packaged.py`.
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::thread::ThreadId;

    #[derive(Clone, Default)]
    struct Fake {
        state: Arc<Mutex<Option<State>>>,
        enable_result: Option<State>,
        disabled: Arc<AtomicUsize>,
        threads: Arc<Mutex<Vec<ThreadId>>>,
    }

    impl Task for Fake {
        fn state(&self) -> Result<State, Unavailable> {
            self.threads
                .lock()
                .unwrap()
                .push(std::thread::current().id());
            self.state
                .lock()
                .unwrap()
                .ok_or_else(|| Unavailable("unreadable".into()))
        }

        fn request_enable(&self) -> Result<State, Unavailable> {
            Ok(self.enable_result.unwrap())
        }

        fn disable(&self) -> Result<(), Unavailable> {
            self.disabled.fetch_add(1, Ordering::SeqCst);
            *self.state.lock().unwrap() = Some(State::Disabled);
            Ok(())
        }
    }

    fn with(fake: &Fake) -> (StartupTask, Arc<AtomicUsize>) {
        let opened = Arc::new(AtomicUsize::new(0));
        let count = opened.clone();
        let fake = fake.clone();
        let task = StartupTask::new(
            move || {
                count.fetch_add(1, Ordering::SeqCst);
                Ok(Box::new(fake.clone()) as Box<dyn Task>)
            },
            CALL_TIMEOUT,
        );
        (task, opened)
    }

    fn in_state(state: State) -> Fake {
        Fake {
            state: Arc::new(Mutex::new(Some(state))),
            ..Fake::default()
        }
    }

    #[test]
    fn test_projection_enumerators_map_to_winrt_state_names() {
        let cases = [
            (0, "disabled"),
            (1, "disabledByUser"),
            (2, "enabled"),
            (3, "disabledByPolicy"),
            (4, "enabledByPolicy"),
        ];
        for (raw, name) in cases {
            assert_eq!(State::from_raw(raw).unwrap().name(), name);
        }
        assert_eq!(State::from_raw(5), None);
        assert_eq!(State::from_raw(-1), None);
    }

    #[test]
    fn test_an_unreachable_task_reads_as_disabled_rather_than_raising() {
        let task = StartupTask::new(|| Err(Unavailable("no package".into())), CALL_TIMEOUT);
        assert!(!task.is_enabled());
        assert!(task.state().is_err());
    }

    #[test]
    fn test_projection_failures_never_escape_as_their_own_type() {
        // An unreadable state is reported as unavailable, never a panic.
        let fake = Fake::default();
        let (task, _) = with(&fake);
        assert!(task.state().is_err());
        assert!(!task.is_enabled());
    }

    #[test]
    fn test_winrt_calls_never_run_on_the_calling_thread() {
        let fake = in_state(State::Enabled);
        let (task, _) = with(&fake);
        assert_eq!(task.state(), Ok(State::Enabled));
        let threads = fake.threads.lock().unwrap();
        assert_eq!(threads.len(), 1);
        assert_ne!(threads[0], std::thread::current().id());
    }

    #[test]
    fn test_a_hung_projection_call_gives_up_instead_of_freezing_the_tray() {
        let task = StartupTask::new(
            || {
                std::thread::sleep(Duration::from_secs(2));
                Err(Unavailable("late".into()))
            },
            Duration::from_millis(100),
        );
        let started = Instant::now();
        assert_eq!(
            task.state(),
            Err(Unavailable("StartupTask did not answer in time".into()))
        );
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn test_the_menu_checkbox_reuses_a_fresh_answer() {
        let fake = in_state(State::Enabled);
        let (task, opened) = with(&fake);
        assert!(task.is_enabled());
        assert!(task.is_enabled());
        assert_eq!(opened.load(Ordering::SeqCst), 1);
        task.expire();
        assert!(task.is_enabled());
        assert_eq!(opened.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn test_enable_reports_the_state_windows_actually_settled_on() {
        let fake = Fake {
            enable_result: Some(State::DisabledByUser),
            ..Fake::default()
        };
        let (task, _) = with(&fake);
        assert_eq!(task.enable(), Ok(State::DisabledByUser));
        assert!(!State::DisabledByUser.is_enabled());
    }

    #[test]
    fn test_disable_leaves_user_and_policy_decisions_alone() {
        for locked in [
            State::DisabledByUser,
            State::DisabledByPolicy,
            State::EnabledByPolicy,
        ] {
            assert!(locked.is_locked());
            let fake = in_state(locked);
            let (task, _) = with(&fake);
            assert_eq!(task.disable(), Ok(locked));
            assert_eq!(fake.disabled.load(Ordering::SeqCst), 0);
        }
        assert!(!State::Enabled.is_locked());
        assert!(!State::Disabled.is_locked());
    }

    #[test]
    fn test_disable_turns_off_a_task_the_app_owns() {
        let fake = in_state(State::Enabled);
        let (task, _) = with(&fake);
        assert_eq!(task.disable(), Ok(State::Disabled));
        assert_eq!(fake.disabled.load(Ordering::SeqCst), 1);
    }
}
