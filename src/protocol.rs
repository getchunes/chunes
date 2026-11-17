//! Validation and service policy for browser-extension reports.
//!
//! A port of `protocol.py`. The accepted wire format, limits, and status
//! codes are the same, so Chune ID cannot tell the two apart.

use std::collections::HashSet;
use std::fmt;

use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

pub const MAX_HEADER_BYTES: usize = 8192;
pub const MAX_BODY_BYTES: usize = 32768;
pub const MAX_TABS: usize = 64;
pub const MAX_HOST_CHARS: usize = 253;
pub const MAX_TITLE_CHARS: usize = 512;
pub const MAX_MEDIA_ID_CHARS: usize = 11;
pub const MAX_ARTWORK_URL_CHARS: usize = 2048;
pub const MAX_TRACK_URL_CHARS: usize = 2048;
pub const MAX_PLAYBACK_SECONDS: f64 = 24.0 * 60.0 * 60.0;
/// Largest epoch a JavaScript Date can represent.
pub const MAX_SAMPLED_AT_MS: f64 = 8.64e15;
pub const REPORT_TTL_SECONDS: f64 = 90.0;
pub const PROTOCOL_VERSION: u8 = 5;
/// A released extension keeps sending v4 until its browser updates, and the
/// store build lags the desktop build by however long review takes, so both
/// versions stay accepted and the response echoes whichever one arrived.
pub const SUPPORTED_PROTOCOL_VERSIONS: [u8; 2] = [4, 5];

const REPORT_KEYS: [&str; 4] = ["enabled", "services", "tabs", "protocol"];
const TAB_KEYS: [&str; 3] = ["host", "mediaId", "title"];
const TRACK_URL_KEY: &str = "trackUrl";
const PLAYBACK_KEYS: [&str; 4] = ["position", "duration", "playing", "sampledAt"];
const PAGE_METADATA_KEY: &str = "metadata";
const PAGE_METADATA_KEYS: [&str; 3] = ["title", "artist", "artwork"];
const SERVICE_KEYS: [&str; 3] = ["appleMusic", "soundcloud", "youtubeMusic"];
const BROWSER_SOURCE_MARKERS: [&str; 6] =
    ["brave", "chrome", "msedge", "firefox", "opera", "vivaldi"];
pub const TAB_REPORT_PORT: u16 = 52846;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError {
    pub status: u16,
    pub reason: &'static str,
}

impl ProtocolError {
    fn new(status: u16, reason: &'static str) -> Self {
        Self { status, reason }
    }
}

impl fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.reason, self.status)
    }
}

impl std::error::Error for ProtocolError {}

