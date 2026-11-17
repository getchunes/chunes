# Chunes in Rust

The crate at the repository root is the port of the Windows app to Rust.
`scripts/build.ps1` and the release workflows build `Chunes.exe` from it with
cargo and package that executable into the MSI and the MSIX. The Python
sources stay in the repository until the port is complete.

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
cargo run
```

The platform-independent tests also run on Linux and macOS:

```powershell
cargo test
```
