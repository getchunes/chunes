"""Show the currently playing track from Windows media sessions as a Discord
"Listening to" status, similar to the built-in Spotify integration.

Reads track metadata from the Windows media transport controls (the same data
shown in the volume overlay popup), so it works with anything: SoundCloud or
YouTube Music in a browser, desktop apps, etc.
"""

import asyncio
import json
import re
import struct
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from pypresence import AioPresence, ActivityType
import pypresence.baseclient as _base
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

import protocol
import settings

# Track titles can contain emoji etc. that the default cp1252 console
# encoding can't represent.
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONFIG_PATH = (
    Path(sys.executable).parent
    if getattr(sys, "frozen", False)
    else Path(__file__).parent
) / "config.json"
POLL_SECONDS = 5
RPC_FRAME = 1
RPC_CLOSE = 2
RPC_PING = 3
RPC_PONG = 4
MAX_RPC_FRAME_BYTES = 1024 * 1024
# Discord allows two presence buttons, each with a label of at most 32
# characters and an http(s) URL, and shows them to everyone but the listener.
MAX_BUTTON_LABEL_CHARS = 32
GET_CHUNES_LABEL = "Get Chunes"
EXTENSION_LISTING_URL = (
    "https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd"
)

DEFAULT_CONFIG = {
    "client_id": "1527834085383213106",
    "sources": ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"],
    "service_label": "",
    "image_key": "",
}

# Live state for the tray app.
status = {"track": None, "host": None, "extension_enabled": None}
_status_lock = threading.Lock()


def set_status(**values):
    with _status_lock:
        status.update(values)


def status_snapshot():
    with _status_lock:
        return dict(status)


async def _read_output(self):
    """Read the next Discord command response while servicing IPC control frames."""
    while True:
        try:
            preamble = await asyncio.wait_for(
                self.sock_reader.readexactly(8), self.response_timeout
            )
            status_code, length = struct.unpack("<II", preamble[:8])
            if length > MAX_RPC_FRAME_BYTES:
                raise _base.PipeClosed
            data = await asyncio.wait_for(
                self.sock_reader.readexactly(length), self.response_timeout
            )
        except asyncio.TimeoutError:
            raise _base.ResponseTimeout
        except (
            ConnectionError,
            OSError,
            asyncio.IncompleteReadError,
            struct.error,
        ):
            raise _base.PipeClosed

        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _base.PipeClosed from exc

        if status_code == RPC_CLOSE:
            raise _base.PipeClosed
        if status_code == RPC_PING:
            self.send_data(RPC_PONG, payload)
            continue
        if status_code == RPC_PONG:
            continue
        if status_code != RPC_FRAME or not isinstance(payload, dict):
            raise _base.PipeClosed

        if payload.get("evt") == "ERROR":
            data = payload.get("data")
            message = data.get("message") if isinstance(data, dict) else None
            if not isinstance(message, str) or not message:
                message = "Discord RPC error"
            raise _base.ServerError(message)
        # Command responses are identified by their nonce; evt is optional and
        # commonly absent or null. Nonce-less DISPATCH events are unrelated to
        # the outstanding SET_ACTIVITY request and must not satisfy it.
        if isinstance(payload.get("cmd"), str) and isinstance(
            payload.get("nonce"), str
        ):
            return payload
        print(f"Ignoring Discord event while awaiting a response: {payload}")


_base.BaseClient.read_output = _read_output


TAB_REPORT_PORT = 52846
# Latest audible-tab report from the browser extension: which sites are
# actually making sound. Windows only tells us "Brave", so without this we
# can't tell SoundCloud from a regular YouTube video.
_tab_state = {
    "enabled": False,
    "services": {"soundcloud": False, "youtubeMusic": False},
    "tabs": [],
}
_tab_reported_at = 0.0


def _fresh_tab_report():
    if protocol.report_is_fresh(_tab_reported_at):
        set_status(extension_enabled=_tab_state["enabled"])
        return _tab_state
    set_status(extension_enabled=None)
    return None


