//! "Start with Windows".
//!
//! A port of the autostart half of `chunes.py`. The MSI build owns a
//! `HKCU\...\Run` value named Chunes; the Store build toggles the manifest's
//! StartupTask instead (see `startup_task`), because its install path changes
//! with every Store update. A Run value that does not look like Chunes is
//! never overwritten or deleted.

use crate::log;
use crate::startup_task::StartupTask;

pub const APP_NAME: &str = "Chunes";
pub const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";

/// The Run value, as far as autostart is concerned.
pub trait RunKey {
    /// `None` when there is no Chunes value, `Some("")` for one of an
    /// unrelated registry type (which is preserved), else its command.
    fn read(&self) -> Option<String>;
    fn write(&self, command: &str);
    fn delete(&self);
}

/// How command lines are split and paths compared on this machine.
pub struct Commands {
    /// The command that starts this executable.
    pub launch: Vec<String>,
    pub parse: fn(&str) -> Vec<String>,
    pub normalize: fn(&str) -> String,
}

/// Quote arguments the way `subprocess.list2cmdline` does.
pub fn list2cmdline(args: &[String]) -> String {
    let mut line = String::new();
    for (index, arg) in args.iter().enumerate() {
        if index > 0 {
            line.push(' ');
        }
        let quote = arg.is_empty() || arg.contains([' ', '\t']);
        if quote {
            line.push('"');
        }
        let mut backslashes = 0;
        for c in arg.chars() {
            match c {
                '\\' => backslashes += 1,
                '"' => {
                    line.push_str(&"\\".repeat(backslashes * 2 + 1));
                    line.push('"');
                    backslashes = 0;
                }
                _ => {
                    line.push_str(&"\\".repeat(backslashes));
                    line.push(c);
                    backslashes = 0;
                }
            }
        }
        line.push_str(&"\\".repeat(if quote { backslashes * 2 } else { backslashes }));
        if quote {
            line.push('"');
        }
    }
    line
}

fn file_name(path: &str) -> String {
    path.rsplit(['\\', '/']).next().unwrap_or("").to_lowercase()
}

impl Commands {
    fn parts(&self, command: Option<&str>) -> Vec<String> {
        match command {
            Some(command) if !command.trim().is_empty() => (self.parse)(command),
            _ => Vec::new(),
        }
    }

    pub fn launch_command(&self) -> String {
        list2cmdline(&self.launch)
    }

    /// Exactly the command that starts this executable.
    pub fn is_current(&self, command: Option<&str>) -> bool {
        let actual = self.parts(command);
        actual.len() == self.launch.len()
            && actual
                .iter()
                .zip(&self.launch)
                .all(|(a, b)| (self.normalize)(a) == (self.normalize)(b))
    }

    /// A command some Chunes install wrote: any `Chunes.exe` on its own, or
    /// the Python source build's `pythonw.exe chunes.py`.
    pub fn is_chunes_owned(&self, command: Option<&str>) -> bool {
        match self.parts(command).as_slice() {
            [exe] => file_name(exe) == "chunes.exe",
            [python, script] => {
                matches!(file_name(python).as_str(), "python.exe" | "pythonw.exe")
                    && file_name(script) == "chunes.py"
            }
            _ => false,
        }
    }
}

/// The Run-key autostart the MSI build uses.
pub struct RunKeyAutostart<R: RunKey> {
    pub key: R,
    pub commands: Commands,
}

impl<R: RunKey> RunKeyAutostart<R> {
    pub fn enabled(&self) -> bool {
        self.commands.is_current(self.key.read().as_deref())
    }

    /// Point an owned legacy entry (an older install path, or the source
    /// build) at this executable.
    pub fn migrate(&self) -> bool {
        let command = self.key.read();
        if command.is_some()
            && !self.commands.is_current(command.as_deref())
            && self.commands.is_chunes_owned(command.as_deref())
        {
            self.key.write(&self.commands.launch_command());
            log!("Migrated the Chunes autostart command to the installed executable.");
            return true;
        }
        false
    }

    /// The uninstaller's cleanup: remove the value only if Chunes owns it.
    pub fn remove_owned(&self) -> bool {
        let command = self.key.read();
        if self.commands.is_current(command.as_deref())
            || self.commands.is_chunes_owned(command.as_deref())
        {
            self.key.delete();
            return true;
        }
        false
    }

