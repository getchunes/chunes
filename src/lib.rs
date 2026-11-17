//! The Chunes Windows app.
//!
//! `protocol`, `attribution`, `presence`, `discord`, `tab_server` and
//! `updater` are platform independent so their tests run on any host.
//! `media` reads the Windows media session and only builds on Windows.

pub mod applog;
pub mod attribution;
pub mod autostart;
pub mod discord;
pub mod http;
pub mod menu;
pub mod packaged;
pub mod presence;
pub mod protocol;
pub mod registry;
pub mod settings;
pub mod startup_task;
pub mod tab_server;
pub mod updater;

#[cfg(windows)]
pub mod app;
#[cfg(windows)]
pub mod engine;
#[cfg(windows)]
pub mod media;
#[cfg(windows)]
pub mod tray;
