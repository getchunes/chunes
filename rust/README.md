# Chunes in Rust (proof of concept)

This directory is an exploratory port of the Windows app to Rust. It is not
shipped, packaged, or signed, and the Python app at the repository root is
still the release build.

What is ported so far:

- `src/protocol.rs`: the Chune ID report and request validation from
  `protocol.py`, with its tests ported to `src/protocol/tests.rs`. Limits,
  status codes, and the v4/v5 negotiation are unchanged, so the extension
  needs no changes.
- `src/tab_server.rs`: the `127.0.0.1:52846` endpoint (`POST /tabs`,
  `GET /state`).
- `src/attribution.rs`: matching a media-session title to a reported tab
  (`resolve_tab` and friends), with the `ResolveTabTests` cases.
- `src/discord.rs`: Discord IPC framing and `SET_ACTIVITY`, replacing
  `pypresence`.
- `src/media.rs`: reading the Windows media session through the `windows`
  crate, replacing the `winrt-*` wheels.
- `src/main.rs`: a console presence loop that ties them together.

Not ported yet: the tray, settings, autostart, single-instance mutex,
artwork lookup, Apple Music timing workarounds, fallback tracks, and the
updater.

## Run it

On 64-bit Windows with the Rust toolchain and the Visual Studio C++ build
tools installed, quit the Python Chunes first (both bind port 52846), then:

```powershell
cd rust
cargo run
```

The platform-independent tests also run on Linux and macOS:

```powershell
cargo test
```
