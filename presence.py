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

# Track titles can contain emoji etc. that the default cp1252 console
# encoding can't represent.
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CONFIG_PATH = Path(__file__).parent / "config.json"
POLL_SECONDS = 5

DEFAULT_CONFIG = {
    "client_id": "1527834085383213106",
    "sources": ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"],
    "service_label": "",
    "image_key": "",
}

# Live state for the tray app: current track description or None.
status = {"track": None}


async def _read_output(self):
    """Replacement for pypresence's read_output that tolerates non-response
    frames on the IPC pipe (the stock version does payload["evt"] and raises
    KeyError on anything that isn't a command response)."""
    while True:
        try:
            preamble = await asyncio.wait_for(
                self.sock_reader.read(8), self.response_timeout
            )
            status_code, length = struct.unpack("<II", preamble[:8])
            data = await asyncio.wait_for(
                self.sock_reader.read(length), self.response_timeout
            )
        except (BrokenPipeError, struct.error):
            raise _base.PipeClosed
        except asyncio.TimeoutError:
            raise _base.ResponseTimeout
        payload = json.loads(data.decode("utf-8"))
        if payload.get("evt") == "ERROR":
            raise _base.ServerError(payload["data"]["message"])
        if "evt" not in payload:
            print(f"Ignoring non-response frame (op {status_code}): {payload}")
            continue
        return payload


_base.BaseClient.read_output = _read_output


TAB_REPORT_PORT = 52846
BLOCKED_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com"}
SERVICE_LABELS = {
    "soundcloud.com": "SoundCloud",
    "music.youtube.com": "YouTube Music",
}

# Latest audible-tab report from the browser extension: which sites are
# actually making sound. Windows only tells us "Brave", so without this we
# can't tell SoundCloud from a regular YouTube video.
_tab_state = {"time": 0.0, "tabs": []}


async def _handle_tab_report(reader, writer):
    reply = b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n"
    try:
        raw = await asyncio.wait_for(reader.read(65536), 5)
        if raw.startswith(b"GET /state"):
            state = json.dumps(_tab_state).encode()
            reply = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                     b"Content-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(state), state))
        else:
            body = raw.split(b"\r\n\r\n", 1)
            if len(body) == 2 and body[1]:
                tabs = json.loads(body[1])
                hosts = sorted({t.get("host", "") for t in tabs})
                old = sorted({t.get("host", "") for t in _tab_state["tabs"]})
                if _tab_state["time"] == 0 or hosts != old:
                    print(f"Extension report: audible hosts = {hosts}")
                _tab_state["tabs"] = tabs
                _tab_state["time"] = time.time()
    except Exception:
        pass
    try:
        writer.write(reply)
        await writer.drain()
        writer.close()
    except Exception:
        pass


def fallback_track():
    """When Windows' media session is unusable (e.g. a blocked YouTube video
    holds the browser's only media slot), build track info from the audible
    music tab's title. No playback position is available this way."""
    if time.time() - _tab_state["time"] > 90:
        return None
    for tab in _tab_state["tabs"]:
        host = tab.get("host") or ""
        t = (tab.get("title") or "").strip()
        if host == "soundcloud.com" and " by " in t:
            title, artist = t.rsplit(" by ", 1)
            return title.strip(), artist.strip(), host
        if host == "music.youtube.com":
            t = re.sub(r"\s*-\s*YouTube Music$", "", t)
            if " - " in t:
                title, artist = t.rsplit(" - ", 1)
                return title.strip(), artist.strip(), host
            if t:
                return t, "", host
    return None


def classify_host(title):
    """Match the playing title against audible browser tabs to find which
    site it comes from. None if the extension isn't reporting or no match."""
    if time.time() - _tab_state["time"] > 90:
        return None
    tl = title.lower().strip()
    if not tl:
        return None
    for tab in _tab_state["tabs"]:
        if tl in (tab.get("title") or "").lower():
            return tab.get("host") or None
    return None


