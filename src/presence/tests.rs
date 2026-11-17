//! Ported from `tests/test_presence.py`. The `ResolveTabTests` cases live in
//! `attribution.rs`, the Discord framing cases in `discord.rs`, and the
//! endpoint cases in `tab_server.rs`.

use std::sync::{Arc, Mutex};

use super::*;
use crate::protocol::{Playback, Services};

fn tab(host: &str, media_id: Option<&str>, title: &str) -> Tab {
    Tab {
        host: host.into(),
        media_id: media_id.map(Into::into),
        title: title.into(),
        playback: None,
        metadata: None,
        track_url: None,
    }
}

fn meta(title: &str, artist: &str, artwork: Option<&str>) -> PageMetadata {
    PageMetadata {
        title: title.into(),
        artist: artist.into(),
        artwork: artwork.map(Into::into),
    }
}

fn report(services: Services, tabs: Vec<Tab>) -> Report {
    Report {
        enabled: true,
        services,
        tabs,
    }
}

const ALL: Services = Services {
    apple_music: true,
    soundcloud: true,
    youtube_music: true,
};

fn fb(title: &str, artist: &str, host: &str, media_id: Option<&str>) -> FallbackTrack {
    FallbackTrack {
        title: title.into(),
        artist: artist.into(),
        host: host.into(),
        media_id: media_id.map(Into::into),
    }
}

fn no_http() -> HttpGet {
    Box::new(|url| panic!("unexpected request to {url}"))
}

/// An `HttpGet` that answers `body` and records every URL it was asked for.
fn canned(body: Result<&str, &str>) -> (HttpGet, Arc<Mutex<Vec<String>>>) {
    let calls = Arc::new(Mutex::new(Vec::new()));
    let log = calls.clone();
    let body = body.map(str::to_owned).map_err(str::to_owned);
    let get: HttpGet = Box::new(move |url| {
        log.lock().unwrap().push(url.to_owned());
        body.clone()
    });
    (get, calls)
}

// --- DesktopProtocolTests ----------------------------------------------------

#[test]
fn test_page_metadata_owns_track_identity() {
    let mut t = tab(
        "music.youtube.com",
        Some("a1B2c3D4e5F"),
        "Previous Song | YouTube Music",
    );
    t.metadata = Some(meta("Next Song", "Next Artist", None));
    assert_eq!(
        attribution::page_reported_track(Some(&t)),
        Some(("Next Song", "Next Artist"))
    );
    t.metadata = None;
    assert_eq!(attribution::page_reported_track(Some(&t)), None);
    assert_eq!(attribution::page_reported_track(None), None);
}

#[test]
fn test_classification_fallback_and_labels_cover_both_services() {
    let services = Services {
        apple_music: false,
        soundcloud: true,
        youtube_music: true,
    };
    let cloud = tab("soundcloud.com", None, "Cloud Song by Cloud Artist");
    let video = tab(
        "music.youtube.com",
        Some("a1B2c3D4e5F"),
        "Video Song | YouTube Music",
    );
    let r = report(services, vec![cloud, video.clone()]);
    let host = |title| attribution::classify_tab(title, Some(&r)).map(|t| t.host.as_str());
    assert_eq!(host("Cloud Song"), Some("soundcloud.com"));
    assert_eq!(host("Video Song"), Some("music.youtube.com"));
    assert_eq!(
        fallback_track(Some(&r)),
        Some(fb("Cloud Song", "Cloud Artist", "soundcloud.com", None))
    );
    let youtube = report(services, vec![video]);
    assert_eq!(
        fallback_track(Some(&youtube)),
        Some(fb(
            "Video Song",
            "",
            "music.youtube.com",
            Some("a1B2c3D4e5F")
        ))
    );
    let dashed = report(
        services,
        vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "My Track - My Artist - YouTube Music",
        )],
    );
    assert_eq!(
        fallback_track(Some(&dashed)),
        Some(fb(
            "My Track",
            "My Artist",
            "music.youtube.com",
            Some("a1B2c3D4e5F")
        ))
    );
    assert_eq!(
        protocol::service_label_for_host("soundcloud.com", ""),
        "SoundCloud"
    );
    assert_eq!(
        protocol::service_label_for_host("music.youtube.com", ""),
        "YouTube Music"
    );
}

#[test]
fn test_apple_music_is_attributed_by_tab_presence_not_title() {
    let mut r = report(
        ALL,
        vec![tab(
            "music.apple.com",
            None,
            "Album Name - Album by Some Artist - Apple Music",
        )],
    );
    // The web player keeps the page name while playing, so the playing title
    // never matches the tab title and no fallback track can be rebuilt from
    // it; attribution comes from the audible tab instead.
    assert_eq!(attribution::classify_tab("Real Song", Some(&r)), None);
    assert_eq!(fallback_track(Some(&r)), None);
    assert_eq!(protocol::untitled_service_tab(Some(&r)), Some(&r.tabs[0]));
    assert_eq!(
        protocol::service_label_for_host("music.apple.com", ""),
        "Apple Music"
    );
    r.services.apple_music = false;
    assert_eq!(protocol::untitled_service_tab(Some(&r)), None);
}

