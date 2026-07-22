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

DEFAULT_CONFIG = {
    "client_id": "1527834085383213106",
    "sources": ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"],
    "service_label": "",
    "image_key": "",
}

# Live state for the tray app.
status = {
    "track": None,
    "host": None,
    "extension_enabled": None,
    "extension_protocol": None,
}
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
_tab_protocol_version = protocol.LEGACY_PROTOCOL_VERSION


def _fresh_tab_report():
    if protocol.report_is_fresh(_tab_reported_at):
        set_status(
            extension_enabled=_tab_state["enabled"],
            extension_protocol=_tab_protocol_version,
        )
        return _tab_state
    set_status(extension_enabled=None, extension_protocol=None)
    return None


def _http_reply(status, body=b"", protocol_version=None):
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
        headers.append(f"X-Chunes-Protocol: {protocol_version or protocol.PROTOCOL_VERSION}")
    if body:
        headers.append("Content-Type: application/json")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


async def _handle_tab_report(reader, writer):
    global _tab_reported_at, _tab_protocol_version
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
            report, report_version = protocol.parse_report_body(body, include_version=True)
            hosts = sorted({tab["host"] for tab in report["tabs"]})
            old_hosts = sorted({tab["host"] for tab in _tab_state["tabs"]})
            if (
                not _tab_reported_at
                or hosts != old_hosts
                or report["enabled"] != _tab_state["enabled"]
                or report_version != _tab_protocol_version
            ):
                print(
                    f"Extension report: enabled={report['enabled']}, "
                    f"protocol={report_version}, audible hosts={hosts}"
                )
            _tab_state.clear()
            _tab_state.update(report)
            _tab_reported_at = time.time()
            _tab_protocol_version = report_version
            with _status_lock:
                current_track = status.get("track")
                current_host = status.get("host")
            res_body = json.dumps(
                {"status": "ok", "track": current_track, "host": current_host},
                separators=(",", ":"),
            ).encode()
            reply = _http_reply(200, res_body, report_version)
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


def fallback_timing(fb, seen, now):
    """Build a media-session fallback track, or None when it would stall.

    `fb` is `(title, artist, host, media_id)` from `fallback_track()`. A
    fallback is used when a non-music tab (e.g. a regular YouTube video) has
    taken over the browser's single OS media session while a music tab is
    still audible. It is published only when this track's real position was
    captured (recorded in `seen`) before the takeover and is still within
    range: playback advances 1:1 with real time, so that anchor keeps the
    progress bar moving. Without it there is only a frozen 0:00, so return
    None and publish nothing. Returns `(title, artist, pos, dur, source)`.
    """
    title, artist, host, _media_id = fb
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
        cand = normalize_title(tab["title"])
        if cand and (cand in tl or tl in cand):
            return tab
    return None


def classify_host(title, report):
    """Return the reported host for a playing title, if one matches."""
    tab = classify_tab(title, report)
    return tab["host"] if tab else None


def resolve_tab(title, source, report):
    """Resolve the audible browser tab a media-session title belongs to.

    A title that matches an enabled music tab is taken directly. An unmatched
    browser title (the Apple Music web player keeps a generic page title, so
    its real track never matches) is attributed to the sole audible music tab
    only when nothing unpublishable is also audible. If a blocked video or a
    disabled service is playing too, the media session's single title could be
    that tab's, so it is left unattributed rather than risk publishing it.
    Returns the resolved tab, or None when it can't be safely attributed.
    """
    tab = classify_tab(title, report)
    if tab is not None or not protocol.is_browser_source(source):
        return tab
    if protocol.has_unpublishable_audible_tab(report):
        return None
    enabled = protocol.enabled_tabs(report)
    if len(enabled) == 1:
        return enabled[0]
    return None


def protocol4_page_track(tab, report_version):
    """Current page track for a v4 SoundCloud/YTM tab, when supplied."""
    if report_version != protocol.PROTOCOL_VERSION or not tab:
        return None
    if protocol.service_for_host(tab["host"]) not in ("soundcloud", "youtubeMusic"):
        return None
    metadata = tab.get("metadata")
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


_legacy_sc_client_id = None
_legacy_ytm_client = None
_YOUTUBE_MUSIC_URL = "https://music.youtube.com/"
_YOUTUBE_MUSIC_ART_HOSTS = {
    "lh3.googleusercontent.com",
    "yt3.ggpht.com",
    "yt3.googleusercontent.com",
    "i.ytimg.com",
}


