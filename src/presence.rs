//! Turns the Windows media session and Chune ID's reports into the Discord
//! "Listening to" activity.
//!
//! A port of the decision logic in `presence.py`: fallback tracks, the Apple
//! Music timing workarounds, artwork and iTunes lookups, presence buttons,
//! and the poll step that decides whether to publish, re-send or clear. The
//! Windows media session, Discord IPC and the extension endpoint live in
//! `media`, `discord` and `tab_server`; nothing here touches the OS, so every
//! rule is tested on any host.

use std::path::Path;

use serde::Deserialize;

use crate::attribution::{self, normalize_title};
use crate::discord::Activity;
use crate::protocol::{self, PageMetadata, Report, Service, Tab};

pub const POLL_SECONDS: u64 = 5;
/// Discord allows two presence buttons, each with a label of at most 32
/// characters and an http(s) URL, and shows them to everyone but the
/// listener.
pub const MAX_BUTTON_LABEL_CHARS: usize = 32;
pub const GET_CHUNES_LABEL: &str = "Get Chunes";
pub const EXTENSION_LISTING_URL: &str =
    "https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd";
const MAX_ACTIVITY_TEXT_CHARS: usize = 128;
/// How many tracks each timing map remembers.
const MAX_TRACKED: usize = 100;
const MAX_ARTWORK_CACHE: usize = 500;
/// Re-send an unchanged activity this often: Discord forgets it if the client
/// reloads, and a dead pipe is only noticed on the next write.
const RESEND_SECONDS: f64 = 60.0;
/// A newly seen Apple Music track is treated as a gapless continuation of the
/// previous one (back-dating its start to when that track was due to end)
/// only when the prediction lands within this many seconds of now. A larger
/// gap means a skip, a pause between tracks, or the first song of a session,
/// which fall back to a fresh anchor.
pub const APPLE_GAPLESS_MARGIN_SECONDS: f64 = 12.0;
/// A MusicKit sample older than this is treated as gone (tab closed,
/// extension reloaded, or the page wedged) and the media-session anchor
/// workaround takes over. The extension only pushes a report when playback
/// changes meaningfully, so a steadily playing sample is normally up to a
/// full report period (~30s) old and extrapolates accurately; a couple of
/// missed periods means trouble. Small negative ages are tolerated: the
/// sample is taken on the browser's clock a report cycle before we compare it
/// against ours.
pub const APPLE_EXTENSION_TIMING_MAX_AGE_SECONDS: f64 = 75.0;

// ---------------------------------------------------------------------------
// Configuration.

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default)]
pub struct Config {
    pub client_id: String,
    pub sources: Vec<String>,
    pub service_label: String,
    pub image_key: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            client_id: "1527834085383213106".into(),
            sources: ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"]
                .map(String::from)
                .to_vec(),
            service_label: String::new(),
            image_key: String::new(),
        }
    }
}

impl Config {
    /// Defaults overlaid with `config.json`, when one sits beside the
    /// executable. Keys the file leaves out keep their defaults.
    pub fn load(path: &Path) -> Result<Self, String> {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(text.trim_start_matches('\u{feff}'))
                .map_err(|e| format!("{} is not valid: {e}", path.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::default()),
            Err(e) => Err(format!("{} could not be read: {e}", path.display())),
        }
    }
}

/// The per-user switches the tray exposes, read once per publish.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Switches {
    pub artwork: bool,
    pub track_button: bool,
    pub get_chunes_button: bool,
}

impl Default for Switches {
    fn default() -> Self {
        Self {
            artwork: true,
            track_button: true,
            get_chunes_button: false,
        }
    }
}

// ---------------------------------------------------------------------------
// Small insertion-ordered map with a size cap, like the dicts presence.py
// trims with `pop(next(iter(...)))`.

#[derive(Debug, Clone)]
pub struct Capped<K, V> {
    entries: Vec<(K, V)>,
    cap: usize,
}

impl<K: PartialEq, V> Capped<K, V> {
    pub fn new(cap: usize) -> Self {
        Self {
            entries: Vec::new(),
            cap,
        }
    }

    pub fn get(&self, key: &K) -> Option<&V> {
        self.entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
    }