#[test]
fn test_fallback_ignores_youtube_music_generic_placeholder_title() {
    // The tab title lags a beat behind real playback after switching
    // tracks/providers; until it updates past the bare "YouTube Music"
    // placeholder, fallback_track must not publish it as a fake track.
    let services = Services {
        youtube_music: true,
        ..Services::default()
    };
    let generic = report(
        services,
        vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "YouTube Music",
        )],
    );
    assert_eq!(fallback_track(Some(&generic)), None);
    let real = report(
        services,
        vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "Real Song | YouTube Music",
        )],
    );
    assert_eq!(
        fallback_track(Some(&real)),
        Some(fb(
            "Real Song",
            "",
            "music.youtube.com",
            Some("a1B2c3D4e5F")
        ))
    );
}

#[test]
fn test_fallback_switches_cleanly_from_soundcloud_to_youtube_music() {
    // Reproduces a provider switch: SoundCloud playing, then stopped and
    // replaced by an audible YouTube Music tab whose title hasn't caught up
    // yet. No stale SoundCloud data or YTM placeholder junk should leak into
    // the result at either step.
    let services = Services {
        soundcloud: true,
        youtube_music: true,
        ..Services::default()
    };
    let cloud = report(
        services,
        vec![tab("soundcloud.com", None, "Cloud Song by Cloud Artist")],
    );
    assert_eq!(
        fallback_track(Some(&cloud)),
        Some(fb("Cloud Song", "Cloud Artist", "soundcloud.com", None))
    );
    let mid_switch = report(
        services,
        vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "YouTube Music",
        )],
    );
    assert_eq!(fallback_track(Some(&mid_switch)), None);
    let settled = report(
        services,
        vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "New Song | YouTube Music",
        )],
    );
    assert_eq!(
        fallback_track(Some(&settled)),
        Some(fb("New Song", "", "music.youtube.com", Some("a1B2c3D4e5F")))
    );
}

// --- ArtworkTests -----------------------------------------------------------

#[test]
fn test_page_metadata_artwork_needs_no_desktop_network_request() {
    let artwork = "https://i.ytimg.com/vi/a1B2c3D4e5F/hqdefault.jpg";
    let mut lookups = Artwork::new(no_http());
    let info = lookups.find(
        "Track",
        "Artist",
        Some("music.youtube.com"),
        Some("a1B2c3D4e5F"),
        "",
        Some(&meta("Track", "Artist", Some(artwork))),
    );
    assert_eq!(info.artwork.as_deref(), Some(artwork));
}