def _http_reply(status, body=b"", protocol_version=protocol.PROTOCOL_VERSION):
    # The extension rejects any response that does not carry its own version,
    # so a report is answered in the version it arrived in.
    reasons = {
        200: "OK",
        204: "No Content",
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        411: "Length Required",
        413: "Content Too Large",
        415: "Unsupported Media Type",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
    }
    headers = [
        f"HTTP/1.1 {status} {reasons[status]}",
        "Connection: close",
        "Cache-Control: no-store",
        "X-Content-Type-Options: nosniff",
        f"Content-Length: {len(body)}",
    ]
    if 200 <= status < 300:
        headers.append(f"X-Chunes-Protocol: {protocol_version}")
    if body:
        headers.append("Content-Type: application/json")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


async def _handle_tab_report(reader, writer):
    global _tab_reported_at
    reply = _http_reply(500)
    try:
        raw_head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        request = protocol.parse_request_head(raw_head[:-4])
        if request.action == "state":
            fresh = protocol.report_is_fresh(_tab_reported_at)
            state = protocol.safe_public_state(_tab_state, fresh)
            body = json.dumps(state, separators=(",", ":")).encode()
            reply = _http_reply(200, body)
        else:
            body = await asyncio.wait_for(
                reader.readexactly(request.content_length), 5
            )
            report, version = protocol.parse_report_body(body, include_version=True)
            hosts = sorted({tab["host"] for tab in report["tabs"]})
            old_hosts = sorted({tab["host"] for tab in _tab_state["tabs"]})
            if (
                not _tab_reported_at
                or hosts != old_hosts
                or report["enabled"] != _tab_state["enabled"]
            ):
                print(
                    f"Extension report: enabled={report['enabled']}, "
                    f"audible hosts={hosts}"
                )
            _tab_state.clear()
            _tab_state.update(report)
            _tab_reported_at = time.time()
            with _status_lock:
                current_track = status.get("track")
                current_host = status.get("host")
            res_body = json.dumps(
                {"status": "ok", "track": current_track, "host": current_host},
                separators=(",", ":"),
            ).encode()
            reply = _http_reply(200, res_body, version)
    except protocol.ProtocolError as exc:
        reply = _http_reply(exc.status)
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        asyncio.TimeoutError,
    ):
        reply = _http_reply(400)
    except Exception as exc:
        print(f"Extension request failed: {type(exc).__name__}: {exc}")
    try:
        writer.write(reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


def fallback_track(report):
    """When Windows' media session is unusable (e.g. a blocked YouTube video
    holds the browser's only media slot), build track info from the audible
    music tab's title. No playback position is available this way."""
    for tab in protocol.enabled_tabs(report):
        host = tab["host"]
        t = tab["title"].strip()
        service = protocol.service_for_host(host)
        if service == "appleMusic":
            # The page title is never the track, so only its own metadata can
            # name what Apple Music is playing.
            metadata = tab.get("metadata")
            if metadata and tab_reports_playing(tab):
                return metadata["title"], metadata["artist"], host, tab["mediaId"]
            continue
        if service == "soundcloud":
            metadata = tab.get("metadata")
            if metadata:
                return metadata["title"], metadata["artist"], host, tab["mediaId"]
            if " by " in t:
                title, artist = t.rsplit(" by ", 1)
                return title.strip(), artist.strip(), host, tab["mediaId"]
            continue
        if service == "youtubeMusic":
            t = re.sub(r"\s*[\|-]\s*YouTube Music$", "", t, flags=re.IGNORECASE).strip()
            if normalize_title(t) in ("", "youtube music", "youtube"):
                continue
            if " - " in t:
                title, artist = t.rsplit(" - ", 1)
                return title.strip(), artist.strip(), host, tab["mediaId"]
            return t, "", host, tab["mediaId"]
    return None


def fallback_timing(fb, tab, seen, now):
    """Build a media-session fallback track, or None when it would stall.

    `fb` is `(title, artist, host, media_id)` from `fallback_track()`, and
    `tab` its reported tab when one names this track. A fallback is used when a
    non-music tab (e.g. a regular YouTube video) has taken over the browser's
    single OS media session while a music tab is still audible. A page that
    measures its own playback supplies the position directly; otherwise it is
    published only when this track's real position was captured (recorded in
    `seen`) before the takeover and is still within range, since playback
    advances 1:1 with real time. Without either there is only a frozen 0:00, so
    return None and publish nothing. Returns `(title, artist, pos, dur,
    source)`.
    """
    title, artist, host, _media_id = fb
    if protocol.service_for_host(host) == "appleMusic":
        ext_timing = apple_extension_timing(tab, now)
        if ext_timing is not None:
            return (title, artist, ext_timing[0], ext_timing[1], f"tab:{host}")
    anchor = seen.get((title, artist))
    if not anchor:
        return None
    a_start, a_dur = anchor
    elapsed = now - a_start
    if 0 < elapsed < a_dur + 30:
        return (title, artist, elapsed, a_dur, f"tab:{host}")
    return None


def provider_duration_start(title, artist, position, duration, seen, now):
    """Keep a provider track anchored across stale backward media positions."""
    previous = seen.get((title, artist))
    if previous:
        previous_start, _previous_duration = previous
        elapsed = now - previous_start
        if elapsed < duration + 30:
            # Chromium periodically publishes a low, stale position for a
            # background provider tab. Keeping the known anchor prevents the
            # familiar 20s -> 5s Discord loop without affecting a new title.
            if position <= 0 or position < elapsed - 5:
                return previous_start
    if 0 < position < duration:
        return int(now - position)
    return int(now)


def normalize_title(t):
    if not t:
        return ""
    return (
        t.lower()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .strip()
    )


def _titles_match(query, candidate):
    """True when two track titles refer to the same song.

    Equal after normalization, or the shorter is contained in the longer and
    covers most of it. The coverage floor stops a short unrelated title from
    substring-matching a longer one (e.g. "Gimme Dat" inside "Gimme Dat Ting"),
    which would otherwise pull in the wrong track's duration."""
    a = normalize_title(query)
    b = normalize_title(candidate)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return shorter in longer and len(shorter) >= 0.8 * len(longer)


def classify_tab(title, report):
    """Match a playing title to its reported audible browser tab."""
    if not report or not report["enabled"]:
        return None
    tl = normalize_title(title)
    if not tl or tl in ("youtube music", "soundcloud", "apple music", "youtube"):
        return None
    for tab in protocol.enabled_tabs(report):
        candidates = [tab["title"]]
        metadata = tab.get("metadata")
        if metadata:
            # The page names the track it is playing. Apple Music's tab title
            # never does, so this is the only title that can match it.
            candidates.append(metadata["title"])
        for candidate in candidates:
            cand = normalize_title(candidate)
            if cand and (cand in tl or tl in cand):
                return tab
    return None


def tab_reports_playing(tab):
    """True unless the tab's own playback sample says it is paused."""
    return tab.get("playing", True) is True


def identified_tab(report):
    """The single audible tab that names the track it is playing.

    A page that reports its own Media Session metadata identifies itself
    without help from the OS media session, so it stays attributable even
    while another tab is making noise. More than one such tab is ambiguous
    again, since the OS session's single title cannot pick between them.
    """
    identified = [
        tab
        for tab in protocol.enabled_tabs(report)
        if tab.get("metadata") and tab_reports_playing(tab)
    ]
    return identified[0] if len(identified) == 1 else None


def classify_host(title, report):
    """Return the reported host for a playing title, if one matches."""
    tab = classify_tab(title, report)
    return tab["host"] if tab else None


def resolve_tab(title, source, report):
    """Resolve the audible browser tab a media-session title belongs to.

    A title that matches an enabled music tab is taken directly. An unmatched
    browser title (the Apple Music web player keeps a generic page title, so
    its real track never matches) belongs to the sole audible music tab when
    that tab reports the track it is playing, or when nothing unpublishable is
    also audible. Otherwise a blocked video or a disabled service could be the
    one the media session is describing, so the title is left unattributed
    rather than published under the wrong service. Returns the resolved tab, or
    None when it can't be safely attributed.
    """
    tab = classify_tab(title, report)
    if tab is not None or not protocol.is_browser_source(source):
        return tab
    tab = identified_tab(report)
    if tab is not None:
        return tab
    if protocol.has_unpublishable_audible_tab(report):
        return None
    enabled = protocol.enabled_tabs(report)
    if len(enabled) == 1:
        return enabled[0]
    return None


def page_reported_track(tab):
    """Current track as the playing page itself reports it, when supplied.

    The page sample is the player's own state, so it owns track identity: it
    must replace stale Windows metadata at a track transition, and it is the
    only description of the track when the OS media session is busy describing
    a different tab.
    """
    metadata = tab.get("metadata") if tab else None
    if not metadata:
        return None
    return metadata["title"], metadata["artist"]


_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    )
}
_artwork_cache = {}


