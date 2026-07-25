# Microsoft Store Listing

Paste-ready copy for the Partner Center **Store listing** page of
`dubsector.dev.Chunes`. Headings match the Partner Center field names, and the
character limit for each field is noted. Packaging, versioning, and upload steps
are in [STORE_SUBMISSION.md](STORE_SUBMISSION.md).

Keep this file in step with the app. The description must never promise the
GitHub update behavior, because the packaged build takes updates from the Store
and hides the update menu items.

## Product name

Chunes

## Short name (50)

Chunes

## Short description (1,000)

Chunes turns SoundCloud, YouTube Music, and Apple Music playback in your browser
into a Discord Listening status, with the artist, album art, and a progress bar
that follows the track. It runs in the notification area, needs no account, and
sends nothing to any server of its own.

## Description (10,000)

Chunes shows what you are listening to as a Discord Listening status.

Discord's built-in music integrations do not cover SoundCloud, YouTube Music, or
Apple Music on the web, and Windows never tells Discord which tab is making
noise. Chunes fills that gap. It runs quietly in the notification area, reads the
Windows media session, and publishes the current track, artist, timing, and cover
art to the Discord desktop app on the same PC.

What Chunes does:

- Publishes SoundCloud, YouTube Music, and Apple Music web playback as a Discord
  Listening status, labeled with the service you are actually using.
- Keeps the progress bar in step with the track and clears the status as soon as
  playback stops.
- Shows album art on the activity card instead of a placeholder.
- Leaves unrelated browser audio alone, so a video clip does not turn into a fake
  music status.
- Stays out of the way. There is no window to keep open and no account to create.

The Chune ID browser extension

Windows reports browser audio as the browser itself and never says which tab is
responsible. The free Chune ID extension for Chrome tells Chunes which of your
audible tabs is a supported music tab, and lets you switch any single service
off. What it sends never leaves your PC; it goes to Chunes over the local
loopback address, and it is a hostname and a track title rather than a browsing
history. Install it from the Chrome Web Store:
https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd

Privacy

Chunes has no account, no ads, no analytics, and no telemetry, and the project
operates no server that Chunes talks to. Track information goes to the Discord
app on the same computer through Discord's local presence interface. Album art
lookups are optional, use Apple's public search API, and can be turned off in the
tray menu. The full policy is at
https://github.com/getchunes/chunes/blob/main/PRIVACY.md

Requirements

- 64-bit Windows 10 version 2004 or newer
- the Discord desktop app running on the same PC, signed in, with Share my
  activity turned on and the status set to something other than Invisible
- the Chune ID extension for browser playback

Chunes starts with Windows after installation. Turn that off from its tray menu
or from Windows Settings > Apps > Startup.

Chunes is open source under the Apache License 2.0. The source, the issue
tracker, and the privacy policy are at https://github.com/getchunes/chunes

Chunes is not affiliated with, sponsored by, or endorsed by Discord, SoundCloud,
Google, YouTube, Apple, or Microsoft.

## What's new in this version (1,500)

First Microsoft Store release of Chunes.

- Apple Music support, including web player timing, so the progress bar follows
  the track rather than guessing at it.
- A tab that reports its own track keeps the status while other browser audio
  plays. Starting a video during a song no longer clears your presence.
- Store installs update through the Store, and autostart can be managed from
  Windows Settings > Apps > Startup.

## Product features (up to 20 entries, 200 each)

- SoundCloud, YouTube Music, and Apple Music web playback as a Discord Listening
  status
- The right service label on every track, so friends see where you are listening
- A progress bar that follows the track and clears when playback stops
- Album art on the activity card, looked up only while you allow it
- Unrelated browser audio stays out of your status
- Lives in the notification area, with no window to manage and no account to
  create
- Works with the free Chune ID extension, which identifies the tab that is
  playing
- Per-service switches, so any of the three services can stay private
- No ads, analytics, or telemetry, and no service operated by the project
- Open source under the Apache License 2.0

## Search terms (up to 7 terms, 30 characters each, 21 words total)

1. discord rich presence
2. discord status
3. now playing
4. soundcloud
5. youtube music
6. apple music
7. listening activity

Fourteen words total, which leaves room if a term needs to be added later.

## Copyright and trademark info (200)

Apache License 2.0. Not affiliated with, sponsored by, or endorsed by Discord,
SoundCloud, Google, YouTube, Apple, or Microsoft.

## Additional license terms (10,000)

Chunes is licensed under the Apache License 2.0:
https://github.com/getchunes/chunes/blob/main/LICENSE

Third-party software and trademark notices:
https://github.com/getchunes/chunes/blob/main/THIRD_PARTY_NOTICES.md

