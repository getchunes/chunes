//! Chunes.exe: the tray app, or one of the installer's helper commands.

#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(windows)]
fn main() {
    chunes::app::main();
}

#[cfg(not(windows))]
fn main() {
    eprintln!("Chunes reads the Windows media session and only runs on Windows.");
    std::process::exit(1);
}
