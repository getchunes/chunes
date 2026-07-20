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


def _http_reply(status, body=b""):
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
        headers.append(f"X-Chunes-Protocol: {protocol.PROTOCOL_VERSION}")
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
            report = protocol.parse_report_body(body)
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
            reply = _http_reply(200, res_body)
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
        if service == "soundcloud" and " by " in t:
            title, artist = t.rsplit(" by ", 1)
            return title.strip(), artist.strip(), host, tab["mediaId"]
        if service == "youtubeMusic":
            t = re.sub(r"\s*[\|-]\s*YouTube Music$", "", t, flags=re.IGNORECASE).strip()
            if normalize_title(t) in ("", "youtube music", "youtube"):
                continue
            if " - " in t:
                title, artist = t.rsplit(" - ", 1)
                return title.strip(), artist.strip(), host, tab["mediaId"]
            return t, "", host, tab["mediaId"]
    return None


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


_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    )
}
_YOUTUBE_MUSIC_URL = "https://music.youtube.com/"
_YOUTUBE_MUSIC_ART_HOSTS = {
    "lh3.googleusercontent.com",
    "yt3.ggpht.com",
    "yt3.googleusercontent.com",
    "i.ytimg.com",
}
_sc_client_id = None
_ytm_client = None
_artwork_cache = {}


def _http_get(url, headers=None):
    request_headers = dict(_UA)
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="replace")


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


def _soundcloud_client_id():
    """SoundCloud's public web client_id, scraped from their app scripts."""
    global _sc_client_id
    if _sc_client_id:
        return _sc_client_id
    html = _http_get("https://soundcloud.com/")
    for m in re.finditer(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html):
        js = _http_get(m.group(1))
        found = re.search(r'client_id\s*[:=]\s*"([A-Za-z0-9]{20,40})"', js)
        if found:
            _sc_client_id = found.group(1)
            return _sc_client_id
    return None


def _find_soundcloud_info(title, artist):
    """Best-effort SoundCloud artwork and duration for the current title and artist."""
    art = None
    dur = 0.0
    try:
        cid = _soundcloud_client_id()
        if cid:
            q = urllib.parse.quote(f"{title} {artist}".strip())
            data = json.loads(_http_get(
                "https://api-v2.soundcloud.com/search/tracks"
                f"?q={q}&client_id={cid}&limit=5"
            ))
            tl = title.lower()
            best_art = None
            best_dur = 0.0
            for t in data.get("collection", []):
                cand = (t.get("artwork_url")
                        or t.get("user", {}).get("avatar_url"))
                cand_dur = (t.get("duration") or 0) / 1000.0
                ct = (t.get("title") or "").lower()
                if ct and (ct in tl or tl in ct):
                    if cand:
                        best_art = cand
                    best_dur = cand_dur
                    break
                if best_art is None and cand:
                    best_art = cand
                    best_dur = cand_dur
            if best_art:
                art = best_art.replace("-large.", "-t500x500.")
            dur = best_dur
    except Exception as e:
        print(f"SoundCloud artwork lookup failed: {type(e).__name__}: {e}")
    return art, dur


def _find_soundcloud_artwork(title, artist):
    return _find_soundcloud_info(title, artist)[0]


def _youtube_music_client():
    """Return public YouTube Music web client values from its own page."""
    global _ytm_client
    if _ytm_client:
        return _ytm_client
    html = _http_get(_YOUTUBE_MUSIC_URL, {"Cookie": "SOCS=CAI"})
    values = {}
    for name in ("INNERTUBE_API_KEY", "INNERTUBE_CLIENT_VERSION", "VISITOR_DATA"):
        found = re.search(rf'"{name}"\s*:\s*"([^"]+)"', html)
        if found:
            values[name] = found.group(1)
    if "INNERTUBE_API_KEY" not in values or "INNERTUBE_CLIENT_VERSION" not in values:
        return None
    _ytm_client = values
    return _ytm_client


