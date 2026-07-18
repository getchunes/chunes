# Chunes

Show what you're listening to on SoundCloud or YouTube Music as a Discord
status, the same way Spotify does it.

Your friends see "Listening to Chunes" with the track name, artist, cover art
and a live progress bar. Pause the music and the status clears.

## How it works

Chunes runs quietly in your system tray. It reads the currently playing track
from Windows itself (the same info that shows in the volume popup), looks up
the cover art on SoundCloud, and hands everything to your Discord desktop app
over Discord's local Rich Presence socket. Nothing is uploaded anywhere and
there is no account, API key or sign-up. Your own Discord app is what
publishes the status.

## Install

1. Grab `Chunes.exe` from the [latest release](../../releases/latest) and run
   it. A purple note icon appears in your tray. Right click it to make Chunes
   start with Windows.
2. Install the companion browser extension,
   [Chunes Helper](https://github.com/getchunes/chunes-extension) (Chrome,
   Brave, Edge). It lets Chunes tell SoundCloud apart from a regular YouTube
   video, since Windows only reports "your browser is playing something".
   Without it Chunes still works, but YouTube videos may show up as music.
3. Make sure Discord's setting "Share my activity" is on (Settings, Activity
   Privacy) and your status is not Invisible.

Windows may warn about an unrecognized app the first time you run the exe.
That is SmartScreen being cautious about new unsigned programs. Click "More
info", then "Run anyway", or build from source below if you prefer.

## The browser extension

The extension watches which tabs are audible and reports the site name and
tab title to Chunes at `127.0.0.1:52846`. That is the only thing it does and
the only place it talks to. Chunes uses it to:

- ignore regular YouTube videos
- label the status SoundCloud or YouTube Music
- keep the status up when a video and music play at the same time

To install it manually instead of from the store: clone the
[chunes-extension](https://github.com/getchunes/chunes-extension) repo, open
`chrome://extensions`, enable Developer mode, click "Load unpacked" and pick
the cloned folder.

## Configuration (optional)

Chunes works with no configuration. To tweak it, create `config.json` next to
the exe (or script):

```json
{
  "client_id": "1527834085383213106",
  "sources": ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"],
  "service_label": "",
  "image_key": ""
}
```

- `client_id`: the Discord application whose name shows in "Listening to
  ...". Replace it with your own application ID to rebrand.
- `sources`: media from apps whose name contains one of these strings is
  shown. Add your music player's process name to include a desktop app.
- `service_label`: fallback hover text on the cover art when the service
  can't be detected.
- `image_key`: fallback image when no cover art is found. Either an image URL
  or an asset name from your own Discord application.

## Running from source

```
pip install pypresence pystray pillow winrt-runtime winrt-Windows.Media ^
    winrt-Windows.Media.Control winrt-Windows.Foundation ^
    winrt-Windows.Foundation.Collections
python chunes.py        (tray app)
python presence.py      (console mode, useful for debugging)
```

Build the exe with:

```
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name Chunes --collect-submodules winrt chunes.py
```

## Notes and limitations

- Windows only. The track info comes from the Windows media session API.
- Discord's desktop app must be running on the same PC.
- Cover art comes from a SoundCloud search, so an obscure rip or a renamed
  upload can occasionally show the wrong art or none.
- Tracks playing in a browser are matched to tabs by title. Two tabs playing
  identically titled media at once can confuse the service detection.