def _http_get(url, headers=None):
    request_headers = dict(_UA)
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _apple_music_track_url(value):
    """The searched track's own Apple Music page, when the API returned one.

    Only the track selector is kept. The Search API appends its own `uo`
    marker, and this link goes on a public profile rather than into an
    analytics pipeline.
    """
    if not isinstance(value, str) or not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname != "music.apple.com":
        return None
    track = urllib.parse.parse_qs(parsed.query).get("i", [None])[0]
    query = urllib.parse.urlencode({"i": track}) if track else ""
    return urllib.parse.urlunsplit(("https", hostname, parsed.path, query, ""))


def _find_apple_music_info(title, artist):
    """Best-effort Apple Music artwork, duration and track page from the public iTunes Search API."""
    art = None
    dur = 0.0
    url = None
    try:
        q = urllib.parse.quote(f"{title} {artist}".strip())
        data = json.loads(_http_get(
            "https://itunes.apple.com/search"
            f"?term={q}&media=music&entity=song&limit=5"
        ))
        matched_art = None
        matched_dur = 0.0
        matched_url = None
        fallback_art = None
        for t in data.get("results", []):
            cand_art = t.get("artworkUrl100")
            cand_dur = (t.get("trackTimeMillis") or 0) / 1000.0
            if _titles_match(title, t.get("trackName") or ""):
                if isinstance(cand_art, str) and cand_art:
                    matched_art = cand_art
                matched_dur = cand_dur
                matched_url = _apple_music_track_url(t.get("trackViewUrl"))
                break
            if fallback_art is None and isinstance(cand_art, str) and cand_art:
                fallback_art = cand_art
        best_art = matched_art or fallback_art
        if best_art:
            art = best_art.replace("100x100", "500x500")
        # Duration and track page only from a title-matched result; a fallback
        # thumbnail is low harm, a wrong duration paints a wrong progress bar
        # and a wrong link sends listeners to the wrong song.
        dur = matched_dur
        url = matched_url
    except Exception as e:
        print(f"Apple Music artwork lookup failed: {type(e).__name__}: {e}")
    return art, dur, url