    /// Insert or replace. A replaced key keeps its place in the order, and
    /// the oldest entry goes once the map is over its cap.
    pub fn insert(&mut self, key: K, value: V) {
        if let Some(slot) = self.entries.iter_mut().find(|(k, _)| *k == key) {
            slot.1 = value;
            return;
        }
        self.entries.push((key, value));
        if self.entries.len() > self.cap {
            self.entries.remove(0);
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn contains(&self, key: &K) -> bool {
        self.get(key).is_some()
    }
}

/// `(title, artist)`, the identity every timing map is keyed by.
pub type TrackKey = (String, String);

fn key(title: &str, artist: &str) -> TrackKey {
    (title.to_owned(), artist.to_owned())
}

// ---------------------------------------------------------------------------
// Tracks.

/// What the Windows media session (or a fallback) says is playing.
#[derive(Debug, Clone, PartialEq)]
pub struct Track {
    pub title: String,
    pub artist: String,
    pub position: f64,
    pub duration: f64,
    pub source: String,
}

/// The media session's position is a snapshot taken at its last update, not
/// "now"; browsers refresh it infrequently, so extrapolate it forward.
///
/// `elapsed` is the seconds since that update, or `None` when it could not
/// be read. Only a recent snapshot (< 15s), or one already past the start of
/// the track (> 15s in), is moved: an inactive background tab (e.g. Apple
/// Music) resets the position to 0 on a track change but leaves the update
/// time pointing at when the previous track started.
pub fn extrapolate_position(position: f64, duration: f64, elapsed: Option<f64>) -> f64 {
    let mut position = position;
    if let Some(elapsed) = elapsed {
        if (0.0 < elapsed && elapsed < 15.0)
            || (0.0 < elapsed && elapsed < 3600.0 && position > 15.0)
        {
            position += elapsed;
        }
    }
    if duration > 0.0 && position >= duration {
        position = 0.0;
    }
    position
}

/// A track rebuilt from an audible music tab, for when the media session is
/// unusable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FallbackTrack {
    pub title: String,
    pub artist: String,
    pub host: String,
    pub media_id: Option<String>,
}

fn fallback(title: &str, artist: &str, tab: &Tab) -> FallbackTrack {
    FallbackTrack {
        title: title.to_owned(),
        artist: artist.to_owned(),
        host: tab.host.clone(),
        media_id: tab.media_id.clone(),
    }
}

/// Strip a trailing `| YouTube Music` or `- YouTube Music`, as
/// `re.sub(r"\s*[\|-]\s*YouTube Music$", "", t, flags=re.I)` does.
fn strip_youtube_music_suffix(title: &str) -> &str {
    const SUFFIX: &str = "youtube music";
    let Some(head_len) = title.len().checked_sub(SUFFIX.len()) else {
        return title;
    };
    if !title.is_char_boundary(head_len) || !title[head_len..].eq_ignore_ascii_case(SUFFIX) {
        return title;
    }
    let head = title[..head_len].trim_end();
    match head.strip_suffix(['|', '-']) {
        Some(rest) => rest.trim_end(),
        None => title,
    }
}

/// When the Windows media session is unusable (e.g. a blocked YouTube video
/// holds the browser's only media slot), build track info from the audible
/// music tab's title. No playback position is available this way.
pub fn fallback_track(report: Option<&Report>) -> Option<FallbackTrack> {
    for tab in protocol::enabled_tabs(report) {
        let title = tab.title.trim();
        match protocol::service_for_host(&tab.host) {
            Some(Service::AppleMusic) => {
                // The page title is never the track, so only its own metadata
                // can name what Apple Music is playing.
                if let Some(m) = &tab.metadata {
                    if attribution::tab_reports_playing(tab) {
                        return Some(fallback(&m.title, &m.artist, tab));
                    }
                }
            }
            Some(Service::SoundCloud) => {
                if let Some(m) = &tab.metadata {
                    return Some(fallback(&m.title, &m.artist, tab));
                }
                if let Some((name, artist)) = title.rsplit_once(" by ") {
                    return Some(fallback(name.trim(), artist.trim(), tab));
                }
            }
            Some(Service::YouTubeMusic) => {
                let title = strip_youtube_music_suffix(title).trim();
                if matches!(
                    normalize_title(title).as_str(),
                    "" | "youtube music" | "youtube"
                ) {
                    continue;
                }
                return Some(match title.rsplit_once(" - ") {
                    Some((name, artist)) => fallback(name.trim(), artist.trim(), tab),
                    None => fallback(title, "", tab),
                });
            }
            None => {}
        }
    }
    None
}

/// Build a media-session fallback track, or `None` when it would stall.
///
/// A fallback is used when a non-music tab (e.g. a regular YouTube video) has
/// taken over the browser's single OS media session while a music tab is
/// still audible. A page that measures its own playback supplies the
/// position directly; otherwise it is published only when this track's real
/// position was captured (recorded in `seen`) before the takeover and is
/// still within range, since playback advances 1:1 with real time. Without
/// either there is only a frozen 0:00, so publish nothing.
pub fn fallback_timing(
    fb: &FallbackTrack,
    tab: Option<&Tab>,
    seen: &Capped<TrackKey, (i64, f64)>,
    now: f64,
) -> Option<Track> {
    let source = format!("tab:{}", fb.host);
    let track = |position, duration| Track {
        title: fb.title.clone(),
        artist: fb.artist.clone(),
        position,
        duration,
        source: source.clone(),
    };
    if protocol::service_for_host(&fb.host) == Some(Service::AppleMusic) {
        if let Some((position, duration)) = apple_extension_timing(tab, now) {
            return Some(track(position, duration));
        }
    }
    let &(start, duration) = seen.get(&key(&fb.title, &fb.artist))?;
    let elapsed = now - start as f64;
    (0.0 < elapsed && elapsed < duration + 30.0).then(|| track(elapsed, duration))
}

/// Keep a provider track anchored across stale backward media positions.
pub fn provider_duration_start(
    title: &str,
    artist: &str,
    position: f64,
    duration: f64,
    seen: &Capped<TrackKey, (i64, f64)>,
    now: f64,
) -> i64 {
    if let Some(&(previous_start, _)) = seen.get(&key(title, artist)) {
        let elapsed = now - previous_start as f64;
        // Chromium periodically publishes a low, stale position for a
        // background provider tab. Keeping the known anchor prevents the
        // familiar 20s -> 5s Discord loop without affecting a new title.
        if elapsed < duration + 30.0 && (position <= 0.0 || position < elapsed - 5.0) {
            return previous_start;
        }
    }
    if 0.0 < position && position < duration {
        return (now - position) as i64;
    }
    now as i64
}

/// True when two track titles refer to the same song.
///
/// Equal after normalization, or the shorter is contained in the longer and
/// covers most of it. The coverage floor stops a short unrelated title from
/// substring-matching a longer one (e.g. "Gimme Dat" inside "Gimme Dat
/// Ting"), which would otherwise pull in the wrong track's duration.
pub fn titles_match(query: &str, candidate: &str) -> bool {
    let a = normalize_title(query);
    let b = normalize_title(candidate);
    if a.is_empty() || b.is_empty() {
        return false;
    }
    if a == b {
        return true;
    }
    let (a_len, b_len) = (a.chars().count(), b.chars().count());
    let (shorter, longer, short_len, long_len) = if b_len < a_len {
        (&b, &a, b_len, a_len)
    } else {
        (&a, &b, a_len, b_len)
    };
    longer.contains(shorter.as_str()) && short_len as f64 >= 0.8 * long_len as f64
}

// ---------------------------------------------------------------------------
// Apple Music timing.

/// Discord start epoch for an Apple Music track.
///
/// Apple's web player drives the Windows media session with a queue-wide
/// position counter that does not reset on a track change, so its reported
/// position is not a trustworthy offset into the current song.
///
/// A track already being tracked keeps its anchor. A newly seen track is
/// back-dated to when the previous Apple track was due to end: Apple plays
/// gapless and each track's length is known (the locked iTunes duration), so
/// this recovers the real offset the counter can't give us. With no previous
/// track, or a prediction outside a small window around now (a manual skip,
/// a gap, or the first song), it falls back to a fresh anchor at now.
pub fn apple_track_start(
    track: &TrackKey,
    now: f64,
    anchors: &mut Capped<TrackKey, i64>,
    previous: Option<(i64, f64)>,
) -> i64 {
    if let Some(&start) = anchors.get(track) {
        return start;
    }
    let mut start = now as i64;
    if let Some((previous_start, previous_duration)) = previous {
        let predicted = previous_start as f64 + previous_duration;
        let gap = now - predicted;
        if previous_duration > 0.0 && (0.0..=APPLE_GAPLESS_MARGIN_SECONDS).contains(&gap) {
            start = predicted as i64;
        }
    }
    anchors.insert(track.clone(), start);
    start
}

/// First trusted duration for an Apple Music track, held against changes.
///
/// Apple's self-reported media-session duration reads 0 for the first
/// several seconds and can flip mid-song, so prefer the iTunes Search value
/// and use the media session only when the lookup has none. Once a positive
/// value is recorded it is locked so the progress bar does not jump.
pub fn apple_locked_duration(
    track: &TrackKey,
    session_duration: f64,
    info_duration: f64,
    locks: &mut Capped<TrackKey, f64>,
) -> f64 {
    let locked = locks.get(track).copied().unwrap_or(0.0);
    if locked > 0.0 {
        return locked;
    }
    let duration = if info_duration > 0.0 {
        info_duration
    } else {
        session_duration
    };
    if duration > 0.0 {
        locks.insert(track.clone(), duration);
    }
    duration
}

/// `(position, duration)` measured in-page by the extension, or `None`.
///
/// The extension reads the Apple Music web player's own MusicKit state, the
/// only source that reports real per-track position and duration. A playing
/// sample is extrapolated to now; a paused one is used as-is. A missing or
/// stale sample returns `None` so the caller can fall back to the
/// media-session anchor workaround.
pub fn apple_extension_timing(tab: Option<&Tab>, now: f64) -> Option<(f64, f64)> {
    let playback = tab?.playback.as_ref()?;
    let age = now - playback.sampled_at / 1000.0;
    if !(-5.0..=APPLE_EXTENSION_TIMING_MAX_AGE_SECONDS).contains(&age) {
        return None;
    }
    let advanced = if playback.playing { age.max(0.0) } else { 0.0 };
    Some((
        playback.position + advanced,
        playback.duration.unwrap_or(0.0),
    ))
}

// ---------------------------------------------------------------------------
// Links, buttons and artwork.

/// Percent-encode like Python's `urllib.parse.quote` (safe characters are
/// the unreserved set plus `/`).
pub fn quote(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for byte in text.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' | b'/' => {
                out.push(byte as char)
            }
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// Where a service looks a track up when nothing names it exactly. A search
/// lands the listener on the right song without pretending to be a
/// permalink.
fn track_search_url(service: Service, query: &str) -> String {
    match service {
        Service::AppleMusic => format!("https://music.apple.com/search?term={query}"),
        Service::SoundCloud => format!("https://soundcloud.com/search?q={query}"),
        Service::YouTubeMusic => format!("https://music.youtube.com/search?q={query}"),
    }
}

/// Best available URL for opening the current track on its own service.
///
/// The playing tab knows its own address, so it wins whenever the extension
/// is new enough to report one. Failing that a YouTube Music video ID and an
/// iTunes title match still name a specific track, and everything else falls
/// back to that service's search.
pub fn track_link(
    host: Option<&str>,
    media_id: Option<&str>,
    title: &str,
    artist: &str,
    tab_url: Option<&str>,
    itunes_url: Option<&str>,
) -> Option<String> {
    let service = protocol::service_for_host(host?)?;
    if let Some(url) = tab_url.filter(|u| !u.is_empty()) {
        return Some(url.to_owned());
    }
    if let (Service::YouTubeMusic, Some(id)) = (service, media_id.filter(|m| !m.is_empty())) {
        return Some(format!("https://music.youtube.com/watch?v={}", quote(id)));
    }
    if let (Service::AppleMusic, Some(url)) = (service, itunes_url.filter(|u| !u.is_empty())) {
        return Some(url.to_owned());
    }
    let query = quote(format!("{title} {artist}").trim());
    (!query.is_empty()).then(|| track_search_url(service, &query))
}

fn truncate_chars(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

/// The presence buttons for the current track, in display order.
///
/// Discord shows these to everyone except the listener, so the track button
/// is only offered when a link actually resolves; a button that opens the
/// wrong thing is worse than no button at all.
#[allow(clippy::too_many_arguments)]
pub fn presence_buttons(
    host: Option<&str>,
    media_id: Option<&str>,
    title: &str,
    artist: &str,
    label: &str,
    tab_url: Option<&str>,
    itunes_url: Option<&str>,
    show_track: bool,
    show_get_chunes: bool,
) -> Vec<(String, String)> {
    let mut buttons = Vec::new();
    if show_track && !label.is_empty() {
        if let Some(link) = track_link(host, media_id, title, artist, tab_url, itunes_url) {
            buttons.push((
                truncate_chars(&format!("Play on {label}"), MAX_BUTTON_LABEL_CHARS),
                link,
            ));
        }
    }
    if show_get_chunes {
        buttons.push((GET_CHUNES_LABEL.into(), EXTENSION_LISTING_URL.into()));
    }
    buttons
}

/// The searched track's own Apple Music page, when the API returned one.
///
/// Only the track selector is kept. The Search API appends its own `uo`
/// marker, and this link goes on a public profile rather than into an
/// analytics pipeline.
pub fn apple_music_track_url(value: Option<&str>) -> Option<String> {
    let parsed = url::Url::parse(value.filter(|v| !v.is_empty())?).ok()?;
    if parsed.scheme() != "https" || parsed.host_str() != Some("music.apple.com") {
        return None;
    }
    let track = parsed
        .query_pairs()
        .find(|(name, value)| name == "i" && !value.is_empty())
        .map(|(_, value)| value.into_owned());
    let mut link = format!("https://music.apple.com{}", parsed.path());
    if let Some(track) = track {
        let query: String = url::form_urlencoded::Serializer::new(String::new())
            .append_pair("i", &track)
            .finish();
        link.push('?');
        link.push_str(&query);
    }
    Some(link)
}

/// Artwork, duration and track page for a track.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TrackInfo {
    pub artwork: Option<String>,
    pub duration: f64,
    pub url: Option<String>,
}

/// Fetches a URL's body as text. The real one is `http::get`; tests pass
/// their own.
pub type HttpGet = Box<dyn Fn(&str) -> Result<String, String> + Send>;

pub const BROWSER_USER_AGENT: &str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) \
    AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36";