_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_sc_client_id = None
_artwork_cache = {}


def _http_get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8", errors="replace")


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


def find_artwork(title, artist):
    """Best-effort album art URL by searching SoundCloud. Returns None if the
    track can't be found (e.g. it's actually a YouTube video)."""
    key = (title, artist)
    if key in _artwork_cache:
        return _artwork_cache[key]
    art = None
    try:
        cid = _soundcloud_client_id()
        if cid:
            q = urllib.parse.quote(f"{title} {artist}".strip())
            data = json.loads(_http_get(
                "https://api-v2.soundcloud.com/search/tracks"
                f"?q={q}&client_id={cid}&limit=5"
            ))
            tl = title.lower()
            best = None
            for t in data.get("collection", []):
                cand = (t.get("artwork_url")
                        or t.get("user", {}).get("avatar_url"))
                if not cand:
                    continue
                ct = (t.get("title") or "").lower()
                if ct and (ct in tl or tl in ct):
                    best = cand
                    break
                if best is None:
                    best = cand
            if best:
                art = best.replace("-large.", "-t500x500.")
    except Exception as e:
        print(f"Artwork lookup failed: {type(e).__name__}: {e}")
    _artwork_cache[key] = art
    if len(_artwork_cache) > 500:
        _artwork_cache.pop(next(iter(_artwork_cache)))
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
        try:
            elapsed = time.time() - tl.last_updated_time.timestamp()
            if 0 < elapsed < 3600:
                pos += elapsed
        except (OSError, OverflowError, ValueError):
            pass
        return (
            props.title,
            props.artist or "",
            pos,
            tl.end_time.total_seconds(),
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
    await asyncio.start_server(_handle_tab_report, "127.0.0.1", TAB_REPORT_PORT)
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
                    return
                except Exception:
                    await asyncio.sleep(10)
            print("Could not reconnect to Discord after 10 minutes, exiting.")
            sys.exit(1)

    while True:
        try:
            track = await get_playing_track(allowed)
        except OSError:
            track = None

        host = None
        if track:
            title, artist, pos, dur, source = track
            host = classify_host(title)
            if host in BLOCKED_HOSTS:
                if last is not None:
                    print(f"Ignoring {host}: {title[:60]}")
                track = None
        if not track:
            # A blocked video may be hogging the browser's only media
            # session; the extension still knows if a music tab is audible.
            fb = fallback_track()
            if fb:
                title, artist, host = fb
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
            # Fallback tracks have no position; pin start to 0 so the
            # unchanged check stays stable and no timestamps are sent.
            start = int(now - pos) if dur > 0 else 0
            if dur > 0 and not source.startswith("tab:"):
                seen[(title, artist)] = (start, dur)
                if len(seen) > 100:
                    seen.pop(next(iter(seen)))
            # Re-send only on track change or a seek (start timestamp moved
            # by more than a few seconds); Discord drops clients that spam
            # SET_ACTIVITY every poll.
            key = (title, artist)
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
                if last is None or last[0] != key:
                    print(f"Now playing: {title} - {artist} ({source})")
                    status["track"] = f"{title} - {artist}" if artist else title
                art = await asyncio.to_thread(find_artwork, title, artist)
                kwargs = dict(
                    activity_type=ActivityType.LISTENING,
                    details=title[:128],
                    state=(f"by {artist}"[:128] if artist else None),
                )
                if dur > 0:
                    kwargs["start"] = start
                    kwargs["end"] = int(start + dur)
                if art or image_key:
                    kwargs["large_image"] = art or image_key
                label = SERVICE_LABELS.get(host, service)
                if label:
                    kwargs["large_text"] = label
                await send(lambda r: r.update(**kwargs))
                last = (key, start, now)
        else:
            if last is not None:
                print("Playback stopped, clearing status.")
                last = None
                status["track"] = None
                await send(lambda r: r.clear())

        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