def _find_apple_music_artwork(title, artist):
    return _find_apple_music_info(title, artist)[0]


def find_artwork_and_info(
    title, artist, host=None, media_id=None, source=None, metadata=None
):
    """Return (art_url, duration_s, track_url) from page metadata or Apple Search."""
    key = (host, media_id, source, title, artist, str(metadata))
    if key in _artwork_cache:
        return _artwork_cache[key]

    service = protocol.service_for_host(host)
    art = None
    dur = 0.0
    url = None

    if metadata:
        art = metadata["artwork"]
    elif service == "appleMusic":
        art, dur, url = _find_apple_music_info(title, artist)
    elif not protocol.is_browser_source(source):
        art, dur, url = _find_apple_music_info(title, artist)

    res = (art, dur, url)
    _artwork_cache[key] = res
    if len(_artwork_cache) > 500:
        _artwork_cache.pop(next(iter(_artwork_cache)))
    return res


def find_artwork(title, artist, host=None, media_id=None, source=None, metadata=None):
    """Return source-specific online album artwork for the current track."""
    art, _, _ = find_artwork_and_info(title, artist, host, media_id, source, metadata)
    return art


# Where a service looks a track up when nothing names it exactly. A search
# lands the listener on the right song without pretending to be a permalink.
TRACK_SEARCH_URLS = {
    "appleMusic": "https://music.apple.com/search?term={query}",
    "soundcloud": "https://soundcloud.com/search?q={query}",
    "youtubeMusic": "https://music.youtube.com/search?q={query}",
}