type ArtworkKey = (
    Option<String>,
    Option<String>,
    String,
    String,
    String,
    Option<PageMetadata>,
);

/// Album artwork and iTunes lookups, cached per track.
pub struct Artwork {
    http_get: HttpGet,
    cache: Capped<ArtworkKey, TrackInfo>,
}

impl Artwork {
    pub fn new(http_get: HttpGet) -> Self {
        Self {
            http_get,
            cache: Capped::new(MAX_ARTWORK_CACHE),
        }
    }

    /// Best-effort Apple Music artwork, duration and track page from the
    /// public iTunes Search API.
    pub fn apple_music_info(&self, title: &str, artist: &str) -> TrackInfo {
        let query = quote(format!("{title} {artist}").trim());
        let url =
            format!("https://itunes.apple.com/search?term={query}&media=music&entity=song&limit=5");
        let data: serde_json::Value = match (self.http_get)(&url)
            .and_then(|body| serde_json::from_str(&body).map_err(|e| e.to_string()))
        {
            Ok(data) => data,
            Err(error) => {
                crate::log!("Apple Music artwork lookup failed: {error}");
                return TrackInfo::default();
            }
        };
        let mut matched: Option<TrackInfo> = None;
        let mut fallback_art: Option<String> = None;
        let results = data
            .get("results")
            .and_then(|r| r.as_array())
            .map(Vec::as_slice)
            .unwrap_or_default();
        for result in results {
            let art = result
                .get("artworkUrl100")
                .and_then(|a| a.as_str())
                .filter(|a| !a.is_empty())
                .map(str::to_owned);
            let name = result
                .get("trackName")
                .and_then(|n| n.as_str())
                .unwrap_or("");
            if titles_match(title, name) {
                let millis = result
                    .get("trackTimeMillis")
                    .and_then(|m| m.as_f64())
                    .unwrap_or(0.0);
                matched = Some(TrackInfo {
                    artwork: art,
                    duration: millis / 1000.0,
                    url: apple_music_track_url(result.get("trackViewUrl").and_then(|u| u.as_str())),
                });
                break;
            }
            if fallback_art.is_none() {
                fallback_art = art;
            }
        }
        // Duration and track page only from a title-matched result; a
        // fallback thumbnail is low harm, a wrong duration paints a wrong
        // progress bar and a wrong link sends listeners to the wrong song.
        let mut info = matched.unwrap_or_default();
        info.artwork = info
            .artwork
            .or(fallback_art)
            .map(|art| art.replace("100x100", "500x500"));
        info
    }

