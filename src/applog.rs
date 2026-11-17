//! The rolling log file.
//!
//! The app has no console, so presence.py's `print` calls went to
//! `chunes.log` through a redirected `sys.stdout`. `log!` writes the same
//! lines there once `open` has run, and to standard output before that (and
//! in tests).

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;

/// A log over this size is moved aside to `chunes.log.old` at startup.
pub const MAX_LOG_BYTES: u64 = 1_000_000;

static SINK: Mutex<Option<File>> = Mutex::new(None);

/// Start appending to `path`, rotating it first when it has grown too big.
pub fn open(path: &Path) -> std::io::Result<()> {
    if let Some(dir) = path.parent() {
        std::fs::create_dir_all(dir)?;
    }
    if path.metadata().is_ok_and(|m| m.len() > MAX_LOG_BYTES) {
        // A short-lived installer helper may share the active process log,
        // so a failed rotation just keeps appending.
        let _ = std::fs::rename(path, path.with_extension("log.old"));
    }
    let file = OpenOptions::new().create(true).append(true).open(path)?;
    *SINK.lock().unwrap_or_else(|e| e.into_inner()) = Some(file);
    Ok(())
}

pub fn line(text: &str) {
    let mut sink = SINK.lock().unwrap_or_else(|e| e.into_inner());
    match sink.as_mut() {
        Some(file) => {
            let _ = writeln!(file, "{text}");
        }
        None => println!("{text}"),
    }
}

/// `println!` into the log file.
#[macro_export]
macro_rules! log {
    ($($arg:tt)*) => {
        $crate::applog::line(&format!($($arg)*))
    };
}