    pub fn toggle(&self) {
        let command = self.key.read();
        if self.commands.is_current(command.as_deref()) {
            self.key.delete();
        } else if command.is_none() || self.commands.is_chunes_owned(command.as_deref()) {
            self.key.write(&self.commands.launch_command());
        } else {
            log!("Not replacing an unrelated Windows Run value named Chunes.");
        }
    }
}

/// Autostart for whichever build this is.
pub enum Autostart<R: RunKey> {
    RunKey(RunKeyAutostart<R>),
    /// The packaged build never touches the Run key.
    Packaged(StartupTask),
}

impl<R: RunKey> Autostart<R> {
    pub fn enabled(&self) -> bool {
        match self {
            Autostart::RunKey(run) => run.enabled(),
            Autostart::Packaged(task) => task.is_enabled(),
        }
    }

    /// The packaged build never owned a Run value to migrate, and its own
    /// install path changes with every Store update.
    pub fn migrate(&self) -> bool {
        match self {
            Autostart::RunKey(run) => run.migrate(),
            Autostart::Packaged(_) => false,
        }
    }

    pub fn remove_owned(&self) -> bool {
        match self {
            Autostart::RunKey(run) => run.remove_owned(),
            Autostart::Packaged(_) => false,
        }
    }

    /// Flip the menu checkbox. `open_settings` opens the Startup apps page,
    /// the only place a refused or unavailable StartupTask can be changed.
    pub fn toggle(&self, open_settings: &dyn Fn()) {
        let task = match self {
            Autostart::RunKey(run) => return run.toggle(),
            Autostart::Packaged(task) => task,
        };
        if task.is_enabled() {
            if let Err(error) = task.disable() {
                log!("Startup task is unavailable ({error}); opening Windows Settings.");
                open_settings();
            }
            return;
        }
        match task.enable() {
            Err(error) => {
                log!("Startup task is unavailable ({error}); opening Windows Settings.");
                open_settings();
            }
            // Windows refuses the request once the user or a policy has
            // turned Chunes off; only Settings can undo that.
            Ok(state) if !state.is_enabled() => {
                log!("Windows kept Chunes autostart {state}; opening Windows Settings.");
                open_settings();
            }
            Ok(_) => {}
        }
    }
}

#[cfg(windows)]
pub mod windows_run_key {
    //! The real Run value and command-line handling.
    use super::{Commands, RunKey, APP_NAME, RUN_KEY};
    use crate::registry::{self, Value};

    pub struct HkcuRun;

    impl RunKey for HkcuRun {
        fn read(&self) -> Option<String> {
            match registry::get(RUN_KEY, APP_NAME)? {
                Value::String(command) => Some(command),
                // Preserve a same-named value with an unrelated type.
                _ => Some(String::new()),
            }
        }

        fn write(&self, command: &str) {
            if let Err(error) = registry::set_string(RUN_KEY, APP_NAME, command) {
                crate::log!("Could not write the autostart command: {error}");
            }
        }

        fn delete(&self) {
            let _ = registry::delete(RUN_KEY, APP_NAME);
        }
    }

    /// Split a command line exactly as Windows does at process start.
    pub fn parse(command: &str) -> Vec<String> {
        use windows::core::HSTRING;
        use windows::Win32::Foundation::{LocalFree, HLOCAL};
        use windows::Win32::UI::Shell::CommandLineToArgvW;

        let mut count = 0;
        // SAFETY: `command` is a valid NUL-terminated string; the returned
        // array is freed with LocalFree below.
        let argv = unsafe { CommandLineToArgvW(&HSTRING::from(command), &mut count) };
        if argv.is_null() {
            return Vec::new();
        }
        // SAFETY: CommandLineToArgvW returned `count` valid strings.
        let parts = (0..count as usize)
            .map(|i| unsafe { (*argv.add(i)).to_string().unwrap_or_default() })
            .collect();
        // SAFETY: the array came from CommandLineToArgvW.
        unsafe {
            let _ = LocalFree(Some(HLOCAL(argv.cast())));
        }
        parts
    }

    fn expand(value: &str) -> String {
        use windows::core::HSTRING;
        use windows::Win32::System::Environment::ExpandEnvironmentStringsW;

        let source = HSTRING::from(value);
        // SAFETY: a size query, then a buffer of that size.
        let size = unsafe { ExpandEnvironmentStringsW(&source, None) };
        if size == 0 {
            return value.to_owned();
        }
        let mut buffer = vec![0u16; size as usize];
        let written = unsafe { ExpandEnvironmentStringsW(&source, Some(&mut buffer)) };
        if written == 0 || written > size {
            return value.to_owned();
        }
        String::from_utf16_lossy(&buffer[..written as usize - 1])
    }