    /// Artwork, duration and track page from page metadata or Apple Search.
    pub fn find(
        &mut self,
        title: &str,
        artist: &str,
        host: Option<&str>,
        media_id: Option<&str>,
        source: &str,
        metadata: Option<&PageMetadata>,
    ) -> TrackInfo {
        let cache_key: ArtworkKey = (
            host.map(str::to_owned),
            media_id.map(str::to_owned),
            source.to_owned(),
            title.to_owned(),
            artist.to_owned(),
            metadata.cloned(),
        );
        if let Some(info) = self.cache.get(&cache_key) {
            return info.clone();
        }
        let is_apple = host.and_then(protocol::service_for_host) == Some(Service::AppleMusic);
        let info = if let Some(metadata) = metadata {
            TrackInfo {
                artwork: metadata.artwork.clone(),
                ..TrackInfo::default()
            }
        } else if is_apple || !protocol::is_browser_source(source) {
            self.apple_music_info(title, artist)
        } else {
            TrackInfo::default()
        };
        self.cache.insert(cache_key, info.clone());
        info
    }
}

// ---------------------------------------------------------------------------
// The poll step.

/// What the presence loop should do after one poll.
#[derive(Debug, Clone, PartialEq)]
pub enum Action {
    /// Publish this activity, and show `status` in the tray.
    Publish {
        activity: Activity,
        status: String,
        host: Option<String>,
    },
    /// Nothing is playing any more: clear the activity and the status.
    Clear,
    /// Leave Discord as it is.
    Keep,
}