def _legacy_soundcloud_info(title, artist):
    """Temporary protocol v3 artwork fallback while Store approval is pending.

    Protocol v4 receives the playing page's artwork directly. Keep this legacy
    path separate so it can be deleted with v3 support.
    """
    global _legacy_sc_client_id
    try:
        if not _legacy_sc_client_id:
            html = _http_get("https://soundcloud.com/")
            for match in re.finditer(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html):
                found = re.search(
                    r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,40})"',
                    _http_get(match.group(1)),
                )
                if found:
                    _legacy_sc_client_id = found.group(1)
                    break
        if not _legacy_sc_client_id:
            return None, 0.0
        query = urllib.parse.quote(f"{title} {artist}".strip())
        data = json.loads(_http_get(
            "https://api-v2.soundcloud.com/search/tracks"
            f"?q={query}&client_id={_legacy_sc_client_id}&limit=5"
        ))
        fallback_art = None
        for track in data.get("collection", []):
            artwork = track.get("artwork_url") or track.get("user", {}).get("avatar_url")
            if fallback_art is None and artwork:
                fallback_art = artwork
            if _titles_match(title, track.get("title") or ""):
                return (
                    artwork.replace("-large.", "-t500x500.") if artwork else None,
                    (track.get("duration") or 0) / 1000.0,
                )
        return (fallback_art.replace("-large.", "-t500x500.") if fallback_art else None), 0.0
    except Exception as exc:
        print(f"Legacy SoundCloud artwork lookup failed: {type(exc).__name__}: {exc}")
        return None, 0.0