## Developed by (255)

dubsector.dev

## Contact and links

| Field | Value |
| --- | --- |
| Privacy policy URL | `https://github.com/getchunes/chunes/blob/main/PRIVACY.md` |
| Website | `https://github.com/getchunes/chunes` |
| Support contact info | `https://github.com/getchunes/chunes/issues` |

The privacy policy URL is required, not optional, because Chunes makes network
requests and handles media metadata.

## Category

Primary: **Music**.

Utilities & tools is the honest alternative for a notification-area helper, but
the audience searches for music and Discord terms, and the Music category is
where they land. Nothing in the listing depends on the choice.

## Age ratings

The IARC questionnaire applies. Chunes has no user-generated content, no
purchases, no chat, and no mature material, so it rates at the lowest level in
every region. Two questions need care:

- Users can share personal information with third parties: **yes**. Track titles
  reach the local Discord client, which publishes them to the user's friends.
- The app collects or transmits location, or shares the user's info for
  advertising: **no**.

## Product declarations

| Declaration | Answer |
| --- | --- |
| Can this product function without an internet connection? | Yes. Presence uses a local interface; only album art and the Store's own updates need the network. |
| Does this product depend on a non-Microsoft driver or NT service? | No. |
| Does this product access, collect, or transmit personal information? | Yes. See the privacy policy. |
| Is this product a system utility, add-in, or requires elevation? | No. It installs and runs per user. |
| Has this product been tested for accessibility? | Leave unchecked. The tray menu has not been through an accessibility pass. |
| Does this product allow purchases? | No. |

## Notes for certification

Paste this into the **Notes for certification** field. Chunes has no main window,
so a tester who launches it and sees nothing may otherwise report a failure to
start.

> Chunes is a notification-area (system tray) application. It has no main window
> by design. After launch, its icon appears in the Windows notification area, and
> right-clicking the icon opens the menu with Start with Windows, Look up online
> album art, Open log, and Quit. The tray tooltip and the log at
> %LOCALAPPDATA%\Chunes\chunes.log confirm it is running.
>
> Its function is visible only when the Discord desktop app is running on the
> same machine. To exercise it end to end:
>
> 1. Install and sign in to the Discord desktop app, then turn on User Settings >
>    Activity Privacy > Share my activity, and set the status to Online.
> 2. Install the free Chune ID extension for Chrome from
>    https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd
> 3. Play any track on soundcloud.com in Chrome.
> 4. The Discord profile shows a "Listening to SoundCloud" activity with the
>    track title, artist, and a progress bar within a few seconds.
>
> Without the extension, Chunes still reads the Windows media session, but it
> cannot tell which browser tab is responsible, so browser playback may not be
> published.
>
> Chunes contacts no server operated by the project. It uses Discord's local
> presence interface, an optional keyless request to Apple's public iTunes Search
> API for cover art, and nothing else. Source and privacy policy:
> https://github.com/getchunes/chunes

## Assets

Listing artwork is generated in the private `getchunes/brand-assets` repository
and is never edited by hand. Package logos are a separate set and live in
`installer/msix/assets`.

| Partner Center slot | Source | Status |
| --- | --- | --- |
| Box art 2160 x 2160 | `assets/store/box-art-2160.png` | ready |
| Box art 1080 x 1080 | `assets/store/box-art-1080.png` | ready |
| Poster art 720 x 1080 | `assets/store/poster-art-720x1080.png` | ready |
| Screenshot, tray menu, 1920 x 1080 | `assets/store/screenshots/tray-menu-1920x1080.png` | ready |
| Hero art 1920 x 1080 | none | optional, spotlight placement only |

Partner Center requires one screenshot and recommends four.

Screenshots are generated, not pasted together by hand:

- `scripts/new-tray-capture.ps1` waits for the tray menu to open and saves that
  popup window alone, at native resolution, to `assets/store/captures`. Popup
  menus are their own top-level window, so no part of the desktop behind the
  menu is captured.
- `scripts/new-store-screenshot.ps1` places a capture on a card at a
  whole-number scale, so the menu text stays crisp, and composes the branded
  1920 x 1080 canvas around it.

Two things to settle before the first submission:

1. The committed capture comes from the MSI build, so it shows **Automatically
   check for updates** and **Check for updates now**. The packaged build hides
   both. Retake it from a self-signed MSIX install for a screenshot that matches
   what a Store customer gets.
2. The menu reads **Nothing playing**. Capturing it during playback shows the
   published track instead, which is a better first impression.

The obvious remaining screenshots are the Discord activity card for a track
Chunes published, and the Chune ID popup next to the tray. A screenshot must
never include a real Discord account name, avatar, friends list, or server list.