#[derive(Debug, Clone, PartialEq)]
struct SentKey {
    title: String,
    artist: String,
    host: Option<String>,
    media_id: Option<String>,
    artwork: bool,
    has_duration: bool,
    track_button: bool,
    get_chunes_button: bool,
    tab_url: Option<String>,
}

/// A publish the engine is waiting to hear back about.
#[derive(Debug, Clone)]
struct Pending {
    key: SentKey,
    start: i64,
    now: f64,
}

/// The state presence.py keeps in `main()`'s locals between polls.
pub struct Engine {
    pub config: Config,
    pub artwork: Artwork,
    /// `(title, artist)` -> `(start epoch, duration)` from real readings.
    seen: Capped<TrackKey, (i64, f64)>,
    /// Anchored start epochs for Apple Music tracks.
    apple_starts: Capped<TrackKey, i64>,
    /// Locked durations for Apple Music tracks.
    apple_durations: Capped<TrackKey, f64>,
    /// The previous Apple track, for gapless start prediction.
    last_apple: Option<TrackKey>,
    /// What Discord was last told: key, start and when.
    last: Option<(SentKey, i64, f64)>,
    pending: Option<Pending>,
}

const GENERIC_TITLES: [&str; 4] = ["youtube music", "soundcloud", "apple music", "youtube"];