def track_link(host, media_id, title, artist, tab_url=None, itunes_url=None):
    """Best available URL for opening the current track on its own service.

    The playing tab knows its own address, so it wins whenever the extension is
    new enough to report one. Failing that a YouTube Music video ID and an
    iTunes title match still name a specific track, and everything else falls
    back to that service's search.
    """
    service = protocol.service_for_host(host)
    if service is None:
        return None
    if tab_url:
        return tab_url
    if service == "youtubeMusic" and media_id:
        return f"https://music.youtube.com/watch?v={urllib.parse.quote(media_id)}"
    if service == "appleMusic" and itunes_url:
        return itunes_url
    query = urllib.parse.quote(f"{title} {artist}".strip())
    if not query:
        return None
    return TRACK_SEARCH_URLS[service].format(query=query)


def presence_buttons(
    host,
    media_id,
    title,
    artist,
    label,
    tab_url=None,
    itunes_url=None,
    show_track=True,
    show_get_chunes=False,
):
    """The presence buttons for the current track, in display order.

    Discord shows these to everyone except the listener, so the track button
    is only offered when a link actually resolves; a button that opens the
    wrong thing is worse than no button at all.
    """
    buttons = []
    if show_track and label:
        link = track_link(host, media_id, title, artist, tab_url, itunes_url)
        if link:
            buttons.append({
                "label": f"Play on {label}"[:MAX_BUTTON_LABEL_CHARS],
                "url": link,
            })
    if show_get_chunes:
        buttons.append({"label": GET_CHUNES_LABEL, "url": EXTENSION_LISTING_URL})
    return buttons


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


async def get_playing_track(allowed_sources):
    """Return (title, artist, position_s, duration_s, source) or None."""
    mgr = await SessionManager.request_async()
    for session in mgr.get_sessions():
        source = session.source_app_user_model_id or ""
        if allowed_sources and not any(
            a.lower() in source.lower() for a in allowed_sources
        ):
            continue
        info = session.get_playback_info()
        if info.playback_status != PlaybackStatus.PLAYING:
            continue
        props = await session.try_get_media_properties_async()
        if not props.title:
            continue
        tl = session.get_timeline_properties()
        # position is a snapshot taken at last_updated_time, not "now";
        # browsers refresh it infrequently, so extrapolate forward.
        pos = tl.position.total_seconds()
        dur = tl.end_time.total_seconds()
        try:
            elapsed = time.time() - tl.last_updated_time.timestamp()
            # Only extrapolate forward if last_updated_time is recent (< 15s)
            # or if position is already past the initial track start window (> 15s).
            # Inactive background tabs (e.g. Apple Music) update media properties
            # and reset position to 0 on track change, but leave last_updated_time
            # pointing to when the previous track started.
            if 0 < elapsed < 15:
                pos += elapsed
            elif 0 < elapsed < 3600 and pos > 15:
                pos += elapsed
        except (OSError, OverflowError, ValueError):
            pass
        if dur > 0 and pos >= dur:
            pos = 0.0
        return (
            props.title,
            props.artist or "",
            pos,
            dur,
            source,
        )
    return None


# A newly seen Apple Music track is treated as a gapless continuation of the
# previous one (back-dating its start to when that track was due to end) only
# when the prediction lands within this many seconds of now. A larger gap means
# a skip, a pause between tracks, or the first song of a session, which fall
# back to a fresh anchor.
APPLE_GAPLESS_MARGIN_SECONDS = 12


