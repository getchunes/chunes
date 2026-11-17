//! The tray app: what `chunes.py` did at startup.
//!
//! Opens the log, answers the installer's helper commands, keeps a single
//! instance, migrates legacy autostart, runs the presence engine on its own
//! thread (restarting it if it ever stops), starts the GitHub release check
//! for the MSI build, and runs the tray on the main thread.

use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

use crate::autostart::windows_run_key::{self, HkcuRun};
use crate::autostart::{Autostart, RunKeyAutostart, APP_NAME};
use crate::menu::{self, Checks, Command, Entry, Status};
use crate::startup_task::{self, StartupTask};
use crate::tab_server::{self, SharedState};
use crate::tray::{self, Tray};
use crate::updater::{self, UpdateController};
use crate::{applog, engine, log, packaged, settings};

const RESTART_DELAY: Duration = Duration::from_secs(30);

pub fn log_path() -> PathBuf {
    let local = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    packaged::log_dir(&local, packaged::package_family_name()).join("chunes.log")
}

fn acquire_single_instance() -> bool {
    use windows::core::w;
    use windows::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS};
    use windows::Win32::System::Threading::CreateMutexW;

    // SAFETY: a named mutex; the handle is deliberately kept open for the
    // life of the process.
    unsafe {
        match CreateMutexW(None, false, w!(r"Local\Chunes.SingleInstance")) {
            Ok(handle) if GetLastError() == ERROR_ALREADY_EXISTS => {
                let _ = CloseHandle(handle);
                false
            }
            Ok(_) => true,
            Err(error) => {
                log!("Could not create the single-instance mutex: {error}");
                true
            }
        }
    }
}

fn autostart() -> Autostart<HkcuRun> {
    if packaged::is_packaged() {
        Autostart::Packaged(StartupTask::windows())
    } else {
        Autostart::RunKey(RunKeyAutostart {
            key: HkcuRun,
            commands: windows_run_key::commands(),
        })
    }
}

fn open_startup_settings() {
    if !updater::open_in_browser(startup_task::SETTINGS_URI) {
        log!("Could not open the Startup apps settings page.");
    }
}

/// Run the presence engine forever, restarting it 30 seconds after a crash.
fn run_engine(state: SharedState) {
    loop {
        let shared = state.clone();
        let outcome = std::panic::catch_unwind(move || {
            tokio::runtime::Builder::new_multi_thread()
                .enable_all()
                .build()
                .map(|runtime| runtime.block_on(engine::run(shared, settings::switches)))
        });
        match outcome {
            Ok(Ok(())) => log!("Engine stopped, restarting in 30s"),
            Ok(Err(error)) => log!("Engine stopped ({error}), restarting in 30s"),
            Err(_) => log!("Engine stopped (panic), restarting in 30s"),
        }
        {
            let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
            shared.track = None;
            shared.host = None;
        }
        std::thread::sleep(RESTART_DELAY);
    }
}

struct App {
    state: SharedState,
    autostart: Autostart<HkcuRun>,
    store_updates: bool,
    updater: Option<UpdateController>,
    log_path: PathBuf,
}

impl App {
    fn status(&self) -> Status {
        let shared = self.state.lock().unwrap_or_else(|e| e.into_inner());
        Status {
            track: shared.track.clone(),
            extension_enabled: shared.fresh_report(tab_server::now()).map(|r| r.enabled),
        }
    }
}

impl tray::Host for App {
    fn menu(&self) -> Vec<Entry> {
        let checks = Checks {
            autostart: self.autostart.enabled(),
            automatic_updates: settings::automatic_updates_enabled(),
            artwork: settings::artwork_enabled(),
            track_button: settings::track_button_enabled(),
            get_chunes_button: settings::get_chunes_button_enabled(),
        };
        menu::build(&self.status(), self.store_updates, checks)
    }

    fn command(&self, command: Command) {
        match command {
            Command::ToggleAutostart => self.autostart.toggle(&open_startup_settings),
            Command::ToggleAutomaticUpdates => {
                settings::set_automatic_updates(!settings::automatic_updates_enabled())
            }
            Command::CheckForUpdates => {
                if let Some(updater) = &self.updater {
                    updater.check_now();
                }
            }
            Command::ToggleArtwork => settings::set_artwork_enabled(!settings::artwork_enabled()),
            Command::ToggleTrackButton => {
                settings::set_track_button_enabled(!settings::track_button_enabled())
            }
            Command::ToggleGetChunesButton => {
                settings::set_get_chunes_button_enabled(!settings::get_chunes_button_enabled())
            }
            Command::OpenLog => {
                if !updater::open_in_browser(&self.log_path.to_string_lossy()) {
                    log!("Could not open {}", self.log_path.display());
                }
            }
            Command::Quit => {}
        }
    }

    fn ready(&self, tray: &Tray) {
        show_tray_intro(&|title, text| tray.notify(title, text));
    }
}

/// The first launch points at the tray icon once; a failed notification is
/// not remembered, so the next launch tries again.
pub fn show_tray_intro(notify: &dyn Fn(&str, &str) -> bool) {
    if settings::tray_intro_shown() {
        return;
    }
    if notify(APP_NAME, menu::TRAY_INTRO_MESSAGE) {
        settings::set_tray_intro_shown();
    }
}

/// The installer runs Chunes.exe with one of these and nothing else.
fn handle_installer_command(args: &[String]) -> bool {
    match args {
        [flag] if flag == "--migrate-autostart" => {
            autostart().migrate();
            true
        }
        [flag] if flag == "--remove-autostart-if-owned" => {
            autostart().remove_owned();
            true
        }
        _ => false,
    }
}

pub fn main() {
    let log_path = log_path();
    if let Err(error) = applog::open(&log_path) {
        eprintln!("Could not open {}: {error}", log_path.display());
    }
    let args: Vec<String> = std::env::args().skip(1).collect();
    if handle_installer_command(&args) {
        return;
    }
    if !acquire_single_instance() {
        log!("Chunes is already running; the second instance is exiting.");
        return;
    }
    let store_updates = packaged::is_packaged();
    if let Some(family) = packaged::package_family_name() {
        log!("Running from package {family}; the Store handles updates.");
    }
    let autostart = autostart();
    autostart.migrate();

    let state = SharedState::default();
    let engine_state = state.clone();
    std::thread::Builder::new()
        .name("Chunes engine".into())
        .spawn(move || run_engine(engine_state))
        .expect("the engine thread starts");

    let updater = (!store_updates).then(|| {
        let updater = UpdateController::new(settings::automatic_updates_enabled);
        if settings::automatic_updates_enabled() {
            updater.start_automatic_check();
        }
        updater
    });
    let app = App {
        state,
        autostart,
        store_updates,
        updater,
        log_path,
    };
    if let Err(error) = tray::run(Rc::new(app)) {
        log!("The tray could not start: {error}");
    }
}
