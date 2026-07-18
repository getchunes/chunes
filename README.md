# Chunes

<img src="assets/logo.svg" alt="Chunes logo" width="160">

Chunes shows SoundCloud and YouTube Music playback as a Discord **Listening to**
status on Windows. It runs in the notification area, clears the status when
playback pauses, and labels both supported services correctly.

## Requirements

- 64-bit Windows 10 or Windows 11
- the Discord desktop app running on the same PC
- the [Chune ID browser extension](https://github.com/getchunes/chunes-extension)
  for accurate browser service, pause, and enable/disable filtering

## Install

1. Download `Chunes-<version>-x64.msi` from the
   [latest release](https://github.com/getchunes/chunes/releases/latest).
2. Confirm that Windows reports the MSI publisher as **SignPath Foundation**.
3. Run the per-user installer. It does not require administrator access and
   adds a Chunes shortcut to the Start menu.
4. Review the installer update and online-artwork options and the
   [privacy policy](PRIVACY.md). Both network features default to on and can be
   unchecked independently before install.
5. Install Chune ID and make sure Discord's **Share my activity** setting is on
   and the Discord status is not Invisible.

Run a newer MSI to upgrade Chunes or rerun the current MSI to repair it. Windows
**Installed apps** can uninstall Chunes. New MSI releases use a stable
UpgradeCode, restore a custom installation directory, and preserve existing
automatic-update and artwork opt-outs during upgrades.

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
    "soundcloud": true,
    "youtubeMusic": true
  },
  "tabs": [
    {"host": "soundcloud.com", "title": "Track by Artist"}
  ]
}
```

While a report is fresh, the extension's master switch, per-service switches,
and reported audible tabs are authoritative for browser media. Disabled
services, unrelated browser media, and paused tabs are suppressed. A fresh
report expires after 90 seconds. The tray displays **Chune ID: on**, **off**, or
**not connected** so the extension master state is visible without duplicating
its control in the app.

Every successful desktop response includes `X-Chunes-Protocol: 1`. Chune ID
must require that header before accepting a `200` or `204` response, so an old
desktop listener cannot be mistaken for protocol v1. This coordinated release
does not support the legacy tab-list payload.

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
- **Look up online cover art** persists whether title and artist may be searched
  on SoundCloud.
- **Open log** opens `%LOCALAPPDATA%\Chunes\chunes.log`.
- **Quit** clears the process from the notification area and stops Chunes.

The automatic-update and online-artwork settings are checkable and stored under
`HKEY_CURRENT_USER\Software\Chunes`. See [PRIVACY.md](PRIVACY.md) for every
local and network data flow and the corresponding opt-outs.

## Update security

Chunes 1.0.0 checks only the latest stable release in
`getchunes/chunes`. Before starting `msiexec`, the updater requires:

- a strictly newer stable semantic version
- exactly one asset named `Chunes-<version>-x64.msi`
- a byte-for-byte match with the SHA-256 digest returned by GitHub's release API
- a Windows-trusted Authenticode signature whose publisher is exactly
  **SignPath Foundation**
- MSI properties exactly identifying `ProductName=Chunes`, `Manufacturer=Chunes`,
  the selected release version, and UpgradeCode
  `{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}`

The user is prompted before download. An unsigned, altered, untrusted, or
differently published installer is deleted and never run. Automatic failures
go to the local log; manual failures are also shown to the user. After initial
verification, Chunes starts a Windows PowerShell helper and exits so files can
be replaced. The helper waits for Chunes to stop, repeats the hash, signature,
publisher, and MSI identity checks, waits for Windows Installer, then relaunches
the installed app. Cancellation or failure attempts to relaunch the unchanged
previous executable instead.

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

Build the versioned executable and per-user MSI with PyInstaller and WiX Toolset
3.14.1. CI downloads the official WiX archive through `scripts/get-wix.ps1` and
requires its fixed SHA-256 before use:

```powershell
.\scripts\build.ps1
```

The outputs are `dist\Chunes.exe` and `dist\Chunes-1.0.0-x64.msi`. The
PyInstaller spec embeds file/product version 1.0.0, the canonical executable
icon, and the canonical tray PNG. Runtime asset loading resolves through
PyInstaller's extraction directory when frozen.

## Release process

`.github/workflows/release.yml` runs only by explicit workflow dispatch from
`main` and refuses a version whose release tag already exists. Separate jobs
build without secrets, sign in the `code-signing` environment, and publish with
`contents: write` only in the `stable-release` environment. External actions
are pinned to resolved full commit SHAs, checkout credentials are not persisted,
and the unsigned and signed handoff artifacts expire after one day. Both signed
artifact handoffs are checked again before publication.

The existing zero-download `v1.0.0` release and tag are intentionally removed
manually only when the reviewed source is on `main`, SignPath secrets and both
GitHub environments are ready, and the final recreation is about to run. Then
dispatch version `1.0.0` with `confirm_v1_recreation` checked. The workflow never
deletes a release or tag itself and still fails if either tag is present. Do not
remove the current release early. Required SignPath secrets are
`SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_PROJECT_SLUG`, and
`SIGNPATH_SIGNING_POLICY_SLUG`.

The SignPath artifact configuration and account setup checklist are under
[`.signpath/`](.signpath/README.md). The configuration deep-signs the embedded
`Chunes.exe`, repackages the MSI, and then signs the MSI itself while enforcing
the Chunes product and version metadata.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org).

- Authors and committers: [@dubsector](https://github.com/dubsector)
- Reviewers: [@dubsector](https://github.com/dubsector)
- Approvers: [@dubsector](https://github.com/dubsector)
- Privacy policy: [PRIVACY.md](PRIVACY.md)

As the current maintainer, `dubsector` is trusted to author and commit project
changes, reviews changes from other contributors, and manually approves every
code-signing request. The Chunes project acknowledges and accepts the
[SignPath Foundation conditions for open-source code signing](https://signpath.org/terms).

Chunes transfers information only for the Discord presence, optional SoundCloud
artwork, and optional GitHub update functions explicitly described in the
privacy policy. The installer and tray provide the documented opt-outs.

## License and notices

Chunes is licensed under the [Apache License 2.0](LICENSE). Third-party software,
the Bootstrap-derived note geometry, and service trademarks are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Chunes is not affiliated with,
sponsored by, or endorsed by Discord, SoundCloud, Google, YouTube, Microsoft, or
GitHub.