def apple_track_start(track_key, now, anchors, prev):
    """Discord start epoch for an Apple Music track.

    Apple's web player drives the Windows media session with a queue-wide
    position counter that does not reset on a track change, so its reported
    position is not a trustworthy offset into the current song and can't be
    back-dated the way SoundCloud/YTM positions are.

    A track already being tracked keeps its anchor. A newly seen track is
    back-dated to when the previous Apple track was due to end: Apple plays
    gapless and each track's length is known (the locked iTunes duration), so
    this recovers the real offset the counter can't give us and keeps the bar
    close to live. With no previous track, or a prediction outside a small
    window around now (a manual skip, a gap, or the first song), it falls back
    to a fresh anchor at now."""
    start = anchors.get(track_key)
    if start is None:
        start = int(now)
        if prev is not None:
            prev_start, prev_dur = prev
            predicted = prev_start + prev_dur
            if prev_dur > 0 and 0 <= now - predicted <= APPLE_GAPLESS_MARGIN_SECONDS:
                start = int(predicted)
        anchors[track_key] = start
        if len(anchors) > 100:
            anchors.pop(next(iter(anchors)))
    return start


def apple_locked_duration(track_key, gsmtc_dur, info_dur, locks):
    """First trusted duration for an Apple Music track, held against changes.

    Apple's self-reported media-session duration reads 0 for the first several
    seconds and can flip mid-song, so prefer the iTunes Search value and use the
    media session only when the lookup has none. Once a positive value is
    recorded it is locked so the progress bar does not jump."""
    dur = locks.get(track_key, 0.0)
    if dur <= 0:
        dur = info_dur if info_dur > 0 else gsmtc_dur
        if dur > 0:
            locks[track_key] = dur
            if len(locks) > 100:
                locks.pop(next(iter(locks)))
    return dur


# A MusicKit sample older than this is treated as gone (tab closed, extension
# reloaded, or the page wedged) and the GSMTC anchor workaround takes over.
# The extension only pushes a report when playback changes meaningfully, so a
# steadily playing sample is normally up to a full report period (~30s) old
# and extrapolates accurately; a couple of missed periods means trouble.
# Small negative ages are tolerated: the sample is taken on the browser's
# clock a report cycle before we compare it against ours.
APPLE_EXTENSION_TIMING_MAX_AGE_SECONDS = 75


def apple_extension_timing(tab, now):
    """(position, duration) measured in-page by the extension, or None.

    The extension reads the Apple Music web player's own MusicKit state, the
    only source that reports real per-track position and duration (the OS
    media session runs a queue-wide counter and misreports duration). A
    playing sample is extrapolated to now; a paused one is used as-is. A
    missing or stale sample returns None so the caller can fall back to the
    GSMTC anchor workaround."""
    if not tab or "position" not in tab:
        return None
    age = now - tab["sampledAt"] / 1000.0
    if not -5 <= age <= APPLE_EXTENSION_TIMING_MAX_AGE_SECONDS:
        return None
    position = tab["position"] + (max(age, 0.0) if tab["playing"] else 0.0)
    duration = tab["duration"] or 0.0
    return position, duration


