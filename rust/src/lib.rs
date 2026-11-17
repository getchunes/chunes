//! Proof-of-concept Rust port of the Chunes Windows app.
//!
//! `protocol` and `discord` are platform independent so their tests run on
//! any host. `media` reads the Windows media session and only builds on
//! Windows.

pub mod attribution;
pub mod discord;
pub mod protocol;
pub mod tab_server;

#[cfg(windows)]
pub mod media;