fn bad(reason: &'static str) -> ProtocolError {
    ProtocolError::new(400, reason)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Service {
    AppleMusic,
    SoundCloud,
    YouTubeMusic,
}

impl Service {
    pub fn label(self) -> &'static str {
        match self {
            Service::AppleMusic => "Apple Music",
            Service::SoundCloud => "SoundCloud",
            Service::YouTubeMusic => "YouTube Music",
        }
    }

    /// Services whose page titles never contain the playing track (Apple
    /// Music's web player keeps the page name while playing), so a playing
    /// title can only be attributed to them by audible-tab presence.
    pub fn has_untitled_tracks(self) -> bool {
        self == Service::AppleMusic
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Services {
    pub apple_music: bool,
    pub soundcloud: bool,
    pub youtube_music: bool,
}

impl Services {
    pub fn enabled(&self, service: Service) -> bool {
        match service {
            Service::AppleMusic => self.apple_music,
            Service::SoundCloud => self.soundcloud,
            Service::YouTubeMusic => self.youtube_music,
        }
    }
}

/// Page-level playback timing measured by the extension (MusicKit). Only the
/// Apple Music web player sends it.
#[derive(Debug, Clone, PartialEq)]
pub struct Playback {
    pub position: f64,
    pub duration: Option<f64>,
    pub playing: bool,
    pub sampled_at: f64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageMetadata {
    pub title: String,
    pub artist: String,
    pub artwork: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Tab {
    pub host: String,
    pub media_id: Option<String>,
    pub title: String,
    pub playback: Option<Playback>,
    pub metadata: Option<PageMetadata>,
    /// The playing tab's own address, added in v5.
    pub track_url: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Report {
    pub enabled: bool,
    pub services: Services,
    pub tabs: Vec<Tab>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestHead {
    State,
    Report { content_length: usize },
}

// ---------------------------------------------------------------------------
// Strict JSON: like `json.loads` with `object_pairs_hook=_unique_object` and
// `parse_constant=_reject_json_constant`. serde_json already rejects NaN and
// Infinity; duplicate members need a visitor of their own.

struct Strict(Value);

impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(StrictVisitor).map(Strict)
    }
}

struct StrictVisitor;

impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Value;

    fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("a JSON value")
    }

    fn visit_bool<E>(self, v: bool) -> Result<Value, E> {
        Ok(Value::Bool(v))
    }

    fn visit_i64<E>(self, v: i64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_u64<E>(self, v: u64) -> Result<Value, E> {
        Ok(Value::Number(v.into()))
    }

    fn visit_f64<E: de::Error>(self, v: f64) -> Result<Value, E> {
        Number::from_f64(v)
            .map(Value::Number)
            .ok_or_else(|| E::custom("non-finite number"))
    }

    fn visit_str<E>(self, v: &str) -> Result<Value, E> {
        Ok(Value::String(v.to_owned()))
    }

    fn visit_string<E>(self, v: String) -> Result<Value, E> {
        Ok(Value::String(v))
    }

    fn visit_unit<E>(self) -> Result<Value, E> {
        Ok(Value::Null)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut items = Vec::new();
        while let Some(Strict(item)) = seq.next_element()? {
            items.push(item);
        }
        Ok(Value::Array(items))
    }

    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut object = Map::new();
        while let Some(name) = map.next_key::<String>()? {
            if object.contains_key(&name) {
                return Err(de::Error::custom("duplicate JSON object member"));
            }
            let Strict(member) = map.next_value()?;
            object.insert(name, member);
        }
        Ok(Value::Object(object))
    }
}

// ---------------------------------------------------------------------------
// Small helpers that mirror Python's semantics.

/// `len(str)` in Python counts code points, not bytes.
fn char_len(value: &str) -> usize {
    value.chars().count()
}

fn key_set(object: &Map<String, Value>) -> HashSet<&str> {
    object.keys().map(String::as_str).collect()
}

fn is_exactly(object: &Map<String, Value>, keys: &[&str]) -> bool {
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

/// A JSON number that is not a bool; Python's `isinstance(v, (int, float))`
/// with the explicit bool exclusion.
fn playback_number(value: &Value, maximum: f64) -> Option<f64> {
    let number = value.as_f64()?;
    (number.is_finite() && (0.0..=maximum).contains(&number)).then_some(number)
}

/// `(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?`
/// of 1 to 253 characters.
fn is_valid_host(host: &str) -> bool {
    if host.is_empty() || host.len() > MAX_HOST_CHARS {
        return false;
    }
    host.split('.').all(|label| {
        !label.is_empty()
            && label.len() <= 63
            && label
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            && !label.starts_with('-')
            && !label.ends_with('-')
    })
}

fn is_youtube_video_id(value: &str) -> bool {
    value.len() == 11
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// What `urllib.parse.urlsplit` would report for an `https` URL: the
/// lowercased hostname, provided the URL carries no user information.
///
/// Python treats any `@` in the network location as user information, even
/// an empty one, and does not treat a backslash as a path separator, so the
/// raw authority is checked for `@` before the WHATWG parser sees it.
fn https_hostname(value: &str) -> Option<String> {
    let (scheme, rest) = value.split_once("://")?;
    if !scheme.eq_ignore_ascii_case("https") {
        return None;
    }
    let authority_end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    if rest[..authority_end].contains('@') {
        return None;
    }
    let parsed = url::Url::parse(value).ok()?;
    if parsed.scheme() != "https" || !parsed.username().is_empty() || parsed.password().is_some() {
        return None;
    }
    parsed.host_str().map(str::to_ascii_lowercase)
}

// ---------------------------------------------------------------------------
// Report validation.

fn validate_tab_playback(
    tab: &Map<String, Value>,
    host: &str,
) -> Result<Option<Playback>, ProtocolError> {
    let present = PLAYBACK_KEYS
        .iter()
        .filter(|key| tab.contains_key(**key))
        .count();
    if present == 0 {
        return Ok(None);
    }
    if present != PLAYBACK_KEYS.len() {
        return Err(bad("Incomplete tab playback"));
    }
    if host != "music.apple.com" {
        return Err(bad("Unexpected tab playback"));
    }
    let position = playback_number(&tab["position"], MAX_PLAYBACK_SECONDS);
    let sampled_at = playback_number(&tab["sampledAt"], MAX_SAMPLED_AT_MS);
    let (Some(position), Some(sampled_at)) = (position, sampled_at) else {
        return Err(bad("Invalid tab playback"));
    };
    let duration = match &tab["duration"] {
        Value::Null => None,
        value => Some(
            playback_number(value, MAX_PLAYBACK_SECONDS)
                .ok_or_else(|| bad("Invalid tab playback"))?,
        ),
    };
    let Value::Bool(playing) = tab["playing"] else {
        return Err(bad("Invalid tab playback"));
    };
    Ok(Some(Playback {
        position,
        duration,
        playing,
        sampled_at,
    }))
}

fn validate_track_url(
    tab: &Map<String, Value>,
    host: &str,
    version: u8,
) -> Result<Option<String>, ProtocolError> {
    let Some(track_url) = tab.get(TRACK_URL_KEY) else {
        return Ok(None);
    };
    if version < 5 {
        return Err(bad("Unexpected tab URL"));
    }
    let track_url = match track_url {
        Value::Null => return Ok(None),
        Value::String(s) if (1..=MAX_TRACK_URL_CHARS).contains(&char_len(s)) => s,
        _ => return Err(bad("Invalid tab URL")),
    };
    // The tab may only describe where it already is; a link anywhere else
    // would put an address of the page's choosing on the user's profile.
    match https_hostname(track_url) {
        Some(hostname) if hostname == host => Ok(Some(track_url.clone())),
        _ => Err(bad("Invalid tab URL")),
    }
}

fn artwork_host_allowed(service: Service, hostname: &str) -> bool {
    match service {
        Service::AppleMusic => hostname.ends_with(".mzstatic.com"),
        Service::SoundCloud => hostname.ends_with(".sndcdn.com"),
        Service::YouTubeMusic => matches!(
            hostname,
            "i.ytimg.com"
                | "lh3.googleusercontent.com"
                | "yt3.ggpht.com"
                | "yt3.googleusercontent.com"
        ),
    }
}

fn validate_page_metadata(
    tab: &Map<String, Value>,
    host: &str,
) -> Result<Option<PageMetadata>, ProtocolError> {
    let metadata = match tab.get(PAGE_METADATA_KEY) {
        None | Some(Value::Null) => return Ok(None),
        Some(metadata) => metadata,
    };
    let service = service_for_host(host).ok_or_else(|| bad("Unexpected page metadata"))?;
    let metadata = match metadata {
        Value::Object(object) if is_exactly(object, &PAGE_METADATA_KEYS) => object,
        _ => return Err(bad("Invalid page metadata")),
    };
    let title = match &metadata["title"] {
        Value::String(s) if !s.trim().is_empty() && char_len(s) <= MAX_TITLE_CHARS => s,
        _ => return Err(bad("Invalid page title")),
    };
    let artist = match &metadata["artist"] {
        Value::String(s) if char_len(s) <= MAX_TITLE_CHARS => s,
        _ => return Err(bad("Invalid page artist")),
    };
    let artwork = match &metadata["artwork"] {
        Value::Null => None,
        Value::String(s) if (1..=MAX_ARTWORK_URL_CHARS).contains(&char_len(s)) => {
            match https_hostname(s) {
                Some(hostname) if artwork_host_allowed(service, &hostname) => Some(s.clone()),
                _ => return Err(bad("Invalid page artwork")),
            }
        }
        _ => return Err(bad("Invalid page artwork")),
    };
    Ok(Some(PageMetadata {
        title: title.trim().to_owned(),
        artist: artist.trim().to_owned(),
        artwork,
    }))
}

fn validate_tab(value: &Value, version: u8) -> Result<Tab, ProtocolError> {
    let Value::Object(tab) = value else {
        return Err(bad("Invalid tab object"));
    };
    let keys = key_set(tab);
    let required = TAB_KEYS.iter().all(|key| keys.contains(key));
    let known = keys.iter().all(|key| {
        TAB_KEYS.contains(key)
            || PLAYBACK_KEYS.contains(key)
            || *key == PAGE_METADATA_KEY
            || *key == TRACK_URL_KEY
    });
    if !required || !known {
        return Err(bad("Invalid tab object"));
    }

    let host = match &tab["host"] {
        Value::String(s) if (1..=MAX_HOST_CHARS).contains(&char_len(s)) => s,
        _ => return Err(bad("Invalid tab host")),
    };
    let title = match &tab["title"] {
        Value::String(s) if char_len(s) <= MAX_TITLE_CHARS => s,
        _ => return Err(bad("Invalid tab title")),
    };
    if !is_valid_host(host) {
        return Err(bad("Invalid tab host"));
    }
    let media_id = match &tab["mediaId"] {
        Value::Null => None,
        Value::String(s) if (1..=MAX_MEDIA_ID_CHARS).contains(&char_len(s)) => Some(s.clone()),
        _ => return Err(bad("Invalid tab media ID")),
    };
    if host == "music.youtube.com" {
        if media_id
            .as_deref()
            .is_some_and(|id| !is_youtube_video_id(id))
        {
            return Err(bad("Invalid YouTube Music video ID"));
        }
    } else if media_id.is_some() {
        return Err(bad("Unexpected tab media ID"));
    }

    Ok(Tab {
        playback: validate_tab_playback(tab, host)?,
        metadata: validate_page_metadata(tab, host)?,
        track_url: validate_track_url(tab, host, version)?,
        host: host.clone(),
        media_id,
        title: title.clone(),
    })
}

/// Validate a v4 or v5 report and return it with the version it arrived in.
pub fn validate_report(value: &Value) -> Result<(Report, u8), ProtocolError> {
    let Value::Object(object) = value else {
        return Err(bad("Invalid report object"));
    };
    if !is_exactly(object, &REPORT_KEYS) {
        return Err(bad("Invalid report object"));
    }
    // `as_u64` is `None` for 5.0 and for booleans, as Python's
    // `type(v) is int` check requires.
    let version = match object["protocol"].as_u64() {
        Some(v)
            if SUPPORTED_PROTOCOL_VERSIONS
                .iter()
                .any(|s| u64::from(*s) == v) =>
        {
            v as u8
        }
        _ => return Err(bad("Invalid report object")),
    };
    let Value::Bool(enabled) = object["enabled"] else {
        return Err(bad("Invalid enabled value"));
    };

    let services = match &object["services"] {
        Value::Object(s) if is_exactly(s, &SERVICE_KEYS) => s,
        _ => return Err(bad("Invalid services object")),
    };
    let flag = |name: &str| match services[name] {
        Value::Bool(b) => Ok(b),
        _ => Err(bad("Invalid service value")),
    };
    let services = Services {
        apple_music: flag("appleMusic")?,
        soundcloud: flag("soundcloud")?,
        youtube_music: flag("youtubeMusic")?,
    };

    let tabs = match &object["tabs"] {
        Value::Array(tabs) if tabs.len() <= MAX_TABS => tabs,
        _ => return Err(bad("Invalid tabs value")),
    };
    let tabs = tabs
        .iter()
        .map(|tab| validate_tab(tab, version))
        .collect::<Result<Vec<_>, _>>()?;

    Ok((
        Report {
            enabled,
            services,
            tabs,
        },
        version,
    ))
}

pub fn parse_report_body(body: &[u8]) -> Result<(Report, u8), ProtocolError> {
    if body.is_empty() || body.len() > MAX_BODY_BYTES {
        return Err(bad("Invalid body size"));
    }
    let text = std::str::from_utf8(body).map_err(|_| bad("Invalid JSON"))?;
    let Strict(value) = serde_json::from_str(text).map_err(|_| bad("Invalid JSON"))?;
    validate_report(&value)
}

// ---------------------------------------------------------------------------
// HTTP request head.

fn is_header_token(name: &str) -> bool {
    !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"!#$%&'*+.^_`|~-".contains(&b))
}

/// Parse a complete HTTP header block without its final CRLFCRLF.
pub fn parse_request_head(raw_head: &[u8]) -> Result<RequestHead, ProtocolError> {
    if raw_head.is_empty() || raw_head.len() > MAX_HEADER_BYTES {
        return Err(ProtocolError::new(431, "Request headers too large"));
    }
    if !raw_head.is_ascii() {
        return Err(bad("Invalid request headers"));
    }
    let text = std::str::from_utf8(raw_head).map_err(|_| bad("Invalid request headers"))?;

    let mut lines = text.split("\r\n");
    let parts: Vec<&str> = lines.next().unwrap_or_default().split(' ').collect();
    if parts.len() != 3 || !matches!(parts[2], "HTTP/1.0" | "HTTP/1.1") {
        return Err(bad("Invalid request line"));
    }
    let (method, path) = (parts[0], parts[1]);

    let mut headers = std::collections::HashMap::new();
    for line in lines {
        if line.is_empty() || line.starts_with([' ', '\t']) || !line.contains(':') {
            return Err(bad("Invalid request headers"));
        }
        let (name, value) = line.split_once(':').unwrap();
        if !is_header_token(name) {
            return Err(bad("Invalid request headers"));
        }
        let name = name.to_ascii_lowercase();
        if headers.contains_key(&name) || value.bytes().any(|b| b < 0x20 || b == 0x7F) {
            return Err(bad("Invalid request headers"));
        }
        headers.insert(name, value.trim().to_owned());
    }
    let header = |name: &str| headers.get(name).map(String::as_str);

    let host = header("host").unwrap_or_default().to_ascii_lowercase();
    if !matches!(
        host.as_str(),
        "127.0.0.1" | "127.0.0.1:52846" | "localhost" | "localhost:52846" | "[::1]" | "[::1]:52846"
    ) {
        return Err(ProtocolError::new(403, "Invalid host"));
    }
    if header("transfer-encoding").is_some() || header("content-encoding").is_some() {
        return Err(bad("Encoded request bodies are not supported"));
    }
    if header("expect").is_some() {
        return Err(bad("Expect is not supported"));
    }

    if method == "GET" && path == "/state" {
        if header("content-length").unwrap_or("0") != "0" {
            return Err(bad("GET body is not supported"));
        }
        return Ok(RequestHead::State);
    }

    if path != "/tabs" {
        return Err(ProtocolError::new(404, "Not found"));
    }
    if method != "POST" {
        return Err(ProtocolError::new(405, "Method not allowed"));
    }
    if !header("content-type")
        .unwrap_or_default()
        .trim()
        .eq_ignore_ascii_case("application/json")
    {
        return Err(ProtocolError::new(
            415,
            "Content-Type must be application/json",
        ));
    }
    let length = header("content-length").unwrap_or_default();
    if length.is_empty() || !length.bytes().all(|b| b.is_ascii_digit()) {
        return Err(ProtocolError::new(411, "Content-Length is required"));
    }
    match length.parse::<usize>() {
        Ok(n) if (1..=MAX_BODY_BYTES).contains(&n) => Ok(RequestHead::Report { content_length: n }),
        _ => Err(ProtocolError::new(413, "Request body too large")),
    }
}

// ---------------------------------------------------------------------------
// Service policy.

pub fn report_is_fresh(reported_at: f64, now: f64) -> bool {
    if reported_at == 0.0 {
        return false;
    }
    let age = now - reported_at;
    (0.0..=REPORT_TTL_SECONDS).contains(&age)
}

pub fn service_for_host(host: &str) -> Option<Service> {
    let normalized = host.trim().to_lowercase();
    let normalized = normalized.trim_end_matches('.');
    match normalized {
        "music.apple.com" => Some(Service::AppleMusic),
        "soundcloud.com" => Some(Service::SoundCloud),
        _ if normalized.ends_with(".soundcloud.com") => Some(Service::SoundCloud),
        "music.youtube.com" => Some(Service::YouTubeMusic),
        _ => None,
    }
}

pub fn service_label_for_host<'a>(host: &str, fallback: &'a str) -> &'a str {
    match service_for_host(host) {
        Some(service) => service.label(),
        None => fallback,
    }
}

pub fn service_is_enabled(report: Option<&Report>, host: &str) -> bool {
    match report {
        Some(report) if report.enabled => {
            service_for_host(host).is_some_and(|service| report.services.enabled(service))
        }
        _ => false,
    }
}

pub fn enabled_tabs(report: Option<&Report>) -> Vec<&Tab> {
    match report {
        Some(r) if r.enabled => r
            .tabs
            .iter()
            .filter(|tab| service_is_enabled(report, &tab.host))
            .collect(),
        _ => Vec::new(),
    }
}

/// True when the browser is audibly playing something that must not be
/// published: a non-music tab (e.g. a regular YouTube video) or a service the
/// user disabled.
pub fn has_unpublishable_audible_tab(report: Option<&Report>) -> bool {
    match report {
        Some(r) if r.enabled => r
            .tabs
            .iter()
            .any(|tab| !service_is_enabled(report, &tab.host)),
        _ => false,
    }
}

/// Tab to attribute a playing title that matches no reported tab title.
pub fn untitled_service_tab(report: Option<&Report>) -> Option<&Tab> {
    enabled_tabs(report)
        .into_iter()
        .find(|tab| service_for_host(&tab.host).is_some_and(Service::has_untitled_tracks))
}

pub fn is_browser_source(source: &str) -> bool {
    let normalized = source.to_lowercase();
    BROWSER_SOURCE_MARKERS
        .iter()
        .any(|marker| normalized.contains(marker))
}

/// Apply a fresh extension report to browser media only.
pub fn browser_track_is_allowed(source: &str, report: Option<&Report>, host: Option<&str>) -> bool {
    if !is_browser_source(source) || report.is_none() {
        return true;
    }
    host.is_some_and(|host| service_is_enabled(report, host))
}

pub fn safe_public_state(report: Option<&Report>, fresh: bool) -> Value {
    match report {
        Some(r) if fresh => serde_json::json!({
            "fresh": true,
            "enabled": r.enabled,
            "services": {
                "appleMusic": r.services.apple_music,
                "soundcloud": r.services.soundcloud,
                "youtubeMusic": r.services.youtube_music,
            },
        }),
        _ => serde_json::json!({
            "fresh": false,
            "enabled": false,
            "services": {"appleMusic": false, "soundcloud": false, "youtubeMusic": false},
        }),
    }
}

#[cfg(test)]
mod tests;
