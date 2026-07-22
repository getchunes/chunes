"""Validation and service policy for browser-extension reports."""

from dataclasses import dataclass
import json
import math
import re
import time
from urllib.parse import urlsplit


MAX_HEADER_BYTES = 8192
MAX_BODY_BYTES = 32768
MAX_TABS = 64
MAX_HOST_CHARS = 253
MAX_TITLE_CHARS = 512
MAX_MEDIA_ID_CHARS = 11
MAX_ARTWORK_URL_CHARS = 2048
MAX_PLAYBACK_SECONDS = 24 * 60 * 60
MAX_SAMPLED_AT_MS = 8.64e15  # largest epoch a JavaScript Date can represent
REPORT_TTL_SECONDS = 90
PROTOCOL_VERSION = 4

REPORT_KEYS = {"enabled", "services", "tabs"}
PROTOCOL_KEY = "protocol"
LEGACY_PROTOCOL_VERSION = 3
TAB_KEYS = {"host", "mediaId", "title"}
# Page-level playback timing measured by the extension (MusicKit). Only the
# Apple Music web player needs it: its OS media session misreports position
# and duration, while SoundCloud/YTM are already correct without help.
PLAYBACK_KEYS = {"position", "duration", "playing", "sampledAt"}
PAGE_METADATA_KEY = "metadata"
PAGE_METADATA_KEYS = {"title", "artist", "artwork"}
SERVICE_KEYS = {"appleMusic", "soundcloud", "youtubeMusic"}
SERVICE_LABELS = {
    "appleMusic": "Apple Music",
    "soundcloud": "SoundCloud",
    "youtubeMusic": "YouTube Music",
}
# Services whose page titles never contain the playing track (Apple Music's
# web player keeps the page name while playing), so a playing title can only
# be attributed to them by audible-tab presence.
UNTITLED_TRACK_SERVICES = {"appleMusic"}
BROWSER_SOURCE_MARKERS = (
    "brave",
    "chrome",
    "msedge",
    "firefox",
    "opera",
    "vivaldi",
)
_HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_YOUTUBE_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_HOST_RE = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


class ProtocolError(ValueError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class RequestHead:
    action: str
    content_length: int = 0


def _unique_object(pairs):
    value = {}
    for name, member in pairs:
        if name in value:
            raise ProtocolError(400, "Duplicate JSON object member")
        value[name] = member
    return value


def _reject_json_constant(value):
    raise ProtocolError(400, f"Invalid JSON constant: {value}")


def _validate_playback_number(value, maximum):
    if type(value) is bool or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= maximum:
        return None
    return number


def _validate_tab_playback(tab, host, version):
    """Validated page playback fields for the Apple Music player."""
    present = set(tab) & PLAYBACK_KEYS
    if not present:
        return {}
    if present != PLAYBACK_KEYS:
        raise ProtocolError(400, "Incomplete tab playback")
    if host != "music.apple.com":
        raise ProtocolError(400, "Unexpected tab playback")

    position = _validate_playback_number(tab["position"], MAX_PLAYBACK_SECONDS)
    sampled_at = _validate_playback_number(tab["sampledAt"], MAX_SAMPLED_AT_MS)
    if position is None or sampled_at is None:
        raise ProtocolError(400, "Invalid tab playback")
    duration = tab["duration"]
    if duration is not None:
        duration = _validate_playback_number(duration, MAX_PLAYBACK_SECONDS)
        if duration is None:
            raise ProtocolError(400, "Invalid tab playback")
    if type(tab["playing"]) is not bool:
        raise ProtocolError(400, "Invalid tab playback")

    return {
        "position": position,
        "duration": duration,
        "playing": tab["playing"],
        "sampledAt": sampled_at,
    }


def _validate_page_metadata(tab, host):
    """Validated current page Media Session metadata, if supplied."""
    metadata = tab.get(PAGE_METADATA_KEY)
    if metadata is None:
        return {}
    service = service_for_host(host)
    if service not in {"appleMusic", "soundcloud", "youtubeMusic"}:
        raise ProtocolError(400, "Unexpected page metadata")
    if not isinstance(metadata, dict) or set(metadata) != PAGE_METADATA_KEYS:
        raise ProtocolError(400, "Invalid page metadata")

    title = metadata["title"]
    artist = metadata["artist"]
    artwork = metadata["artwork"]
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
        raise ProtocolError(400, "Invalid page title")
    if not isinstance(artist, str) or len(artist) > MAX_TITLE_CHARS:
        raise ProtocolError(400, "Invalid page artist")
    if artwork is not None:
        if not isinstance(artwork, str) or not 0 < len(artwork) <= MAX_ARTWORK_URL_CHARS:
            raise ProtocolError(400, "Invalid page artwork")
        parsed = urlsplit(artwork)
        hostname = (parsed.hostname or "").lower()
        allowed_artwork = {
            "appleMusic": hostname.endswith(".mzstatic.com"),
            "soundcloud": hostname.endswith(".sndcdn.com"),
            "youtubeMusic": hostname in {
                "i.ytimg.com",
                "lh3.googleusercontent.com",
                "yt3.ggpht.com",
                "yt3.googleusercontent.com",
            },
        }[service]
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not allowed_artwork
        ):
            raise ProtocolError(400, "Invalid page artwork")

    return {
        PAGE_METADATA_KEY: {
            "title": title.strip(),
            "artist": artist.strip(),
            "artwork": artwork,
        }
    }