#[test]
fn test_apple_music_artwork_comes_from_itunes_search_only() {
    let small = "https://is1-ssl.mzstatic.com/image/thumb/Music/cover/100x100bb.jpg";
    let body = format!(r#"{{"results":[{{"trackName":"Real Song","artworkUrl100":"{small}"}}]}}"#);
    let (get, calls) = canned(Ok(&body));
    let info = Artwork::new(get).find(
        "Real Song",
        "Some Artist",
        Some("music.apple.com"),
        None,
        "brave",
        None,
    );
    assert_eq!(
        info.artwork.as_deref(),
        Some("https://is1-ssl.mzstatic.com/image/thumb/Music/cover/500x500bb.jpg")
    );
    assert_eq!(
        calls.lock().unwrap().as_slice(),
        ["https://itunes.apple.com/search?term=Real%20Song%20Some%20Artist&media=music&entity=song&limit=5"]
    );
}

#[test]
fn test_get_playing_track_ignores_stale_last_updated_time() {
    // The media session reports position 0 on a 228s track, last updated 5
    // minutes ago (a background tab that just changed track).
    assert_eq!(extrapolate_position(0.0, 228.0, Some(300.0)), 0.0);
    // A fresh snapshot moves forward, as does one already mid-track.
    assert_eq!(extrapolate_position(10.0, 228.0, Some(4.0)), 14.0);
    assert_eq!(extrapolate_position(60.0, 228.0, Some(40.0)), 100.0);
    // Never past the end, and an unreadable update time changes nothing.
    assert_eq!(extrapolate_position(220.0, 228.0, Some(10.0)), 0.0);
    assert_eq!(extrapolate_position(42.0, 228.0, None), 42.0);
}

#[test]
fn test_apple_music_artwork_failure_yields_no_art() {
    let (get, _) = canned(Err("down"));
    let info = Artwork::new(get).find(
        "Real Song",
        "Some Artist",
        Some("music.apple.com"),
        None,
        "brave",
        None,
    );
    assert_eq!(info, TrackInfo::default());
}

#[test]
fn test_unidentified_browser_track_makes_no_lookup() {
    let info =
        Artwork::new(no_http()).find("Video", "Channel", None, None, "Google.Chrome_123", None);
    assert_eq!(info.artwork, None);
}

#[test]
fn test_desktop_player_uses_itunes_and_is_cached() {
    let (get, calls) = canned(Ok(r#"{"results":[]}"#));
    let mut lookups = Artwork::new(get);
    for _ in 0..3 {
        lookups.find("Song", "Artist", None, None, "Spotify.exe", None);
    }
    assert_eq!(calls.lock().unwrap().len(), 1);
}

// --- TitleMatchTests --------------------------------------------------------

#[test]
fn test_exact_and_normalized_titles_match() {
    assert!(titles_match("Real Song", "real song"));
    assert!(titles_match("It's Mine", "It\u{2019}s Mine"));
}

#[test]
fn test_high_coverage_substring_matches() {
    // A media session reports "HUMBLE" for the track spelled "HUMBLE.": the
    // shorter covers 6/7 of the longer, above the floor.
    assert!(titles_match("HUMBLE", "HUMBLE."));
}

#[test]
fn test_low_coverage_substring_is_rejected() {
    // "Gimme Dat" covers only 0.64 of "Gimme Dat Ting".
    assert!(!titles_match("Gimme Dat Ting", "Gimme Dat"));
}

#[test]
fn test_unrelated_titles_do_not_match() {
    assert!(!titles_match("365", "party 4 u"));
}

#[test]
fn test_empty_titles_never_match() {
    assert!(!titles_match("", "Song"));
    assert!(!titles_match("Song", ""));
}

// --- TrackLinkTests ---------------------------------------------------------

#[test]
fn test_the_reported_tab_address_wins() {
    // A v5 extension knows exactly where the track is; nothing derived can
    // beat that.
    assert_eq!(
        track_link(
            Some("music.youtube.com"),
            Some("a1B2c3D4e5F"),
            "Song",
            "Artist",
            Some("https://music.youtube.com/watch?v=a1B2c3D4e5F&list=X"),
            None,
        )
        .as_deref(),
        Some("https://music.youtube.com/watch?v=a1B2c3D4e5F&list=X")
    );
}

#[test]
fn test_youtube_music_video_id_names_the_track() {
    assert_eq!(
        track_link(
            Some("music.youtube.com"),
            Some("a1B2c3D4e5F"),
            "Song",
            "Artist",
            None,
            None
        )
        .as_deref(),
        Some("https://music.youtube.com/watch?v=a1B2c3D4e5F")
    );
}

#[test]
fn test_apple_music_prefers_the_itunes_match_over_a_search() {
    let matched = "https://music.apple.com/us/album/real-song/1?i=2";
    assert_eq!(
        track_link(
            Some("music.apple.com"),
            None,
            "Real Song",
            "Artist",
            None,
            Some(matched)
        )
        .as_deref(),
        Some(matched)
    );
    // With album art lookup off there is no iTunes call to learn from.
    assert_eq!(
        track_link(
            Some("music.apple.com"),
            None,
            "Real Song",
            "Artist",
            None,
            None
        )
        .as_deref(),
        Some("https://music.apple.com/search?term=Real%20Song%20Artist")
    );
}

#[test]
fn test_soundcloud_falls_back_to_a_search() {
    assert_eq!(
        track_link(Some("soundcloud.com"), None, "Song", "Artist", None, None).as_deref(),
        Some("https://soundcloud.com/search?q=Song%20Artist")
    );
    // A YouTube Music tab with no video ID searches the same way.
    assert_eq!(
        track_link(
            Some("music.youtube.com"),
            None,
            "Song",
            "Artist",
            None,
            None
        )
        .as_deref(),
        Some("https://music.youtube.com/search?q=Song%20Artist")
    );
}

#[test]
fn test_query_characters_are_escaped() {
    assert_eq!(
        track_link(
            Some("soundcloud.com"),
            None,
            "Rock & Roll?",
            "A/B",
            None,
            None
        )
        .as_deref(),
        Some("https://soundcloud.com/search?q=Rock%20%26%20Roll%3F%20A/B")
    );
}

#[test]
fn test_an_unknown_host_gets_no_link() {
    // A desktop player carries the config's own service label, which says
    // nothing about where the track can be opened.
    assert_eq!(track_link(None, None, "Song", "Artist", None, None), None);
    assert_eq!(
        track_link(Some("example.com"), None, "Song", "Artist", None, None),
        None
    );
}

#[test]
fn test_a_nameless_track_gets_no_link() {
    assert_eq!(
        track_link(Some("soundcloud.com"), None, "", "", None, None),
        None
    );
}

#[test]
fn test_the_apple_link_keeps_only_the_track_selector() {
    // The Search API appends its own "uo" marker; this link goes on a public
    // profile, not into an analytics pipeline.
    let cases = [
        (
            Some("https://music.apple.com/us/album/x/1?i=2&uo=4"),
            Some("https://music.apple.com/us/album/x/1?i=2"),
        ),
        (
            Some("https://music.apple.com/us/album/x/1?uo=4"),
            Some("https://music.apple.com/us/album/x/1"),
        ),
        (
            Some("https://music.apple.com/us/song/x/1"),
            Some("https://music.apple.com/us/song/x/1"),
        ),
        (Some("http://music.apple.com/us/album/x/1?i=2"), None),
        (Some("https://evil.example.com/us/album/x/1?i=2"), None),
        (Some(""), None),
        (None, None),
    ];
    for (raw, expected) in cases {
        assert_eq!(apple_music_track_url(raw).as_deref(), expected, "{raw:?}");
    }
}

// --- PresenceButtonTests ----------------------------------------------------

#[test]
fn test_track_button_is_labelled_for_its_service() {
    assert_eq!(
        presence_buttons(
            Some("soundcloud.com"),
            None,
            "Song",
            "Artist",
            "SoundCloud",
            None,
            None,
            true,
            false
        ),
        vec![(
            "Play on SoundCloud".to_owned(),
            "https://soundcloud.com/search?q=Song%20Artist".to_owned()
        )]
    );
}

#[test]
fn test_labels_stay_within_the_discord_limit() {
    // A config may set any service label it likes for a desktop player.
    let buttons = presence_buttons(
        Some("music.youtube.com"),
        Some("a1B2c3D4e5F"),
        "Song",
        "Artist",
        "A Very Long Music Service Name Indeed",
        None,
        None,
        true,
        false,
    );
    assert_eq!(buttons[0].0.chars().count(), MAX_BUTTON_LABEL_CHARS);
}

#[test]
fn test_no_buttons_when_both_are_switched_off() {
    assert!(presence_buttons(
        Some("soundcloud.com"),
        None,
        "Song",
        "Artist",
        "SoundCloud",
        None,
        None,
        false,
        false
    )
    .is_empty());
}

#[test]
fn test_the_get_chunes_button_is_opt_in_and_comes_second() {
    let buttons = presence_buttons(
        Some("soundcloud.com"),
        None,
        "Song",
        "Artist",
        "SoundCloud",
        None,
        None,
        true,
        true,
    );
    assert_eq!(buttons.len(), 2);
    assert_eq!(buttons[1].0, "Get Chunes");
    assert_eq!(buttons[1].1, EXTENSION_LISTING_URL);
}

#[test]
fn test_no_track_button_without_a_link_or_a_label() {
    // An unknown host resolves no link, and a desktop source with no
    // configured label has nothing to name.
    assert!(presence_buttons(
        Some("example.com"),
        None,
        "Song",
        "Artist",
        "Winamp",
        None,
        None,
        true,
        false
    )
    .is_empty());
    assert!(presence_buttons(
        Some("soundcloud.com"),
        None,
        "Song",
        "Artist",
        "",
        None,
        None,
        true,
        false
    )
    .is_empty());
}

// --- ProviderDurationGuardTests ---------------------------------------------

#[test]
fn test_apple_duration_not_taken_from_unmatched_result() {
    // The only result's title matches too loosely to trust its length; its
    // artwork may still be adopted, its duration must not be.
    let (get, _) = canned(Ok(
        r#"{"results":[{"trackName":"Gimme Dat","trackTimeMillis":204000,"artworkUrl100":"https://is1.mzstatic.com/a/100x100bb.jpg"}]}"#,
    ));
    let info = Artwork::new(get).apple_music_info("Gimme Dat Ting", "Davido");
    assert_eq!(info.duration, 0.0);
    assert_eq!(
        info.artwork.as_deref(),
        Some("https://is1.mzstatic.com/a/500x500bb.jpg")
    );
    assert_eq!(info.url, None);
}

#[test]
fn test_apple_duration_taken_only_from_matched_result() {
    let (get, _) = canned(Ok(r#"{"results":[
        {"trackName":"Unrelated","trackTimeMillis":999000,"artworkUrl100":"https://is1.mzstatic.com/a/100x100bb.jpg"},
        {"trackName":"Real Song","trackTimeMillis":180000,"artworkUrl100":"https://is1.mzstatic.com/b/100x100bb.jpg","trackViewUrl":"https://music.apple.com/us/album/real-song/1?i=2"}
    ]}"#));
    let info = Artwork::new(get).apple_music_info("Real Song", "Artist");
    assert_eq!(info.duration, 180.0);
    assert_eq!(
        info.artwork.as_deref(),
        Some("https://is1.mzstatic.com/b/500x500bb.jpg")
    );
    assert_eq!(
        info.url.as_deref(),
        Some("https://music.apple.com/us/album/real-song/1?i=2")
    );
}

#[test]
fn test_soundcloud_page_metadata_has_no_duration_guess() {
    let info = Artwork::new(no_http()).find(
        "Gimme Dat Ting",
        "Davido",
        Some("soundcloud.com"),
        None,
        "",
        Some(&meta(
            "Gimme Dat Ting",
            "Davido",
            Some("https://i1.sndcdn.com/x-large.jpg"),
        )),
    );
    assert_eq!(info.duration, 0.0);
    assert_eq!(
        info.artwork.as_deref(),
        Some("https://i1.sndcdn.com/x-large.jpg")
    );
}

fn seen_with(title: &str, artist: &str, start: i64, duration: f64) -> Capped<TrackKey, (i64, f64)> {
    let mut seen = Capped::new(100);
    seen.insert(key(title, artist), (start, duration));
    seen
}

#[test]
fn test_zero_background_position_keeps_provider_anchor() {
    let seen = seen_with("Track", "Artist", 1_000, 180.0);
    assert_eq!(
        provider_duration_start("Track", "Artist", 0.0, 180.0, &seen, 1_004.0),
        1_000
    );
    assert_eq!(
        provider_duration_start("Track", "Artist", 0.0, 180.0, &seen, 1_211.0),
        1_211
    );
}

#[test]
fn test_stale_low_position_keeps_provider_anchor() {
    let seen = seen_with("Track", "Artist", 1_000, 180.0);
    assert_eq!(
        provider_duration_start("Track", "Artist", 5.0, 180.0, &seen, 1_020.0),
        1_000
    );
}

// --- AppleTimingHelperTests -------------------------------------------------

#[test]
fn test_anchor_start_is_stable_across_polls() {
    let mut anchors = Capped::new(100);
    let song = key("Song", "Artist");
    // A later poll with a later wall clock keeps the original anchor, so the
    // elapsed bar grows 1:1 instead of following Apple's counter.
    assert_eq!(apple_track_start(&song, 1000.0, &mut anchors, None), 1000);
    assert_eq!(apple_track_start(&song, 1200.0, &mut anchors, None), 1000);
}

#[test]
fn test_new_track_without_previous_anchors_at_now() {
    let mut anchors = Capped::new(100);
    assert_eq!(
        apple_track_start(&key("B", "x"), 1200.0, &mut anchors, None),
        1200
    );
}

#[test]
fn test_gapless_change_back_dates_to_previous_track_end() {
    // Previous track started at 1000 and is 180s long, due to end at 1180;
    // the new track is noticed 4s later at 1184 (natural detection lag).
    let mut anchors = Capped::new(100);
    assert_eq!(
        apple_track_start(&key("B", "x"), 1184.0, &mut anchors, Some((1000, 180.0))),
        1180
    );
}

#[test]
fn test_skip_prediction_in_the_future_falls_back_to_now() {
    // The previous track would end at 1180, but the user skipped ahead, so
    // now (1100) precedes the predicted end: anchor fresh instead.
    let mut anchors = Capped::new(100);
    assert_eq!(
        apple_track_start(&key("B", "x"), 1100.0, &mut anchors, Some((1000, 180.0))),
        1100
    );
}

#[test]
fn test_large_gap_prediction_falls_back_to_now() {
    // The previous track ended at 1180 but the next starts 60s later (a
    // pause), well outside the gapless margin: anchor fresh.
    let mut anchors = Capped::new(100);
    assert_eq!(
        apple_track_start(&key("B", "x"), 1240.0, &mut anchors, Some((1000, 180.0))),
        1240
    );
}

#[test]
fn test_prediction_requires_a_known_previous_duration() {
    // The previous track's duration was never locked, so its end is unknown.
    let mut anchors = Capped::new(100);
    assert_eq!(
        apple_track_start(&key("B", "x"), 1184.0, &mut anchors, Some((1000, 0.0))),
        1184
    );
}

#[test]
fn test_anchor_dict_is_capped() {
    let mut anchors = Capped::new(100);
    for i in 0..150 {
        apple_track_start(
            &key(&format!("t{i}"), ""),
            1000.0 + i as f64,
            &mut anchors,
            None,
        );
    }
    assert!(anchors.len() <= 100);
    // The oldest go first.
    assert!(!anchors.contains(&key("t0", "")));
    assert!(anchors.contains(&key("t149", "")));
}

#[test]
fn test_locked_duration_prefers_itunes_and_holds() {
    let mut locks = Capped::new(100);
    let song = key("Song", "Artist");
    // First poll: the media session duration is still 0, iTunes has the real
    // length.
    assert_eq!(apple_locked_duration(&song, 0.0, 200.0, &mut locks), 200.0);
    // Later poll: the media session flips to a bogus value; the lock holds.
    assert_eq!(
        apple_locked_duration(&song, 373.0, 373.0, &mut locks),
        200.0
    );
}

#[test]
fn test_locked_duration_falls_back_to_gsmtc_when_itunes_absent() {
    let mut locks = Capped::new(100);
    let song = key("Song", "Artist");
    // No iTunes match and no media-session duration yet: nothing to lock.
    assert_eq!(apple_locked_duration(&song, 0.0, 0.0, &mut locks), 0.0);
    assert!(!locks.contains(&song));
    // The media session populates: lock the first non-zero value.
    assert_eq!(apple_locked_duration(&song, 250.0, 0.0, &mut locks), 250.0);
    assert_eq!(apple_locked_duration(&song, 999.0, 0.0, &mut locks), 250.0);
}

// --- FallbackTimingTests ----------------------------------------------------

const NOW: f64 = 2_000.0;

fn cloud_fb() -> FallbackTrack {
    fb("Cloud Song", "Cloud Artist", "soundcloud.com", None)
}

#[test]
fn test_no_anchor_publishes_nothing() {
    // Never saw this track's real position, so the only thing we could show
    // is a frozen 0:00. Publish nothing instead.
    assert_eq!(
        fallback_timing(&cloud_fb(), None, &Capped::new(100), NOW),
        None
    );
}

#[test]
fn test_recent_anchor_yields_moving_position() {
    // Real position captured 40s ago on a 180s track: still valid.
    let seen = seen_with("Cloud Song", "Cloud Artist", (NOW - 40.0) as i64, 180.0);
    assert_eq!(
        fallback_timing(&cloud_fb(), None, &seen, NOW),
        Some(Track {
            title: "Cloud Song".into(),
            artist: "Cloud Artist".into(),
            position: 40.0,
            duration: 180.0,
            source: "tab:soundcloud.com".into(),
        })
    );
}

#[test]
fn test_anchor_past_track_end_plus_grace_is_dropped() {
    // Anchored 220s ago on a 180s track (> dur + 30 grace): stale.
    let seen = seen_with("Cloud Song", "Cloud Artist", (NOW - 220.0) as i64, 180.0);
    assert_eq!(fallback_timing(&cloud_fb(), None, &seen, NOW), None);
}

#[test]
fn test_anchor_for_a_different_track_is_ignored() {
    let seen = seen_with("Other Song", "Other Artist", (NOW - 10.0) as i64, 180.0);
    assert_eq!(fallback_timing(&cloud_fb(), None, &seen, NOW), None);
}

fn sampled_apple(playing: bool, sampled_at: f64, duration: Option<f64>) -> Tab {
    Tab {
        playback: Some(Playback {
            position: 42.0,
            duration,
            playing,
            sampled_at,
        }),
        ..tab(
            "music.apple.com",
            None,
            "Album - Album by Artist - Apple Music",
        )
    }
}

#[test]
fn test_apple_page_sample_needs_no_anchor() {
    // A paused video can hold the browser's only media session while Apple
    // Music keeps playing. MusicKit still reports the real position, so there
    // is nothing to recover from a previous reading.
    let mut t = sampled_apple(true, 1_750_000_008_000.0, Some(207.0));
    t.metadata = Some(meta("Real Song", "Real Artist", None));
    let apple = fb("Real Song", "Real Artist", "music.apple.com", None);
    assert_eq!(
        fallback_timing(&apple, Some(&t), &Capped::new(100), 1_750_000_010.0),
        Some(Track {
            title: "Real Song".into(),
            artist: "Real Artist".into(),
            position: 44.0,
            duration: 207.0,
            source: "tab:music.apple.com".into(),
        })
    );
}

#[test]
fn test_apple_fallback_track_comes_from_page_metadata() {
    let mut t = sampled_apple(true, 1_750_000_008_000.0, Some(207.0));
    t.metadata = Some(meta("Real Song", "Real Artist", None));
    let r = report(ALL, vec![t.clone()]);
    assert_eq!(
        fallback_track(Some(&r)),
        Some(fb("Real Song", "Real Artist", "music.apple.com", None))
    );
    // Without metadata the page title cannot name the track, so Apple has no
    // fallback at all.
    t.metadata = None;
    assert_eq!(fallback_track(Some(&report(ALL, vec![t]))), None);
}

// --- AppleExtensionTimingTests ----------------------------------------------

const APPLE_NOW: f64 = 1_750_000_010.0;
/// Sampled 2 seconds before `APPLE_NOW`.
const SAMPLED: f64 = 1_750_000_008_000.0;

#[test]
fn test_playing_sample_extrapolates_to_now() {
    let t = sampled_apple(true, SAMPLED, Some(207.0));
    assert_eq!(
        apple_extension_timing(Some(&t), APPLE_NOW),
        Some((44.0, 207.0))
    );
}

#[test]
fn test_paused_sample_is_used_as_is() {
    let t = sampled_apple(false, SAMPLED, Some(207.0));
    assert_eq!(
        apple_extension_timing(Some(&t), APPLE_NOW),
        Some((42.0, 207.0))
    );
}

#[test]
fn test_missing_duration_reports_zero() {
    let t = sampled_apple(true, SAMPLED, None);
    assert_eq!(
        apple_extension_timing(Some(&t), APPLE_NOW),
        Some((44.0, 0.0))
    );
}

#[test]
fn test_slight_clock_skew_is_tolerated_without_rewinding() {
    // Sampled "3 seconds in the future" on the browser's clock: position must
    // not be extrapolated backwards.
    let t = sampled_apple(true, 1_750_000_013_000.0, Some(207.0));
    assert_eq!(
        apple_extension_timing(Some(&t), APPLE_NOW),
        Some((42.0, 207.0))
    );
}

#[test]
fn test_stale_sample_returns_none() {
    let t = sampled_apple(true, SAMPLED - 120_000.0, Some(207.0));
    assert_eq!(apple_extension_timing(Some(&t), APPLE_NOW), None);
}

#[test]
fn test_far_future_sample_returns_none() {
    let t = sampled_apple(true, SAMPLED + 60_000.0, Some(207.0));
    assert_eq!(apple_extension_timing(Some(&t), APPLE_NOW), None);
}

#[test]
fn test_tab_without_playback_fields_returns_none() {
    let t = tab("music.apple.com", None, "Apple Music");
    assert_eq!(apple_extension_timing(Some(&t), APPLE_NOW), None);
}

#[test]
fn test_missing_tab_returns_none() {
    assert_eq!(apple_extension_timing(None, APPLE_NOW), None);
}

// --- The poll step (main() in presence.py) ----------------------------------

const CHROME: &str = "Google.Chrome_1";

fn playing(title: &str, artist: &str, position: f64, duration: f64, source: &str) -> Track {
    Track {
        title: title.into(),
        artist: artist.into(),
        position,
        duration,
        source: source.into(),
    }
}

fn engine() -> Engine {
    Engine::new(Config::default(), Artwork::new(no_http()))
}

const NO_ART: Switches = Switches {
    artwork: false,
    track_button: true,
    get_chunes_button: false,
};

fn published(action: Action) -> Activity {
    match action {
        Action::Publish { activity, .. } => activity,
        other => panic!("expected a publish, got {other:?}"),
    }
}

#[test]
fn test_poll_publishes_a_browser_track_and_then_keeps_it() {
    let mut engine = engine();
    let r = report(
        ALL,
        vec![tab("soundcloud.com", None, "Cloud Song by Cloud Artist")],
    );
    let track = playing("Cloud Song", "Cloud Artist", 30.0, 180.0, CHROME);
    let action = engine.poll(Some(track.clone()), Some(&r), NO_ART, 1_000.0);
    let Action::Publish {
        activity,
        status,
        host,
    } = action
    else {
        panic!("expected a publish");
    };
    assert_eq!(status, "Cloud Song - Cloud Artist");
    assert_eq!(host.as_deref(), Some("soundcloud.com"));
    assert_eq!(
        activity,
        Activity {
            details: "Cloud Song".into(),
            state: Some("by Cloud Artist".into()),
            start: Some(970),
            end: Some(1_150),
            large_image: None,
            large_text: Some("SoundCloud".into()),
            buttons: vec![(
                "Play on SoundCloud".into(),
                "https://soundcloud.com/search?q=Cloud%20Song%20Cloud%20Artist".into()
            )],
        }
    );
    engine.published();
    // Five seconds on, the same track at the expected position is left alone.
    let later = playing("Cloud Song", "Cloud Artist", 35.0, 180.0, CHROME);
    assert_eq!(
        engine.poll(Some(later), Some(&r), NO_ART, 1_005.0),
        Action::Keep
    );
    // A minute later it is re-sent even though nothing changed.
    let much_later = playing("Cloud Song", "Cloud Artist", 90.0, 180.0, CHROME);
    published(engine.poll(Some(much_later), Some(&r), NO_ART, 1_060.0));
}

#[test]
fn test_poll_resends_after_a_seek_and_after_a_failed_send() {
    let mut engine = engine();
    let r = report(ALL, vec![tab("soundcloud.com", None, "Song by Artist")]);
    published(engine.poll(
        Some(playing("Song", "Artist", 10.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_000.0,
    ));
    // Discord never confirmed it, so the next poll tries again.
    published(engine.poll(
        Some(playing("Song", "Artist", 15.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_005.0,
    ));
    engine.published();
    // Jumping ahead 60 seconds moves the start by more than the slack.
    let activity = published(engine.poll(
        Some(playing("Song", "Artist", 80.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_010.0,
    ));
    assert_eq!(activity.start, Some(930));
}

#[test]
fn test_poll_clears_once_when_playback_stops() {
    let mut engine = engine();
    let r = report(ALL, vec![tab("soundcloud.com", None, "Song by Artist")]);
    published(engine.poll(
        Some(playing("Song", "Artist", 10.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_000.0,
    ));
    engine.published();
    let empty = report(ALL, vec![]);
    assert_eq!(
        engine.poll(None, Some(&empty), NO_ART, 1_005.0),
        Action::Clear
    );
    assert_eq!(
        engine.poll(None, Some(&empty), NO_ART, 1_010.0),
        Action::Keep
    );
}

#[test]
fn test_poll_never_publishes_a_plain_youtube_video() {
    let mut engine = engine();
    let r = report(ALL, vec![tab("youtube.com", None, "Cool Clip - YouTube")]);
    let video = playing("Cool Clip", "Channel", 10.0, 300.0, CHROME);
    assert_eq!(
        engine.poll(Some(video), Some(&r), NO_ART, 1_000.0),
        Action::Keep
    );
}

#[test]
fn test_poll_skips_a_generic_placeholder_title() {
    let mut engine = engine();
    let r = report(
        ALL,
        vec![
            tab("music.youtube.com", Some("a1B2c3D4e5F"), "YouTube Music"),
            tab("youtube.com", None, "Clip - YouTube"),
        ],
    );
    let generic = playing("YouTube Music", "", 0.0, 0.0, CHROME);
    assert_eq!(
        engine.poll(Some(generic), Some(&r), NO_ART, 1_000.0),
        Action::Keep
    );
}

#[test]
fn test_poll_falls_back_to_the_audible_tab_after_a_takeover() {
    let mut engine = engine();
    let cloud = tab("soundcloud.com", None, "Cloud Song by Cloud Artist");
    let r = report(ALL, vec![cloud.clone()]);
    published(engine.poll(
        Some(playing("Cloud Song", "Cloud Artist", 40.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_000.0,
    ));
    engine.published();
    // A YouTube video takes the media session; SoundCloud is still audible.
    let takeover = report(
        ALL,
        vec![cloud, tab("youtube.com", None, "Cool Clip - YouTube")],
    );
    let video = playing("Cool Clip", "Channel", 5.0, 300.0, CHROME);
    assert_eq!(
        engine.poll(Some(video.clone()), Some(&takeover), NO_ART, 1_020.0),
        Action::Keep
    );
    // A minute on it is re-sent from the anchor, still moving 1:1.
    let activity = published(engine.poll(Some(video), Some(&takeover), NO_ART, 1_070.0));
    assert_eq!(activity.details, "Cloud Song");
    assert_eq!(activity.start, Some(960));
    assert_eq!(activity.end, Some(1_140));
}

#[test]
fn test_poll_uses_the_apple_page_timing() {
    let mut engine = engine();
    let mut apple = sampled_apple(true, 1_750_000_008_000.0, Some(207.0));
    apple.metadata = Some(meta("Real Song", "Real Artist", None));
    let r = report(ALL, vec![apple]);
    // The media session shows the queue-wide counter and a wrong duration.
    let session = playing("Real Song", "Real Artist", 900.0, 373.0, CHROME);
    let activity = published(engine.poll(Some(session), Some(&r), NO_ART, 1_750_000_010.0));
    assert_eq!(activity.start, Some(1_750_000_010 - 44));
    assert_eq!(activity.end, Some(1_750_000_010 - 44 + 207));
    assert_eq!(activity.large_text.as_deref(), Some("Apple Music"));
    assert_eq!(
        activity.buttons[0].1,
        "https://music.apple.com/search?term=Real%20Song%20Real%20Artist"
    );
}

#[test]
fn test_poll_anchors_apple_without_a_page_sample() {
    let mut engine = engine();
    let r = report(
        ALL,
        vec![tab(
            "music.apple.com",
            None,
            "Album - Album by Artist - Apple Music",
        )],
    );
    let first = playing("Song A", "Artist", 900.0, 180.0, CHROME);
    let activity = published(engine.poll(Some(first), Some(&r), NO_ART, 1_000.0));
    // Anchored at first sight, not at the queue-wide counter.
    assert_eq!(activity.start, Some(1_000));
    assert_eq!(activity.end, Some(1_180));
    engine.published();
    // The next track shows up 3s after the first was due to end: gapless.
    let second = playing("Song B", "Artist", 1_083.0, 0.0, CHROME);
    let activity = published(engine.poll(Some(second), Some(&r), NO_ART, 1_183.0));
    assert_eq!(activity.start, Some(1_180));
}

#[test]
fn test_poll_uses_itunes_for_artwork_and_duration() {
    let (get, _) = canned(Ok(
        r#"{"results":[{"trackName":"Song","trackTimeMillis":200000,"artworkUrl100":"https://is1.mzstatic.com/a/100x100bb.jpg","trackViewUrl":"https://music.apple.com/us/album/song/1?i=2&uo=4"}]}"#,
    ));
    let mut engine = Engine::new(
        Config {
            service_label: "Winamp".into(),
            ..Config::default()
        },
        Artwork::new(get),
    );
    // A desktop player with no duration of its own.
    let track = playing("Song", "Artist", 0.0, 0.0, "Winamp.exe");
    let activity = published(engine.poll(Some(track), None, Switches::default(), 1_000.0));
    assert_eq!(
        activity.large_image.as_deref(),
        Some("https://is1.mzstatic.com/a/500x500bb.jpg")
    );
    assert_eq!(activity.large_text.as_deref(), Some("Winamp"));
    assert_eq!(activity.start, Some(1_000));
    assert_eq!(activity.end, Some(1_200));
    // An unknown host gets no "Play on" link.
    assert!(activity.buttons.is_empty());
}

#[test]
fn test_poll_falls_back_to_the_configured_image_key() {
    let mut engine = Engine::new(
        Config {
            image_key: "chunes".into(),
            ..Config::default()
        },
        Artwork::new(no_http()),
    );
    let r = report(ALL, vec![tab("soundcloud.com", None, "Song by Artist")]);
    let track = playing("Song", "Artist", 10.0, 180.0, CHROME);
    let activity = published(engine.poll(Some(track), Some(&r), NO_ART, 1_000.0));
    assert_eq!(activity.large_image.as_deref(), Some("chunes"));
}

#[test]
fn test_poll_keeps_the_host_through_an_unattributed_poll() {
    let mut engine = engine();
    let r = report(ALL, vec![tab("soundcloud.com", None, "Song by Artist")]);
    published(engine.poll(
        Some(playing("Song", "Artist", 10.0, 180.0, CHROME)),
        Some(&r),
        NO_ART,
        1_000.0,
    ));
    engine.published();
    // The extension's report went stale, so the browser track is published
    // without its policy, still under the host it last resolved to.
    assert_eq!(
        engine.poll(
            Some(playing("Song", "Artist", 15.0, 180.0, CHROME)),
            None,
            NO_ART,
            1_005.0
        ),
        Action::Keep
    );
}

#[test]
fn test_config_defaults_and_overrides() {
    let dir = std::env::temp_dir().join(format!("chunes-config-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("config.json");
    assert_eq!(Config::load(&path).unwrap(), Config::default());
    std::fs::write(
        &path,
        "\u{feff}{\"service_label\": \"Winamp\", \"extra\": 1}",
    )
    .unwrap();
    let config = Config::load(&path).unwrap();
    assert_eq!(config.service_label, "Winamp");
    assert_eq!(config.client_id, Config::default().client_id);
    std::fs::write(&path, "{").unwrap();
    assert!(Config::load(&path).is_err());
    std::fs::remove_dir_all(&dir).unwrap();
}