impl Engine {
    pub fn new(config: Config, artwork: Artwork) -> Self {
        Self {
            config,
            artwork,
            seen: Capped::new(MAX_TRACKED),
            apple_starts: Capped::new(MAX_TRACKED),
            apple_durations: Capped::new(MAX_TRACKED),
            last_apple: None,
            last: None,
            pending: None,
        }
    }

    /// Whether anything is currently published.
    pub fn is_publishing(&self) -> bool {
        self.last.is_some()
    }

    /// Discord accepted the last `Publish`.
    pub fn published(&mut self) {
        if let Some(Pending { key, start, now }) = self.pending.take() {
            self.last = Some((key, start, now));
        }
    }

    /// Discord was reconnected and has forgotten the activity, so the next
    /// poll publishes again.
    pub fn forget(&mut self) {
        self.last = None;
        self.pending = None;
    }

    /// Decide what Discord should show, given the media session's `track`,
    /// the extension's fresh `report`, and the tray switches.
    pub fn poll(
        &mut self,
        track: Option<Track>,
        report: Option<&Report>,
        switches: Switches,
        now: f64,
    ) -> Action {
        self.pending = None;
        let mut host: Option<String> = None;
        let mut media_id: Option<String> = None;
        let mut tab: Option<&Tab> = None;
        let mut page_metadata: Option<&PageMetadata> = None;
        let mut current: Option<Track> = None;

        if let Some(mut t) = track {
            let mut generic = GENERIC_TITLES.contains(&normalize_title(&t.title).as_str());
            tab = attribution::resolve_tab(&t.title, &t.source, report);
            if let Some(resolved) = tab {
                host = Some(resolved.host.clone());
                media_id = resolved.media_id.clone();
                page_metadata = resolved.metadata.as_ref();
                if let Some((title, artist)) = attribution::page_reported_track(Some(resolved)) {
                    t.title = title.to_owned();
                    t.artist = artist.to_owned();
                    generic = false;
                }
            }
            if host.is_none() {
                if let Some((sent, _, _)) = &self.last {
                    if sent.title == t.title && sent.artist == t.artist {
                        host = sent.host.clone();
                        media_id = sent.media_id.clone();
                    }
                }
            }
            if !generic {
                if protocol::browser_track_is_allowed(&t.source, report, host.as_deref()) {
                    current = Some(t);
                } else if self.last.is_some() {
                    let shown: String = t.title.chars().take(60).collect();
                    crate::log!("Ignoring disabled or non-music browser source: {shown}");
                }
            }
        }

        if current.is_none() {
            // A blocked video may be hogging the browser's only media
            // session; the extension still knows if a music tab is audible.
            if let Some(fb) = fallback_track(report) {
                host = Some(fb.host.clone());
                media_id = fb.media_id.clone();
                // Whatever the media session pointed at, the fallback track
                // comes from the reporting tab found below or from nothing.
                tab = protocol::enabled_tabs(report).into_iter().find(|t| {
                    t.host == fb.host
                        && t.metadata
                            .as_ref()
                            .is_some_and(|m| m.title == fb.title && m.artist == fb.artist)
                });
                if let Some(found) = tab {
                    page_metadata = found.metadata.as_ref();
                }
                current = fallback_timing(&fb, tab, &self.seen, now);
            }
        }

        let Some(track) = current else {
            // No track playing means no gapless continuation to anchor from.
            self.last_apple = None;
            if self.last.take().is_some() {
                crate::log!("Playback stopped, clearing status.");
                return Action::Clear;
            }
            return Action::Keep;
        };
        self.publish_step(track, host, media_id, tab, page_metadata, switches, now)
    }