def validate_report(value, include_version=False):
    """Return a detached legacy v3 or explicit v4 report.

    Compatibility is intentionally localized here: v3 has the historical
    exact shape, while v4 adds an explicit marker before it may carry page
    metadata. Remove the v3 branch when its supported-release window ends.
    """
    if not isinstance(value, dict):
        raise ProtocolError(400, "Invalid report object")
    if set(value) == REPORT_KEYS:
        version = LEGACY_PROTOCOL_VERSION
    elif set(value) == REPORT_KEYS | {PROTOCOL_KEY} and value[PROTOCOL_KEY] == PROTOCOL_VERSION:
        version = PROTOCOL_VERSION
    else:
        raise ProtocolError(400, "Invalid report object")
    if type(value["enabled"]) is not bool:
        raise ProtocolError(400, "Invalid enabled value")

    services = value["services"]
    if not isinstance(services, dict) or set(services) != SERVICE_KEYS:
        raise ProtocolError(400, "Invalid services object")
    if any(type(services[name]) is not bool for name in SERVICE_KEYS):
        raise ProtocolError(400, "Invalid service value")

    tabs = value["tabs"]
    if not isinstance(tabs, list) or len(tabs) > MAX_TABS:
        raise ProtocolError(400, "Invalid tabs value")

    clean_tabs = []
    for tab in tabs:
        optional_keys = PLAYBACK_KEYS | ({PAGE_METADATA_KEY} if version == PROTOCOL_VERSION else set())
        if not isinstance(tab, dict) or not TAB_KEYS <= set(tab) <= TAB_KEYS | optional_keys:
            raise ProtocolError(400, "Invalid tab object")
        host = tab["host"]
        media_id = tab["mediaId"]
        title = tab["title"]
        if not isinstance(host, str) or not 0 < len(host) <= MAX_HOST_CHARS:
            raise ProtocolError(400, "Invalid tab host")
        if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
            raise ProtocolError(400, "Invalid tab title")
        if host != host.lower() or not _HOST_RE.fullmatch(host):
            raise ProtocolError(400, "Invalid tab host")
        if media_id is not None and (
            not isinstance(media_id, str)
            or not 0 < len(media_id) <= MAX_MEDIA_ID_CHARS
        ):
            raise ProtocolError(400, "Invalid tab media ID")
        if host == "music.youtube.com":
            if media_id is not None and not _YOUTUBE_VIDEO_ID_RE.fullmatch(media_id):
                raise ProtocolError(400, "Invalid YouTube Music video ID")
        elif media_id is not None:
            raise ProtocolError(400, "Unexpected tab media ID")
        clean_tab = {"host": host, "mediaId": media_id, "title": title}
        clean_tab.update(_validate_tab_playback(tab, host, version))
        if version == PROTOCOL_VERSION:
            clean_tab.update(_validate_page_metadata(tab, host))
        clean_tabs.append(clean_tab)

    report = {
        "enabled": value["enabled"],
        "services": {
            "appleMusic": services["appleMusic"],
            "soundcloud": services["soundcloud"],
            "youtubeMusic": services["youtubeMusic"],
        },
        "tabs": clean_tabs,
    }
    return (report, version) if include_version else report


def parse_report_body(body, include_version=False):
    if not body or len(body) > MAX_BODY_BYTES:
        raise ProtocolError(400, "Invalid body size")
    try:
        value = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(400, "Invalid JSON") from exc
    return validate_report(value, include_version)


