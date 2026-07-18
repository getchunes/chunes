# Privacy Policy

Effective: July 18, 2026

Chunes is a local Windows application. It has no Chunes account, advertising,
analytics, crash-reporting service, or telemetry endpoint. Chunes does process
music metadata and makes the network requests described below so it can provide
Discord presence, optional artwork, and optional updates.

## Data processed locally

Chunes reads the following data from Windows Global System Media Transport
Controls:

- track title and artist
- playback state, position, and duration
- the source application's identifier

The Chune ID browser extension sends Chunes an `application/json` report over
the loopback interface at `127.0.0.1:52846`. That report contains the
extension's master enabled state, the SoundCloud and YouTube Music enabled
states, and the host and title of reported audible tabs. A YouTube Music report
can also contain the watch page's 11-character public video ID for exact album
art lookup. It does not contain general browsing history, page body contents,
cookies, account credentials, or full URLs. Loopback reports are held in memory
and expire after 90 seconds.

Chunes stores these items on the PC:

- `%LOCALAPPDATA%\Chunes\chunes.log` and one rotated `.old` log, which can
  contain track titles, artists, source application identifiers, reported
  audible hosts, and operational errors
- downloaded update installers under `%LOCALAPPDATA%\Chunes\Updates`
- optional `config.json` next to the executable
- checkable settings under `HKEY_CURRENT_USER\Software\Chunes`
- a `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` entry
  only if the user selects **Start with Windows**

Current track state, browser report state, artwork results, and artwork cache
entries are otherwise held only in memory for the life of the process. Chunes
does not operate a server reachable from other computers; the extension
listener binds only to the local loopback address.

## Discord

To perform its primary function, Chunes sends the current track title, artist,
playback timing, detected service label, and an optional artwork URL to the
locally installed Discord desktop client through Discord's local Rich Presence
IPC interface. Discord decides how that activity is processed and displayed.
Discord's privacy policy applies:

https://discord.com/privacy

To stop this transfer for supported browser services, turn off the Chune ID
master switch or the relevant service switch. To stop all Discord presence,
quit Chunes from its tray menu and turn off **Start with Windows** if enabled.

## Online album artwork

When **Look up online album art** is checked, Chunes uses the identified service
to find artwork:

- For SoundCloud, or when a non-browser track has no identified service, Chunes
  contacts SoundCloud's public website, public web application scripts, and
  public track search API. The search includes the current track title and
  artist.
- For YouTube Music, Chunes sends the public video ID to YouTube Music's public
  web metadata endpoint. It accepts only an exact matching track and square
  Google-hosted music artwork; it does not use a generic YouTube video
  thumbnail or fall back to SoundCloud.

These requests necessarily expose the user's IP address and a generic desktop
browser user-agent to the selected service and its infrastructure. YouTube
Music may also issue a transient visitor value used for its public web request;
Chunes does not send account cookies or credentials. When a result is found,
Chunes gives Discord the resulting provider-hosted artwork URL; Discord may then
retrieve that image.

SoundCloud's privacy policy applies:

https://soundcloud.com/pages/privacy

Google's privacy policy applies to YouTube Music:

https://policies.google.com/privacy

Clear **Look up online album art** in the tray menu to stop all artwork lookup
requests. The choice is stored locally and takes effect for the current track
on the next polling cycle. The installer shows a separate artwork checkbox that
defaults to on, and preserves an existing opt-out during upgrades.

## GitHub updates

When **Automatically check for updates** is checked, Chunes contacts the public
GitHub Releases API shortly after startup. A manual **Check for updates now**
does the same. The request contains the installed Chunes version in its
user-agent and necessarily exposes the user's IP address to GitHub. If the user
accepts an offered update, Chunes downloads the exact MSI release asset from
GitHub's release infrastructure.

GitHub's privacy statement applies:

https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement

Automatic checks are optional. The installer shows a checkbox that defaults to
on, and the choice is preserved during upgrades. Clear **Automatically check
for updates** in the tray menu to stop startup checks. Chunes does not contact
GitHub for updates when this option is off unless the user invokes the manual
command. If the setting is turned off during the startup delay, Chunes checks it
again and cancels the pending request before contacting GitHub.

## No Chunes service

The Chunes project does not receive the media metadata, extension reports,
settings, logs, SoundCloud searches, YouTube Music artwork requests, or Discord
presence described above.
Chunes does not sell or share personal data with a Chunes-operated service
because no such service is used by the application.

## Removing local data

Uninstall Chunes from Windows **Installed apps** to remove the application and
shortcut. The user may also delete `%LOCALAPPDATA%\Chunes`, remove
`HKEY_CURRENT_USER\Software\Chunes`, and remove the Chunes value from the
current user's Windows `Run` key to erase remaining logs, downloaded updates,
settings, and autostart configuration. A user-created `config.json` may remain
in the installation directory if it prevented that directory from becoming
empty.

## Changes and questions

Material privacy changes will be recorded in this repository. Questions can be
opened at https://github.com/getchunes/chunes/issues without including private
media history, logs, credentials, or other sensitive information.