def _youtube_music_track(response, video_id):
    try:
        tabs = response["contents"]["singleColumnMusicWatchNextResultsRenderer"][
            "tabbedRenderer"
        ]["watchNextTabbedResultsRenderer"]["tabs"]
        items = tabs[0]["tabRenderer"]["content"]["musicQueueRenderer"][
            "content"
        ]["playlistPanelRenderer"]["contents"]
    except (KeyError, IndexError, TypeError):
        return None

    for item in items:
        renderer = item.get("playlistPanelVideoRenderer")
        wrapper = item.get("playlistPanelVideoWrapperRenderer")
        if renderer is None and isinstance(wrapper, dict):
            primary = wrapper.get("primaryRenderer", {})
            renderer = primary.get("playlistPanelVideoRenderer")
        if isinstance(renderer, dict) and renderer.get("videoId") == video_id:
            return renderer
    return None


def _square_youtube_music_artwork(track):
    thumbnails = track.get("thumbnail", {}).get("thumbnails", [])
    square_candidates = []
    any_candidates = []
    for thumbnail in thumbnails:
        url = thumbnail.get("url")
        width = thumbnail.get("width")
        height = thumbnail.get("height")
        if (
            not isinstance(url, str)
            or type(width) is not int
            or type(height) is not int
            or width <= 0
        ):
            continue
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname in _YOUTUBE_MUSIC_ART_HOSTS:
            clean_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
            if width == height:
                square_candidates.append((width, clean_url))
            else:
                any_candidates.append((width, clean_url))
                
    if square_candidates:
        return max(square_candidates)[1]
    return max(any_candidates, default=(0, None))[1]