def parse_request_head(raw_head):
    """Parse a complete HTTP header block without its final CRLFCRLF."""
    if not raw_head or len(raw_head) > MAX_HEADER_BYTES:
        raise ProtocolError(431, "Request headers too large")
    try:
        text = raw_head.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError(400, "Invalid request headers") from exc

    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[2] not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ProtocolError(400, "Invalid request line")
    method, path, _ = parts

    headers = {}
    for line in lines[1:]:
        if not line or line[0] in " \t" or ":" not in line:
            raise ProtocolError(400, "Invalid request headers")
        name, value = line.split(":", 1)
        if not _HEADER_NAME_RE.fullmatch(name):
            raise ProtocolError(400, "Invalid request headers")
        name = name.lower()
        if name in headers or any(
            ord(char) < 0x20 or ord(char) == 0x7F for char in value
        ):
            raise ProtocolError(400, "Invalid request headers")
        headers[name] = value.strip()

    host = headers.get("host", "").lower()
    if host not in {
        "127.0.0.1",
        "127.0.0.1:52846",
        "localhost",
        "localhost:52846",
        "[::1]",
        "[::1]:52846",
    }:
        raise ProtocolError(403, "Invalid host")
    if "transfer-encoding" in headers or "content-encoding" in headers:
        raise ProtocolError(400, "Encoded request bodies are not supported")
    if "expect" in headers:
        raise ProtocolError(400, "Expect is not supported")

    if method == "GET" and path == "/state":
        if headers.get("content-length", "0") != "0":
            raise ProtocolError(400, "GET body is not supported")
        return RequestHead("state")

    if path != "/tabs":
        raise ProtocolError(404, "Not found")
    if method != "POST":
        raise ProtocolError(405, "Method not allowed")

    if headers.get("content-type", "").strip().lower() != "application/json":
        raise ProtocolError(415, "Content-Type must be application/json")
    length = headers.get("content-length", "")
    if not re.fullmatch(r"[0-9]+", length):
        raise ProtocolError(411, "Content-Length is required")
    content_length = int(length)
    if content_length < 1 or content_length > MAX_BODY_BYTES:
        raise ProtocolError(413, "Request body too large")
    return RequestHead("report", content_length)


def report_is_fresh(reported_at, now=None):
    if not reported_at:
        return False
    if now is None:
        now = time.time()
    age = now - reported_at
    return 0 <= age <= REPORT_TTL_SECONDS


def service_for_host(host):
    normalized = (host or "").strip().lower().rstrip(".")
    if normalized == "music.apple.com":
        return "appleMusic"
    if normalized == "soundcloud.com" or normalized.endswith(".soundcloud.com"):
        return "soundcloud"
    if normalized == "music.youtube.com":
        return "youtubeMusic"
    return None


def service_label_for_host(host, fallback=""):
    return SERVICE_LABELS.get(service_for_host(host), fallback)


def service_is_enabled(report, host):
    if not report or not report["enabled"]:
        return False
    service = service_for_host(host)
    return service is not None and report["services"][service]


def enabled_tabs(report):
    if not report or not report["enabled"]:
        return []
    return [
        tab
        for tab in report["tabs"]
        if service_is_enabled(report, tab["host"])
    ]


def has_unpublishable_audible_tab(report):
    """True when the browser is audibly playing something that must not be
    published: a non-music tab (e.g. a regular YouTube video) or a service the
    user disabled.

    Reports list only audible tabs, so any tab that is not an enabled music
    service means non-allowlisted audio is playing right now. The OS media
    session reports a single browser title without saying which tab produced
    it, so when such a tab is present an unmatched title cannot be safely
    attributed to a music service.
    """
    if not report or not report["enabled"]:
        return False
    return any(
        not service_is_enabled(report, tab["host"]) for tab in report["tabs"]
    )


def untitled_service_tab(report):
    """Tab to attribute a playing title that matches no reported tab title.

    Only tabs of UNTITLED_TRACK_SERVICES qualify: their audible presence is
    the strongest available signal that the unmatched browser session is
    theirs.
    """
    for tab in enabled_tabs(report):
        if service_for_host(tab["host"]) in UNTITLED_TRACK_SERVICES:
            return tab
    return None


def is_browser_source(source):
    normalized = (source or "").lower()
    return any(marker in normalized for marker in BROWSER_SOURCE_MARKERS)


def browser_track_is_allowed(source, report, host):
    """Apply a fresh extension report to browser media only.

    A classified host came from the report's audible tabs. An absent host
    therefore also suppresses paused and unrelated browser media.
    """
    if not is_browser_source(source) or report is None:
        return True
    return service_is_enabled(report, host)


def safe_public_state(report, fresh):
    if not fresh or not report:
        return {
            "fresh": False,
            "enabled": False,
            "services": {"appleMusic": False, "soundcloud": False, "youtubeMusic": False},
        }
    return {
        "fresh": True,
        "enabled": report["enabled"],
        "services": dict(report["services"]),
    }