    #[allow(clippy::too_many_arguments)]
    fn publish_step(
        &mut self,
        track: Track,
        host: Option<String>,
        media_id: Option<String>,
        tab: Option<&Tab>,
        page_metadata: Option<&PageMetadata>,
        switches: Switches,
        now: f64,
    ) -> Action {
        let Track {
            title,
            artist,
            position,
            source,
            ..
        } = track;
        let mut duration = track.duration;
        let track_key = key(&title, &artist);
        let tab_url = tab.and_then(|t| t.track_url.clone());
        let is_apple =
            host.as_deref().and_then(protocol::service_for_host) == Some(Service::AppleMusic);
        let mut extension_timing = None;

        // A fallback track exists only when a real anchor was recovered, so
        // it carries a genuine elapsed position; anchor its start to wall
        // clock the same way a normal browser track is anchored.
        let mut start = if source.starts_with("tab:") {
            (now - position) as i64
        } else if is_apple {
            extension_timing = apple_extension_timing(tab, now);
            if let Some((position, measured)) = extension_timing {
                // The extension read the real position from the page's
                // MusicKit player; anchor directly to it. Keeping the anchor
                // and lock maps current means a lost sample later hands the
                // media-session workaround an accurate starting point.
                let start = (now - position) as i64;
                self.apple_starts.insert(track_key.clone(), start);
                if measured > 0.0 {
                    self.apple_durations.insert(track_key.clone(), measured);
                }
                start
            } else {
                // Apple's queue-wide position counter is not a reliable
                // offset into the current song; anchor to first-seen wall
                // clock, back-dated to the previous track's end on a gapless
                // change.
                let previous = self
                    .last_apple
                    .as_ref()
                    .filter(|previous| **previous != track_key)
                    .and_then(|previous| {
                        let start = *self.apple_starts.get(previous)?;
                        let duration = self.apple_durations.get(previous).copied().unwrap_or(0.0);
                        Some((start, duration))
                    });
                apple_track_start(&track_key, now, &mut self.apple_starts, previous)
            }
        } else if duration > 0.0 {
            provider_duration_start(&title, &artist, position, duration, &self.seen, now)
        } else {
            (now - position) as i64
        };
        self.last_apple = is_apple.then(|| track_key.clone());
        if start > 0 {
            self.seen.insert(track_key.clone(), (start, duration));
        }

        // Re-send only on track change or a seek (start moved by more than a
        // few seconds); Discord drops clients that spam SET_ACTIVITY every
        // poll. The iTunes link is only resolved further down, but it
        // follows from the title and artist already keyed here; the tab's
        // own address is the one link input that can change on its own.
        let sent_key = SentKey {
            title: title.clone(),
            artist: artist.clone(),
            host: host.clone(),
            media_id: media_id.clone(),
            artwork: switches.artwork,
            has_duration: duration > 0.0,
            track_button: switches.track_button,
            get_chunes_button: switches.get_chunes_button,
            tab_url: tab_url.clone(),
        };
        if let Some((last_key, last_start, last_at)) = &self.last {
            if *last_key == sent_key
                && (start - last_start).abs() <= 4
                && now - last_at < RESEND_SECONDS
            {
                return Action::Keep;
            }
        }
        let new_track = self
            .last
            .as_ref()
            .is_none_or(|(k, _, _)| k.title != title || k.artist != artist);
        if new_track {
            crate::log!("Now playing: {title} - {artist} ({source})");
        }

        let info = if switches.artwork {
            self.artwork.find(
                &title,
                &artist,
                host.as_deref(),
                media_id.as_deref(),
                &source,
                page_metadata,
            )
        } else {
            TrackInfo::default()
        };
        if is_apple {
            match extension_timing {
                // MusicKit reports the track's real duration directly.
                Some((_, measured)) if measured > 0.0 => duration = measured,
                // The media session's Apple duration is late and flips
                // mid-song; lock the first trusted value (iTunes-preferred)
                // without moving the anchored start.
                _ => {
                    duration = apple_locked_duration(
                        &track_key,
                        duration,
                        info.duration,
                        &mut self.apple_durations,
                    )
                }
            }
        } else if duration <= 0.0 && info.duration > 0.0 {
            duration = info.duration;
            // A background browser tab can expose a real track but only a
            // zero position. Keep its first provider-backed anchor instead of
            // resetting Discord to 0:00 each poll.
            start = provider_duration_start(&title, &artist, position, duration, &self.seen, now);
        }

        let label = protocol::service_label_for_host(
            host.as_deref().unwrap_or(""),
            &self.config.service_label,
        )
        .to_owned();
        let large_image = info
            .artwork
            .clone()
            .or_else(|| (!self.config.image_key.is_empty()).then(|| self.config.image_key.clone()));
        let activity = Activity {
            details: truncate_chars(&title, MAX_ACTIVITY_TEXT_CHARS),
            state: (!artist.is_empty())
                .then(|| truncate_chars(&format!("by {artist}"), MAX_ACTIVITY_TEXT_CHARS)),
            start: (start > 0).then_some(start),
            end: (start > 0 && duration > 0.0).then_some((start as f64 + duration) as i64),
            large_image,
            large_text: (!label.is_empty()).then(|| label.clone()),
            buttons: presence_buttons(
                host.as_deref(),
                media_id.as_deref(),
                &title,
                &artist,
                &label,
                tab_url.as_deref(),
                info.url.as_deref(),
                switches.track_button,
                switches.get_chunes_button,
            ),
        };
        self.pending = Some(Pending {
            key: sent_key,
            start,
            now,
        });
        let status = if artist.is_empty() {
            title
        } else {
            format!("{title} - {artist}")
        };
        Action::Publish {
            activity,
            status,
            host,
        }
    }
}

#[cfg(test)]
mod tests;