def _find_youtube_music_artwork(video_id):
    """Return exact square album artwork for a YouTube Music video ID."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return None
    try:
        client = _youtube_music_client()
        if not client:
            return None
        headers = {"Origin": _YOUTUBE_MUSIC_URL.rstrip("/")}
        visitor = client.get("VISITOR_DATA")
        if visitor:
            headers["X-Goog-Visitor-Id"] = visitor
        body = {
            "context": {
                "client": {
                    "clientName": "WEB_REMIX",
                    "clientVersion": client["INNERTUBE_CLIENT_VERSION"],
                },
                "user": {},
            },
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
        }
        api_key = urllib.parse.quote(client["INNERTUBE_API_KEY"], safe="")
        response = _http_post_json(
            f"{_YOUTUBE_MUSIC_URL}youtubei/v1/next?alt=json&key={api_key}",
            body,
            headers,
        )
        track = _youtube_music_track(response, video_id)
        return _square_youtube_music_artwork(track) if track else None
    except Exception as e:
        print(f"YouTube Music artwork lookup failed: {type(e).__name__}: {e}")
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
        tl = title.lower()
        best_art = None
        best_dur = 0.0
        for t in data.get("results", []):
            cand_art = t.get("artworkUrl100")
            cand_dur = (t.get("trackTimeMillis") or 0) / 1000.0
            ct = (t.get("trackName") or "").lower()
            if ct and (ct in tl or tl in ct):
                if isinstance(cand_art, str) and cand_art:
                    best_art = cand_art
                best_dur = cand_dur
                break
            if best_art is None and isinstance(cand_art, str) and cand_art:
                best_art = cand_art
                best_dur = cand_dur
        if best_art:
            art = best_art.replace("100x100", "500x500")
        dur = best_dur
    except Exception as e:
        print(f"Apple Music artwork lookup failed: {type(e).__name__}: {e}")
    return art, dur


def _find_apple_music_artwork(title, artist):
    return _find_apple_music_info(title, artist)[0]


def find_artwork_and_info(title, artist, host=None, media_id=None, source=None):
    """Return (art_url, duration_s) for the track with cross-provider fallbacks."""
    key = (host, media_id, source, title, artist)
    if key in _artwork_cache:
        return _artwork_cache[key]

    service = protocol.service_for_host(host)
    art = None
    dur = 0.0

    if service == "youtubeMusic":
        if media_id:
            art = _find_youtube_music_artwork(media_id)
        if not art:
            art, dur = _find_apple_music_info(title, artist)
            if not art:
                art, dur = _find_soundcloud_info(title, artist)
    elif service == "appleMusic":
        art, dur = _find_apple_music_info(title, artist)
        if not art:
            art, dur = _find_soundcloud_info(title, artist)
    elif service == "soundcloud" or not protocol.is_browser_source(source):
        art, dur = _find_soundcloud_info(title, artist)
        if not art:
            art, dur = _find_apple_music_info(title, artist)

    res = (art, dur)
    _artwork_cache[key] = res
    if len(_artwork_cache) > 500:
        _artwork_cache.pop(next(iter(_artwork_cache)))
    return res


def find_artwork(title, artist, host=None, media_id=None, source=None):
    """Return source-specific online album artwork for the current track."""
    art, _ = find_artwork_and_info(title, artist, host, media_id, source)
    return art


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    return cfg


_first_seen_at = {}  # (title, artist) -> epoch of the first poll that saw it


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

        track_key = (props.title, props.artist or "")
        now = time.time()
        first_seen = _first_seen_at.get(track_key)
        if first_seen is None:
            first_seen = _first_seen_at[track_key] = now
            if len(_first_seen_at) > 100:
                _first_seen_at.pop(next(iter(_first_seen_at)))

        # A throttled background tab (e.g. Apple Music) can update title and
        # timeline on different ticks, so the triple isn't always a coherent
        # snapshot of the current track. Only trust/extrapolate position when
        # the anchor postdates when we first saw this title (skewed by a
        # couple seconds for clock/poll slop) and duration is sane; otherwise
        # the snapshot likely belongs to the previous track.
        try:
            anchor = tl.last_updated_time.timestamp()
            elapsed = now - anchor
            coherent = dur > 0 and 0 <= pos <= dur and anchor > first_seen - 2
            if coherent and 0 < elapsed < POLL_SECONDS + 5:
                pos += elapsed
            elif not coherent:
                pos = 0.0
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
        if track:
            title, artist, pos, dur, source = track
            if normalize_title(title) in ("youtube music", "soundcloud", "apple music", "youtube"):
                track = None
            else:
                tab = classify_tab(title, report)
                if tab is None and protocol.is_browser_source(source):
                    enabled = protocol.enabled_tabs(report)
                    if len(enabled) == 1:
                        tab = enabled[0]
                    else:
                        tab = protocol.untitled_service_tab(report)
                if tab:
                    host = tab["host"]
                    media_id = tab["mediaId"]
                if host is None and last is not None and last[0][0] == title and last[0][1] == artist:
                    host = last[0][2]
                    media_id = last[0][3]
                if not protocol.browser_track_is_allowed(source, report, host):
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
                pos, dur, source = 0.0, 0.0, f"tab:{host}"
                # If we saw this track's real position before losing the
                # media slot, its wall-clock anchor is still valid: playback
                # advances 1:1 with real time.
                anchor = seen.get((title, artist))
                if anchor:
                    a_start, a_dur = anchor
                    elapsed = time.time() - a_start
                    if 0 < elapsed < a_dur + 30:
                        pos, dur = elapsed, a_dur
                track = (title, artist, pos, dur, source)

        if track:
            now = time.time()
            use_artwork = settings.artwork_enabled()
            # Fallback tracks have no position; pin start to 0 so the
            # unchanged check stays stable and no timestamps are sent.
            # A pos outside (0, dur) is not a trustworthy mid-track position;
            # anchor to now instead of drifting start into the past.
            if source.startswith("tab:"):
                start = 0
            else:
                start = int(now - pos) if 0 < pos < dur else int(now)
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
                art, info_dur = await asyncio.to_thread(
                    find_artwork_and_info, title, artist, host, media_id, source
                )
                if not use_artwork:
                    art = None
                if dur <= 0 and info_dur > 0:
                    dur = info_dur
                    start = int(now - pos) if 0 < pos < dur else int(now)
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
