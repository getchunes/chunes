# Chunes

<img src="assets/logo.svg" alt="Chunes logo" width="160">

[![Chunes release](https://img.shields.io/github/v/release/getchunes/chunes?label=Chunes)](https://github.com/getchunes/chunes/releases/latest) [![Chune ID on the Chrome Web Store](https://img.shields.io/chrome-web-store/v/ofbfkbhgfhoapckgjcpmcohbhnogpfjd?label=Chune%20ID)](https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd)

<a href="https://apps.microsoft.com/detail/9N7DTVS01C6Z?hl=en-us&gl=US&ocid=pdpshare">
	<img src="https://get.microsoft.com/images/en-us%20dark.svg" width="200"/>
</a>

Chunes shows SoundCloud and YouTube Music playback as a Discord **Listening to**
status on Windows. It runs in the notification area, clears the status when
playback pauses, and labels both supported services correctly.

## Requirements

- 64-bit Windows 10 or Windows 11
- the Discord desktop app running on the same PC
- the [Chune ID browser extension](https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd)
  for accurate browser service, pause, and enable/disable filtering. This
  Chunes release accepts protocol 4 and 5, which Chune ID 1.0.10 and later send;
  the [extension source is on GitHub](https://github.com/getchunes/chunes-extension)

## Install

Chunes ships through two channels. The Microsoft Store build updates itself
through the Store. The MSI is the direct download and checks GitHub releases
for updates. Both are the same application; pick whichever suits you.

### Microsoft Store

Search the Microsoft Store for **Chunes**, or install it from the Store
listing. Store installs update automatically and do not show the GitHub update
options in the tray menu. Autostart is managed by the manifest startup task, so
Windows **Settings > Apps > Startup** can also turn it off.

The `.msix` produced by the **Build Microsoft Store MSIX** workflow is a
submission artifact for Partner Center. It is intentionally unsigned, because
the Store signs it during certification, and it **cannot be sideloaded**. It is
never attached to a GitHub release.

### MSI

1. Download `Chunes-<version>-x64.msi` from the
   [latest release](https://github.com/getchunes/chunes/releases/latest).
2. Check the release-specific trust information below before running the MSI.
3. Run the per-user installer. It does not require administrator access and
   adds a Chunes shortcut to the Start menu.
4. Review the installer update and online-artwork options and the
   [privacy policy](PRIVACY.md). Both network features default to on and can be
   unchecked independently before install.
5. Install [Chune ID](https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd)
   and make sure Discord's **Share my activity** setting is on and the Discord
   status is not Invisible.

Chunes starts with Windows after a first install, on both channels. Turn it off
from the tray menu, or from Windows **Settings > Apps > Startup**. Upgrades keep
that choice; they never restore autostart once you have turned it off.

Run a newer MSI to upgrade Chunes or rerun the current MSI to repair it. Windows
**Installed apps** can uninstall Chunes. New MSI releases use a stable
UpgradeCode, restore a custom installation directory, and preserve existing
automatic-update and artwork opt-outs during upgrades.

### Release trust

GitHub releases of the MSI are not code signed, so Windows reports **Unknown
publisher** when you run it. Each release title and installer says so. The
Microsoft Store build is signed by Microsoft. If you want a signed install, use
the Store.

Every GitHub release and tag is immutable. To confirm an MSI is the one
published, compare its hash with both the SHA-256 printed in the release notes
and the `sha256:` asset digest shown by GitHub:

```powershell
(Get-FileHash .\Chunes-<version>-x64.msi -Algorithm SHA256).Hash.ToLowerInvariant()
```

A match confirms that the downloaded bytes are the bytes in that immutable
GitHub release. It does not provide Authenticode publisher identity or prove
that the code is safe.

## How it works

Chunes reads the active Windows media session and sends track title, artist,
timing, service label, and optional artwork URL to the local Discord desktop
client over Discord Rich Presence IPC.

Windows identifies browser media only as the browser. Chune ID therefore posts
this exact JSON shape to the loopback-only `127.0.0.1:52846/tabs` endpoint with
the exact media type `application/json`:

```json
{
  "enabled": true,
  "services": {
    "appleMusic": true,
    "soundcloud": true,
    "youtubeMusic": true
  },
  "tabs": [
    {"host": "soundcloud.com", "mediaId": null, "title": "Track by Artist"},
    {"host": "music.youtube.com", "mediaId": "a1B2c3D4e5F", "title": "Track - Artist"}
  ],
  "protocol": 5
}
```

While a report is fresh, the extension's master switch, per-service switches,
and reported audible tabs are authoritative for browser media. Disabled
services, unrelated browser media, and paused tabs are suppressed. A fresh
report expires after 90 seconds. The tray displays **Chune ID: on**, **off**, or
**not connected** so the extension master state is visible without duplicating
its control in the app.

Reports carry `"protocol": 4` or `"protocol": 5` and optional, bounds-checked
page Media Session metadata, and Chunes answers with the `X-Chunes-Protocol`
header matching the version the report arrived in. Protocol 5 adds an optional
`trackUrl` per tab, which must be an `https` address on that tab's own host, so
the presence button can open the track itself rather than a search for it.
Earlier protocol versions are no longer accepted.

A tab that reports its own metadata names the track it is playing, so it stays
identified even while another tab makes noise. Starting a regular YouTube video
during a song therefore no longer clears the status. A title that names no
reporting tab is still left unattributed whenever unrelated browser audio could
be the one the Windows media session is describing.

Without Chune ID, Chunes can still use Windows media metadata, but it cannot
reliably distinguish supported music from unrelated browser playback or retain
the correct service when another tab owns the browser media session.

## Tray settings

- **Start with Windows** adds or removes the current-user Windows `Run` entry.
  It is checked only when that entry resolves to the running Chunes executable.
  Installed Chunes migrates recognized portable or source commands but leaves
  unrelated same-named registry values untouched.
- **Automatically check for updates** persists the startup update preference.
- **Check for updates now** performs an immediate manual check.
- **Look up online album art** controls whether Chunes uses the provider artwork
  URL supplied locally by Chune ID or looks the cover up on the public Apple
  Search API.
- **Show "Play on..." button** adds a button to the Discord status that opens
  the playing track on its own service. On by default.
- **Show "Get Chunes" button** adds a second button linking to the Chune ID
  listing on the Chrome Web Store. Off by default.
- **Open log** opens `%LOCALAPPDATA%\Chunes\chunes.log`.
- **Quit** clears the process from the notification area and stops Chunes.

Discord shows presence buttons to everyone who opens the profile except the
listener, so you will not see your own.

These settings are all checkable and stored under
`HKEY_CURRENT_USER\Software\Chunes`. See [PRIVACY.md](PRIVACY.md) for every
local and network data flow and the corresponding opt-outs.

## Update security

Chunes checks published releases in `getchunes/chunes`. When it finds a newer
numeric version, it opens that version's exact GitHub release page in the
default browser. Chunes never downloads an MSI, starts Windows Installer, or
exits during an update check; updates are always installed manually.

## Configuration

No configuration file is required. Advanced users can copy the shape from
`config.example.json` to `config.json` next to the script or installed
executable:

```json
{
  "client_id": "1527834085383213106",
  "sources": ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"],
  "service_label": "",
  "image_key": ""
}
```

`client_id` selects the Discord application. `sources` contains source-app
substrings allowed from Windows media sessions. `service_label` is fallback
artwork hover text when Chune ID cannot identify a service. `image_key` is a
fallback Discord application asset key or image URL.

## Develop and test

Use 64-bit Python on Windows. Runtime and build versions are pinned separately;
CI and releases additionally use the wheel hashes in
`requirements-windows-py313.lock`:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python presence.py
```

`presence.py` runs in a console for debugging; `chunes.py` runs the tray app.

Build the versioned executable and per-user MSI with cargo and WiX Toolset
3.14.1. The Rust toolchain is pinned by `rust-toolchain.toml`, and building
needs the Visual Studio C++ build tools and the Windows SDK. CI downloads the
official WiX archive through `scripts/get-wix.ps1` and requires its fixed
SHA-256 before use:

```powershell
.\scripts\build.ps1
```

The outputs are `dist\Chunes.exe` and `dist\Chunes-<version>-x64.msi`.
`build.rs` embeds the `Cargo.toml` package version as four-part file and
product metadata, the canonical executable icon, and the application manifest
from `installer\chunes.rc`.

## Release process

`.github/workflows/release-unsigned.yml` publishes GitHub releases. It runs only
by explicit workflow dispatch from `main`, requires explicit confirmation and a
protected `unsigned-manual-release` environment, proves the raw EXE, embedded
EXE, and MSI are unsigned, checks the MSI identity, and includes a versioned
installer warning. It publishes an immutable normal GitHub release as `latest`,
which the in-app release check finds.

Repository release immutability is enabled and must be independently confirmed
by a maintainer before the workflow is dispatched; GitHub's scoped workflow
token cannot read that administrator-only setting. The workflow requires an
unused release and tag, an exact tag ref at the dispatched `GITHUB_SHA`, a draft
containing only the expected MSI, and a GitHub asset digest matching the local
SHA-256. It rechecks the tag immediately before publication and fails unless
the published release reports `immutable: true`. External actions are
pinned to resolved full commit SHAs, checkout credentials are not persisted,
and handoff artifacts expire after one day without compression. Published
releases are never recreated; corrections always use a new version.

The step-by-step runbook is in [RELEASING.md](RELEASING.md).

## Maintainers

- Authors and committers: [@dubsector](https://github.com/dubsector)
- Reviewers: [@dubsector](https://github.com/dubsector)
- Approvers: [@dubsector](https://github.com/dubsector)
- Privacy policy: [PRIVACY.md](PRIVACY.md)

Chunes transfers information only for Discord presence, optional SoundCloud or
YouTube Music album artwork, and optional GitHub update functions described in the
privacy policy. The installer and tray provide the documented opt-outs.

## License and notices

Chunes is licensed under the [Apache License 2.0](LICENSE). Third-party software,
the Bootstrap-derived note geometry, and service trademarks are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Chunes is not affiliated with,
sponsored by, or endorsed by Discord, SoundCloud, Google, YouTube, Microsoft, or
GitHub.