def _http_post_json(url, value, headers=None):
    request_headers = {**_UA, "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(value, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _legacy_youtube_music_artwork(video_id):
    """Temporary exact v3 lookup while the Store update is pending."""
    global _legacy_ytm_client
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return None
    try:
        if not _legacy_ytm_client:
            html = _http_get(_YOUTUBE_MUSIC_URL, {"Cookie": "SOCS=CAI"})
            values = {}
            for name in ("INNERTUBE_API_KEY", "INNERTUBE_CLIENT_VERSION", "VISITOR_DATA"):
                found = re.search(rf'"{name}"\s*:\s*"([^"]+)"', html)
                if found:
                    values[name] = found.group(1)
            if "INNERTUBE_API_KEY" not in values or "INNERTUBE_CLIENT_VERSION" not in values:
                return None
            _legacy_ytm_client = values
        headers = {"Origin": _YOUTUBE_MUSIC_URL.rstrip("/")}
        if _legacy_ytm_client.get("VISITOR_DATA"):
            headers["X-Goog-Visitor-Id"] = _legacy_ytm_client["VISITOR_DATA"]
        response = _http_post_json(
            f"{_YOUTUBE_MUSIC_URL}youtubei/v1/next?alt=json&key="
            f"{urllib.parse.quote(_legacy_ytm_client['INNERTUBE_API_KEY'], safe='')}",
            {
                "context": {"client": {
                    "clientName": "WEB_REMIX",
                    "clientVersion": _legacy_ytm_client["INNERTUBE_CLIENT_VERSION"],
                }, "user": {}},
                "enablePersistentPlaylistPanel": True,
                "isAudioOnly": True,
                "playlistId": f"RDAMVM{video_id}",
                "tunerSettingValue": "AUTOMIX_SETTING_NORMAL",
                "videoId": video_id,
                "watchEndpointMusicSupportedConfigs": {
                    "watchEndpointMusicConfig": {
                        "hasPersistentPlaylistPanel": True,
                        "musicVideoType": "MUSIC_VIDEO_TYPE_ATV",
                    }
                },
            },
            headers,
        )
        items = response["contents"]["singleColumnMusicWatchNextResultsRenderer"][
            "tabbedRenderer"
        ]["watchNextTabbedResultsRenderer"]["tabs"][0]["tabRenderer"]["content"][
            "musicQueueRenderer"
        ]["content"]["playlistPanelRenderer"]["contents"]
        for item in items:
            renderer = item.get("playlistPanelVideoRenderer")
            wrapper = item.get("playlistPanelVideoWrapperRenderer")
            if renderer is None and isinstance(wrapper, dict):
                renderer = wrapper.get("primaryRenderer", {}).get(
                    "playlistPanelVideoRenderer"
                )
            if isinstance(renderer, dict) and renderer.get("videoId") == video_id:
                thumbnails = renderer.get("thumbnail", {}).get("thumbnails", [])
                valid = []
                for thumbnail in thumbnails:
                    url = thumbnail.get("url")
                    width = thumbnail.get("width")
                    height = thumbnail.get("height")
                    parsed = urllib.parse.urlsplit(url) if isinstance(url, str) else None
                    if (
                        parsed
                        and parsed.scheme == "https"
                        and parsed.hostname in _YOUTUBE_MUSIC_ART_HOSTS
                        and type(width) is int
                        and type(height) is int
                        and width > 0
                    ):
                        clean = urllib.parse.urlunsplit(
                            (parsed.scheme, parsed.netloc, parsed.path, "", "")
                        )
                        valid.append((width == height, width, clean))
                squares = [candidate for candidate in valid if candidate[0]]
                return max(squares or valid, default=(False, 0, None))[2]
    except Exception as exc:
        print(f"Legacy YouTube Music artwork lookup failed: {type(exc).__name__}: {exc}")
    return None


def _find_apple_music_info(title, artist):
    """Best-effort Apple Music artwork and duration from the public iTunes Search API."""
    art = None
    dur = 0.0
    try:
        q = urllib.parse.quote(f"{title} {artist}".strip())
        data = json.loads(_http_get(
            "https://itunes.apple.com/search"
            f"?term={q}&media=music&entity=song&limit=5"
        ))
        matched_art = None
        matched_dur = 0.0
        fallback_art = None
        for t in data.get("results", []):
            cand_art = t.get("artworkUrl100")
            cand_dur = (t.get("trackTimeMillis") or 0) / 1000.0
            if _titles_match(title, t.get("trackName") or ""):
                if isinstance(cand_art, str) and cand_art:
                    matched_art = cand_art
                matched_dur = cand_dur
                break
            if fallback_art is None and isinstance(cand_art, str) and cand_art:
                fallback_art = cand_art
        best_art = matched_art or fallback_art
        if best_art:
            art = best_art.replace("100x100", "500x500")
        # Duration only from a title-matched result; a fallback thumbnail is low
        # harm, a wrong duration paints a wrong progress bar.
        dur = matched_dur
    except Exception as e:
        print(f"Apple Music artwork lookup failed: {type(e).__name__}: {e}")
    return art, dur


def _find_apple_music_artwork(title, artist):
    return _find_apple_music_info(title, artist)[0]


def find_artwork_and_info(
    title, artist, host=None, media_id=None, source=None, metadata=None, legacy=False
):
    """Return (art_url, duration_s) from trusted page metadata or Apple Search."""
    key = (host, media_id, source, title, artist, str(metadata), legacy)
    if key in _artwork_cache:
        return _artwork_cache[key]

    service = protocol.service_for_host(host)
    art = None
    dur = 0.0

    if metadata:
        art = metadata["artwork"]
    elif legacy and service == "soundcloud":
        art, dur = _legacy_soundcloud_info(title, artist)
    elif legacy and service == "youtubeMusic":
        art = _legacy_youtube_music_artwork(media_id)
        if not art:
            art, dur = _find_apple_music_info(title, artist)
    elif service == "appleMusic":
        art, dur = _find_apple_music_info(title, artist)
    elif not protocol.is_browser_source(source):
        art, dur = _find_apple_music_info(title, artist)

    res = (art, dur)
    _artwork_cache[key] = res
    if len(_artwork_cache) > 500:
        _artwork_cache.pop(next(iter(_artwork_cache)))
    return res


def find_artwork(title, artist, host=None, media_id=None, source=None, metadata=None, legacy=False):
    """Return source-specific online album artwork for the current track."""
    art, _ = find_artwork_and_info(title, artist, host, media_id, source, metadata, legacy)
    return art


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
        legacy_artwork = report is not None and _tab_protocol_version == protocol.LEGACY_PROTOCOL_VERSION
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
                # A v4 SoundCloud/YTM page sample is the player itself. It
                # must replace stale Windows metadata at a track transition;
                # otherwise the old title is paired with the next track's
                # in-page position and Discord starts in the middle.
                page_track = protocol4_page_track(tab, _tab_protocol_version)
                if page_track:
                    title, artist = page_track
                    generic_title = False
                # Protocol 3 and Apple retain their conservative metadata
                # attribution until Windows identifies the same track.
                elif page_metadata and (
                    generic_title or _titles_match(title, page_metadata["title"])
                ):
                    title = page_metadata["title"]
                    artist = page_metadata["artist"]
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
                for reported_tab in protocol.enabled_tabs(report):
                    if (
                        reported_tab["host"] == host
                        and reported_tab.get("metadata", {}).get("title") == title
                        and reported_tab.get("metadata", {}).get("artist") == artist
                    ):
                        page_metadata = reported_tab["metadata"]
                        break
                track = fallback_timing(fb, seen, time.time())
                if track:
                    title, artist, pos, dur, source = track

        if track:
            now = time.time()
            use_artwork = settings.artwork_enabled()
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
            key = (title, artist, host, media_id, use_artwork, dur > 0)
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
                if use_artwork:
                    art, info_dur = await asyncio.to_thread(
                        find_artwork_and_info,
                        title,
                        artist,
                        host,
                        media_id,
                        source,
                        page_metadata,
                        legacy_artwork,
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
