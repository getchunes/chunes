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
2. Check the release-specific trust information below before running the MSI.
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

### Release trust

Chunes v1.0.0 was the initial unsigned MSI. Windows reports **Unknown
publisher**. Obtain `Chunes-1.0.0-x64.msi` only from the
immutable [v1.0.0 GitHub release](https://github.com/getchunes/chunes/releases/tag/v1.0.0).
The release title and notes prominently identify it as **UNSIGNED INTERIM**.

GitHub locks the published v1.0.0 asset and tag against replacement. Compare
the local hash with both the SHA-256 printed in the release notes and the
`sha256:` asset digest shown by GitHub:

```powershell
(Get-FileHash .\Chunes-1.0.0-x64.msi -Algorithm SHA256).Hash.ToLowerInvariant()
```

A match confirms that the downloaded bytes are the bytes in that immutable
GitHub release. It does not provide Authenticode publisher identity, prove that
the code is safe, or turn the unsigned MSI into a signed one.

New releases use one of two channels. A signed stable release is published as
`latest` and is eligible for the in-app updater. If code signing is unavailable,
an unsigned release may instead be published as an explicitly labeled manual
prerelease; Windows shows **Unknown publisher**, and the in-app updater does not
offer it. Every release and tag is immutable. A later signing transition always
uses a higher version rather than replacing an unsigned release.

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
    {"host": "soundcloud.com", "mediaId": null, "title": "Track by Artist"},
    {"host": "music.youtube.com", "mediaId": "a1B2c3D4e5F", "title": "Track - Artist"}
  ]
}
```

While a report is fresh, the extension's master switch, per-service switches,
and reported audible tabs are authoritative for browser media. Disabled
services, unrelated browser media, and paused tabs are suppressed. A fresh
report expires after 90 seconds. The tray displays **Chune ID: on**, **off**, or
**not connected** so the extension master state is visible without duplicating
its control in the app.

Every successful desktop response includes `X-Chunes-Protocol: 2`. Chune ID
must require that header before accepting a `200` or `204` response, so an old
desktop listener cannot be mistaken for protocol v2. Chune ID and Chunes 1.0.1
replace the protocol-1 payload as a coordinated upgrade.

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
- **Look up online album art** persists whether Chunes may search SoundCloud or,
  for an identified YouTube Music track, request its exact square music artwork.
- **Open log** opens `%LOCALAPPDATA%\Chunes\chunes.log`.
- **Quit** clears the process from the notification area and stops Chunes.

The automatic-update and online-artwork settings are checkable and stored under
`HKEY_CURRENT_USER\Software\Chunes`. See [PRIVACY.md](PRIVACY.md) for every
local and network data flow and the corresponding opt-outs.

## Update security

Chunes checks only the latest stable release in `getchunes/chunes`. Before
starting `msiexec`, the updater requires:

- a strictly newer stable semantic version
- an immutable GitHub release
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

Running an unsigned Chunes version does not create an updater exception. It
remains fail-closed and will download or install only a newer
MSI that passes the immutable-release, digest, exact identity, Windows trust,
and exact **SignPath Foundation** publisher checks. There is no unsigned update
bypass.

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

The outputs are `dist\Chunes.exe` and `dist\Chunes-<version>-x64.msi`. The
PyInstaller spec embeds the current `version.py` value as four-part file and
product metadata, plus the canonical executable icon and tray PNG. Runtime
asset loading resolves through PyInstaller's extraction directory when frozen.

## Release process

`.github/workflows/release.yml` is the preferred stable signed path.
It runs only by explicit workflow dispatch from `main`. Separate jobs build
without secrets, sign in the `code-signing` environment, and publish with
`contents: write` only in the `stable-release` environment. There is no unsigned
bypass. Before publication, the workflow independently verifies Windows trust,
the exact SignPath Foundation publisher, MSI identity, and the embedded
`Chunes.exe` signature and metadata after SignPath output and after the artifact
handoff.

`.github/workflows/release-unsigned.yml` is the manual fallback when code
signing is unavailable. It requires explicit confirmation and a separate
protected environment, proves the raw EXE, embedded EXE, and MSI are unsigned,
and includes a versioned installer warning. It publishes an immutable GitHub
prerelease without changing `/releases/latest`, so the in-app updater never
offers it.

Repository release immutability is enabled and must be independently confirmed
by a maintainer before either workflow is dispatched; GitHub's scoped workflow
token cannot read that administrator-only setting. Both release workflows require an
unused release and tag, an exact tag ref at the dispatched `GITHUB_SHA`, a draft
containing only the expected MSI, and a GitHub asset digest matching the local
SHA-256. They recheck the tag immediately before publication and fail unless
the published release reports `immutable: true`. External actions are
pinned to resolved full commit SHAs, checkout credentials are not persisted,
and handoff artifacts expire after one day without compression. Published
releases are never recreated; corrections always use a new version.

The signed path requires `SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID`,
`SIGNPATH_PROJECT_SLUG`, and `SIGNPATH_SIGNING_POLICY_SLUG` as protected
`code-signing` environment secrets.

The SignPath artifact configuration and account setup checklist are under
[`.signpath/`](.signpath/README.md). The configuration deep-signs the embedded
`Chunes.exe`, repackages the MSI, and then signs the MSI itself while enforcing
the Chunes product and version metadata.

## Code signing policy

The preferred stable release path uses free code signing provided by
[SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org). If that service is unavailable or
the project is not approved, the separate unsigned manual-prerelease path keeps
releases possible without weakening automatic-update verification.

- Authors and committers: [@dubsector](https://github.com/dubsector)
- Reviewers: [@dubsector](https://github.com/dubsector)
- Approvers: [@dubsector](https://github.com/dubsector)
- Privacy policy: [PRIVACY.md](PRIVACY.md)

As the current maintainer, `dubsector` is trusted to author and commit project
changes, reviews changes from other contributors, and manually approves every
code-signing request. The Chunes project acknowledges and accepts the
[SignPath Foundation conditions for open-source code signing](https://signpath.org/terms).

Chunes transfers information only for Discord presence, optional SoundCloud or
YouTube Music album artwork, and optional GitHub update functions described in the
privacy policy. The installer and tray provide the documented opt-outs.

## License and notices

Chunes is licensed under the [Apache License 2.0](LICENSE). Third-party software,
the Bootstrap-derived note geometry, and service trademarks are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Chunes is not affiliated with,
sponsored by, or endorsed by Discord, SoundCloud, Google, YouTube, Microsoft, or
GitHub.