async def main():
    cfg = load_config()
    client_id = cfg["client_id"]
    allowed = cfg.get("sources", [])
    service = cfg.get("service_label", "")
    image_key = cfg.get("image_key", "")

    rpc = AioPresence(client_id)
    await rpc.connect()
    tab_server = await asyncio.start_server(
        _handle_tab_report,
        "127.0.0.1",
        TAB_REPORT_PORT,
        limit=protocol.MAX_HEADER_BYTES + 4,
    )
    print("Connected to Discord. Watching for music...")

    last = None
    seen = {}  # (title, artist) -> (start_epoch, duration) from real readings
    apple_starts = {}  # (title, artist) -> anchored start epoch (Apple Music)
    apple_durs = {}  # (title, artist) -> locked duration (Apple Music)
    last_apple_key = None  # previous Apple track, for gapless start prediction

    async def send(coro_factory):
        # Discord's RPC responses occasionally trip up pypresence (missing
        # "evt" key) or the pipe drops when Discord restarts; reconnect and
        # let the next poll retry rather than crashing.
        nonlocal rpc, last
        try:
            await coro_factory(rpc)
            return True
        except Exception as e:
            print(f"RPC hiccup ({type(e).__name__}: {e}), reconnecting...")
            try:
                rpc.close()
            except Exception:
                pass
            rpc = AioPresence(client_id)
            for _ in range(60):
                try:
                    await rpc.connect()
                    # Discord forgot the activity; force a re-send next poll.
                    last = None
                    print("Reconnected to Discord.")
                    return False
                except Exception:
                    await asyncio.sleep(10)
            print("Could not reconnect to Discord after 10 minutes, exiting.")
            sys.exit(1)

    while True:
        report = _fresh_tab_report()
        try:
            track = await get_playing_track(allowed)
        except OSError:
            track = None

        host = None
        media_id = None
        tab = None
        page_metadata = None
        if track:
            title, artist, pos, dur, source = track
            generic_title = normalize_title(title) in (
                "youtube music", "soundcloud", "apple music", "youtube"
            )
            tab = resolve_tab(title, source, report)
            if tab:
                host = tab["host"]
                media_id = tab["mediaId"]
                page_metadata = tab.get("metadata")
                page_track = page_reported_track(tab)
                if page_track:
                    title, artist = page_track
                    generic_title = False
            if generic_title:
                track = None
            if host is None and last is not None and last[0][0] == title and last[0][1] == artist:
                host = last[0][2]
                media_id = last[0][3]
            if track and not protocol.browser_track_is_allowed(source, report, host):
                if last is not None:
                    print(
                        "Ignoring disabled or non-music browser source: "
                        f"{title[:60]}"
                    )
                track = None
        if not track:
            # A blocked video may be hogging the browser's only media
            # session; the extension still knows if a music tab is audible.
            fb = fallback_track(report)
            if fb:
                title, artist, host, media_id = fb
                # Whatever the media session pointed at, the fallback track
                # comes from the reporting tab found below or from nothing.
                tab = None
                for reported_tab in protocol.enabled_tabs(report):
                    if (
                        reported_tab["host"] == host
                        and reported_tab.get("metadata", {}).get("title") == title
                        and reported_tab.get("metadata", {}).get("artist") == artist
                    ):
                        tab = reported_tab
                        page_metadata = reported_tab["metadata"]
                        break
                track = fallback_timing(fb, tab, seen, time.time())
                if track:
                    title, artist, pos, dur, source = track

        if track:
            now = time.time()
            use_artwork = settings.artwork_enabled()
            show_track_button = settings.track_button_enabled()
            show_get_chunes_button = settings.get_chunes_button_enabled()
            tab_url = tab.get(protocol.TRACK_URL_KEY) if tab else None
            is_apple = protocol.service_for_host(host) == "appleMusic"
            ext_timing = None
            # A fallback track exists only when a real anchor was recovered,
            # so it carries a genuine elapsed position; anchor its start to
            # wall clock the same way a normal browser track is anchored.
            if source.startswith("tab:"):
                start = int(now - pos)
            elif is_apple:
                ext_timing = apple_extension_timing(tab, now)
                if ext_timing is not None:
                    # The extension read the real position from the page's
                    # MusicKit player; anchor directly to it. Keeping the
                    # anchor and lock dicts current means a lost sample later
                    # hands the GSMTC workaround an accurate starting point.
                    start = int(now - ext_timing[0])
                    apple_starts[(title, artist)] = start
                    if len(apple_starts) > 100:
                        apple_starts.pop(next(iter(apple_starts)))
                    if ext_timing[1] > 0:
                        apple_durs[(title, artist)] = ext_timing[1]
                        if len(apple_durs) > 100:
                            apple_durs.pop(next(iter(apple_durs)))
                else:
                    # Apple's queue-wide position counter is not a reliable
                    # offset into the current song; anchor to first-seen wall
                    # clock, back-dated to the previous track's end on a
                    # gapless change.
                    prev = None
                    if last_apple_key is not None and last_apple_key != (title, artist):
                        prev_start = apple_starts.get(last_apple_key)
                        if prev_start is not None:
                            prev = (prev_start, apple_durs.get(last_apple_key, 0.0))
                    start = apple_track_start((title, artist), now, apple_starts, prev)
            else:
                start = (
                    provider_duration_start(title, artist, pos, dur, seen, now)
                    if dur > 0
                    else int(now - pos)
                )
            last_apple_key = (title, artist) if is_apple else None
            if start > 0:
                seen[(title, artist)] = (start, dur)
                if len(seen) > 100:
                    seen.pop(next(iter(seen)))
            # Re-send only on track change or a seek (start timestamp moved
            # by more than a few seconds); Discord drops clients that spam
            # SET_ACTIVITY every poll.
            # The iTunes link is only resolved further down, but it follows
            # from the title and artist already keyed here; the tab's own
            # address is the one link input that can change on its own.
            key = (
                title,
                artist,
                host,
                media_id,
                use_artwork,
                dur > 0,
                show_track_button,
                show_get_chunes_button,
                tab_url,
            )
            # Re-send periodically even if unchanged: Discord forgets the
            # activity if the client reloads, and we only notice the dead
            # pipe when we next write to it.
            unchanged = (
                last is not None
                and last[0] == key
                and abs(start - last[1]) <= 4
                and now - last[2] < 60
            )
            if not unchanged:
                if last is None or last[0][:2] != key[:2]:
                    print(f"Now playing: {title} - {artist} ({source})")
                    set_status(
                        track=f"{title} - {artist}" if artist else title,
                        host=host,
                    )
                art = None
                info_dur = 0.0
                itunes_url = None
                if use_artwork:
                    art, info_dur, itunes_url = await asyncio.to_thread(
                        find_artwork_and_info,
                        title,
                        artist,
                        host,
                        media_id,
                        source,
                        page_metadata,
                    )
                if is_apple:
                    if ext_timing is not None and ext_timing[1] > 0:
                        # MusicKit reports the track's real duration directly.
                        dur = ext_timing[1]
                    else:
                        # GSMTC's Apple duration is late and flips mid-song;
                        # lock the first trusted value (iTunes-preferred)
                        # without moving the anchored start.
                        dur = apple_locked_duration(
                            (title, artist), dur, info_dur, apple_durs
                        )
                elif dur <= 0 and info_dur > 0:
                    dur = info_dur
                    # A background browser tab can expose a real track but
                    # only a zero position. Keep its first provider-backed
                    # anchor instead of resetting Discord to 0:00 each poll.
                    start = provider_duration_start(
                        title, artist, pos, dur, seen, now
                    )
                kwargs = dict(
                    activity_type=ActivityType.LISTENING,
                    details=title[:128],
                    state=(f"by {artist}"[:128] if artist else None),
                )
                if start > 0:
                    kwargs["start"] = start
                    if dur > 0:
                        kwargs["end"] = int(start + dur)
                if art or image_key:
                    kwargs["large_image"] = art or image_key
                label = protocol.service_label_for_host(host, service)
                if label:
                    kwargs["large_text"] = label
                buttons = presence_buttons(
                    host,
                    media_id,
                    title,
                    artist,
                    label,
                    tab_url,
                    itunes_url,
                    show_track_button,
                    show_get_chunes_button,
                )
                if buttons:
                    kwargs["buttons"] = buttons
                if await send(lambda r: r.update(**kwargs)):
                    last = (key, start, now)
        else:
            # No track playing means no gapless continuation to anchor from.
            last_apple_key = None
            if last is not None:
                print("Playback stopped, clearing status.")
                last = None
                set_status(track=None, host=None)
                await send(lambda r: r.clear())

        await asyncio.sleep(POLL_SECONDS)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