    /// `os.path.normcase(os.path.realpath(os.path.abspath(expandvars(p))))`.
    pub fn normalize(value: &str) -> String {
        let expanded = expand(value);
        let absolute = std::path::absolute(&expanded).unwrap_or_else(|_| expanded.clone().into());
        let real = std::fs::canonicalize(&absolute).unwrap_or(absolute);
        let text = real.to_string_lossy();
        let text = text
            .strip_prefix(r"\\?\UNC\")
            .map(|rest| format!(r"\\{rest}"))
            .unwrap_or_else(|| text.strip_prefix(r"\\?\").unwrap_or(&text).to_owned());
        text.replace('/', "\\").to_lowercase()
    }

    pub fn commands() -> Commands {
        let exe = std::env::current_exe()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default();
        Commands {
            launch: vec![exe],
            parse,
            normalize,
        }
    }
}

#[cfg(test)]
mod tests {
    //! Ported from `AutostartTests` and `PackagedTrayTests` in
    //! `tests/test_chunes.py`.
    use super::*;
    use crate::startup_task::{State, Task, Unavailable, CALL_TIMEOUT};
    use std::cell::{Cell, RefCell};
    use std::sync::{Arc, Mutex};

    #[derive(Default)]
    struct FakeKey {
        value: RefCell<Option<String>>,
        writes: RefCell<Vec<String>>,
        deletes: Cell<usize>,
        reads: Cell<usize>,
    }

    impl RunKey for &FakeKey {
        fn read(&self) -> Option<String> {
            self.reads.set(self.reads.get() + 1);
            self.value.borrow().clone()
        }
        fn write(&self, command: &str) {
            self.writes.borrow_mut().push(command.to_owned());
            *self.value.borrow_mut() = Some(command.to_owned());
        }
        fn delete(&self) {
            self.deletes.set(self.deletes.get() + 1);
            *self.value.borrow_mut() = None;
        }
    }

    /// Split on `|` so tests choose the parts exactly.
    fn split(command: &str) -> Vec<String> {
        command.split('|').map(str::to_owned).collect()
    }

    fn lower(path: &str) -> String {
        path.to_lowercase()
    }

    fn commands() -> Commands {
        Commands {
            launch: vec![r"C:\Apps\Chunes.exe".into()],
            parse: split,
            normalize: lower,
        }
    }

    fn run_key(key: &FakeKey) -> RunKeyAutostart<&FakeKey> {
        RunKeyAutostart {
            key,
            commands: commands(),
        }
    }

    #[test]
    fn test_checked_state_requires_the_exact_current_command() {
        let c = commands();
        assert!(c.is_current(Some(r"C:\Apps\Chunes.exe")));
        assert!(c.is_current(Some(r"c:\apps\CHUNES.EXE")));
        assert!(!c.is_current(Some(r"D:\Portable\Chunes.exe")));
        assert!(!c.is_current(None));
        assert!(!c.is_current(Some("  ")));
    }

    #[test]
    fn test_only_recognized_chunes_commands_are_owned() {
        let c = commands();
        let cases = [
            (r"D:\Portable\Chunes.exe", true),
            (r"C:\Python\pythonw.exe|D:\src\chunes.py", true),
            (r"C:\Windows\notepad.exe", false),
            (r"D:\Other\Chunes.exe|--unrelated", false),
        ];
        for (parts, expected) in cases {
            assert_eq!(c.is_chunes_owned(Some(parts)), expected, "{parts}");
        }
    }

    #[test]
    fn test_migration_rewrites_owned_legacy_entry_only() {
        let key = FakeKey::default();
        *key.value.borrow_mut() = Some(r"D:\Old\Chunes.exe".into());
        assert!(run_key(&key).migrate());
        assert_eq!(key.writes.borrow().as_slice(), [r"C:\Apps\Chunes.exe"]);
        // Already current, or not ours: nothing to do.
        assert!(!run_key(&key).migrate());
        *key.value.borrow_mut() = Some(r"C:\Windows\notepad.exe".into());
        assert!(!run_key(&key).migrate());
        assert_eq!(key.writes.borrow().len(), 1);
    }

    #[test]
    fn test_unrelated_run_value_is_not_overwritten_or_deleted() {
        let key = FakeKey::default();
        *key.value.borrow_mut() = Some(r"C:\Other\other.exe".into());
        run_key(&key).toggle();
        assert!(key.writes.borrow().is_empty());
        assert_eq!(key.deletes.get(), 0);
        // A value of an unrelated type reads as "" and is kept too.
        *key.value.borrow_mut() = Some(String::new());
        run_key(&key).toggle();
        assert!(key.writes.borrow().is_empty());
    }

    #[test]
    fn test_toggle_adds_and_removes_the_current_command() {
        let key = FakeKey::default();
        run_key(&key).toggle();
        assert!(run_key(&key).enabled());
        run_key(&key).toggle();
        assert!(!run_key(&key).enabled());
        assert_eq!(key.deletes.get(), 1);
    }

    #[test]
    fn test_uninstall_helper_removes_owned_current_or_legacy_entry() {
        let key = FakeKey::default();
        *key.value.borrow_mut() = Some(r"D:\Old\Chunes.exe".into());
        assert!(run_key(&key).remove_owned());
        assert_eq!(key.deletes.get(), 1);
        *key.value.borrow_mut() = Some(r"C:\Other\other.exe".into());
        assert!(!run_key(&key).remove_owned());
        assert_eq!(key.deletes.get(), 1);
    }

    #[test]
    fn test_launch_command_quotes_like_list2cmdline() {
        assert_eq!(
            list2cmdline(&[r"C:\Program Files\Chunes\Chunes.exe".into()]),
            r#""C:\Program Files\Chunes\Chunes.exe""#
        );
        assert_eq!(
            list2cmdline(&[r"C:\Apps\Chunes.exe".into()]),
            r"C:\Apps\Chunes.exe"
        );
        assert_eq!(
            list2cmdline(&["a b\\".into(), "x\"y".into(), String::new()]),
            r#""a b\\" x\"y """#
        );
    }

    struct FakeTask {
        state: Arc<Mutex<State>>,
        enable_result: Result<State, Unavailable>,
        disables: Arc<Mutex<usize>>,
    }

    impl Task for FakeTask {
        fn state(&self) -> Result<State, Unavailable> {
            Ok(*self.state.lock().unwrap())
        }
        fn request_enable(&self) -> Result<State, Unavailable> {
            self.enable_result.clone()
        }
        fn disable(&self) -> Result<(), Unavailable> {
            *self.disables.lock().unwrap() += 1;
            *self.state.lock().unwrap() = State::Disabled;
            Ok(())
        }
    }

    fn packaged(
        state: State,
        enable_result: Result<State, Unavailable>,
    ) -> (Autostart<&'static FakeKey>, Arc<Mutex<usize>>) {
        let disables = Arc::new(Mutex::new(0));
        let shared = Arc::new(Mutex::new(state));
        let count = disables.clone();
        let task = StartupTask::new(
            move || {
                Ok(Box::new(FakeTask {
                    state: shared.clone(),
                    enable_result: enable_result.clone(),
                    disables: count.clone(),
                }) as Box<dyn Task>)
            },
            CALL_TIMEOUT,
        );
        (Autostart::Packaged(task), disables)
    }

    #[test]
    fn test_the_run_key_is_never_touched_by_the_packaged_build() {
        // The packaged variant has no Run key to read at all; check it
        // answers from the task and turns the task off.
        let (autostart, disables) = packaged(State::Enabled, Ok(State::Enabled));
        assert!(autostart.enabled());
        assert!(!autostart.migrate());
        assert!(!autostart.remove_owned());
        let opened = Cell::new(false);
        autostart.toggle(&|| opened.set(true));
        assert_eq!(*disables.lock().unwrap(), 1);
        assert!(!opened.get());
    }

    #[test]
    fn test_a_refused_startup_request_sends_the_user_to_windows_settings() {
        for (result, expect_settings) in [(State::DisabledByUser, true), (State::Enabled, false)] {
            let (autostart, _) = packaged(State::Disabled, Ok(result));
            let opened = Cell::new(false);
            autostart.toggle(&|| opened.set(true));
            assert_eq!(opened.get(), expect_settings, "{result}");
        }
    }

    #[test]
    fn test_an_unavailable_startup_task_opens_windows_settings() {
        let task = StartupTask::new(|| Err(Unavailable("no package".into())), CALL_TIMEOUT);
        let autostart: Autostart<&FakeKey> = Autostart::Packaged(task);
        let opened = Cell::new(false);
        autostart.toggle(&|| opened.set(true));
        assert!(opened.get());
    }
}
