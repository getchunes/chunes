//! Ported from `tests/test_protocol.py`; each Rust test keeps the name of
//! the Python test it mirrors.

use serde_json::{json, Value};

use super::*;

fn valid_report() -> Value {
    json!({
        "protocol": 4,
        "enabled": true,
        "services": {"appleMusic": true, "soundcloud": true, "youtubeMusic": true},
        "tabs": [
            {"host": "soundcloud.com", "mediaId": null, "title": "Song by Artist"},
            {"host": "music.youtube.com", "mediaId": "a1B2c3D4e5F", "title": "Track - Artist"},
        ],
    })
}

fn apple_tab() -> Value {
    json!({
        "host": "music.apple.com",
        "mediaId": null,
        "title": "Daughter from Hell - Album by Gracie Abrams - Apple Music",
    })
}

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

fn validated_report() -> Report {
    Report {
        enabled: true,
        services: Services {
            apple_music: true,
            soundcloud: true,
            youtube_music: true,
        },
        tabs: vec![
            tab("soundcloud.com", None, "Song by Artist"),
            tab("music.youtube.com", Some("a1B2c3D4e5F"), "Track - Artist"),
        ],
    }
}

fn report(value: &Value) -> Report {
    validate_report(value).expect("valid report").0
}

fn merge(base: Value, extra: Value) -> Value {
    let (Value::Object(mut base), Value::Object(extra)) = (base, extra) else {
        panic!("objects only");
    };
    base.extend(extra);
    Value::Object(base)
}

fn assert_rejected(value: &Value) {
    let error = validate_report(value).expect_err(&format!("accepted {value}"));
    assert_eq!(error.status, 400, "{value}");
}

// --- ReportValidationTests ------------------------------------------------

#[test]
fn test_accepts_and_detaches_exact_payload() {
    assert_eq!(report(&valid_report()), validated_report());
}

#[test]
fn test_accepts_page_metadata() {
    let mut value = valid_report();
    value["tabs"][0]["metadata"] = json!({"title": "Song", "artist": "Artist", "artwork": "https://i1.sndcdn.com/artwork.jpg"});
    assert_eq!(
        report(&value).tabs[0].metadata,
        Some(PageMetadata {
            title: "Song".into(),
            artist: "Artist".into(),
            artwork: Some("https://i1.sndcdn.com/artwork.jpg".into()),
        })
    );
}

#[test]
fn test_accepts_either_supported_protocol_version() {
    for version in [4, 5] {
        let mut value = valid_report();
        value["protocol"] = json!(version);
        let (parsed, negotiated) = validate_report(&value).unwrap();
        assert_eq!(parsed, validated_report());
        assert_eq!(negotiated, version);
    }
}

#[test]
fn test_accepts_the_playing_tabs_own_address_on_v5() {
    let mut value = valid_report();
    value["protocol"] = json!(5);
    value["tabs"][0]["trackUrl"] = json!("https://soundcloud.com/artist/song");
    let parsed = report(&value);
    assert_eq!(
        parsed.tabs[0].track_url.as_deref(),
        Some("https://soundcloud.com/artist/song")
    );
    assert_eq!(parsed.tabs[1].track_url, None);
    value["tabs"][0]["trackUrl"] = Value::Null;
    assert_eq!(report(&value).tabs[0].track_url, None);
}

#[test]
fn test_rejects_a_tab_address_from_a_v4_reporter() {
    let mut value = valid_report();
    value["tabs"][0]["trackUrl"] = json!("https://soundcloud.com/artist/song");
    assert_rejected(&value);
}

#[test]
fn test_rejects_a_tab_address_that_is_not_the_tabs_own_page() {
    let long = format!("https://soundcloud.com/{}", "a".repeat(MAX_TRACK_URL_CHARS));
    let invalid = [
        json!("https://example.com/song"),
        json!("https://soundcloud.com.example.com/song"),
        json!("http://soundcloud.com/artist/song"),
        json!("https://user:pass@soundcloud.com/artist/song"),
        json!("javascript:alert(1)"),
        json!(""),
        json!(long),
        json!(12),
        // Python reads everything before the backslash as user information.
        json!("https://soundcloud.com\\@example.com/song"),
        json!("https://@soundcloud.com/song"),
    ];
    for track_url in invalid {
        let mut value = valid_report();
        value["protocol"] = json!(5);
        value["tabs"][0]["trackUrl"] = track_url;
        assert_rejected(&value);
    }
}

#[test]
fn test_requires_a_supported_protocol_marker() {
    let mut unmarked = valid_report();
    unmarked.as_object_mut().unwrap().remove("protocol");
    let mut cases = vec![unmarked];
    for marker in [json!(3), json!(6), json!("5"), json!(5.0), json!(true)] {
        let mut value = valid_report();
        value["protocol"] = marker;
        cases.push(value);
    }
    for value in &cases {
        assert_rejected(value);
    }
}

#[test]
fn test_rejects_non_exact_payloads() {
    let mut invalid = Vec::new();
    let mut push = |edit: &dyn Fn(&mut Value)| {
        let mut value = valid_report();
        edit(&mut value);
        invalid.push(value);
    };
    push(&|v| {
        v.as_object_mut().unwrap().remove("tabs");
    });
    push(&|v| v["version"] = json!(1));
    push(&|v| v["enabled"] = json!(1));
    push(&|v| {
        v["services"]
            .as_object_mut()
            .unwrap()
            .remove("youtubeMusic");
    });
    push(&|v| v["services"]["youtube"] = json!(true));
    push(&|v| v["services"]["soundcloud"] = json!(1));
    push(&|v| {
        let apple = merge(apple_tab(), json!({"mediaId": "a1B2c3D4e5F"}));
        v["tabs"].as_array_mut().unwrap().push(apple);
    });
    push(&|v| v["tabs"] = json!({}));
    push(&|v| v["tabs"][0]["audible"] = json!(true));
    push(&|v| v["tabs"][0]["mediaId"] = json!("a1B2c3D4e5F"));
    push(&|v| v["tabs"][1]["mediaId"] = json!("not-a-video-id"));
    push(&|v| v["tabs"][0]["host"] = json!("SoundCloud.com:443"));
    push(&|v| v["tabs"][0]["title"] = json!("x".repeat(513)));
    for value in &invalid {
        assert_rejected(value);
    }
}

#[test]
fn test_title_limit_counts_characters_not_bytes() {
    // Python's len() counts code points; 512 multi-byte characters fit.
    let mut value = valid_report();
    value["tabs"][0]["title"] = json!("é".repeat(512));
    assert!(validate_report(&value).is_ok());
}

#[test]
fn test_rejects_duplicate_json_members_and_nonstandard_constants() {
    let bodies: [&[u8]; 3] = [
        br#"{"enabled":true,"enabled":false,"services":{},"tabs":[]}"#,
        br#"{"enabled":true,"services":{"appleMusic":true,"soundcloud":true,"soundcloud":false,"youtubeMusic":true},"tabs":[]}"#,
        br#"{"enabled":true,"services":{"appleMusic":true,"soundcloud":true,"youtubeMusic":true},"tabs":[{"host":"soundcloud.com","title":NaN}]}"#,
    ];
    for body in bodies {
        assert_eq!(parse_report_body(body).unwrap_err().status, 400);
    }
}

#[test]
fn test_rejects_body_limits() {
    assert!(parse_report_body(b"").is_err());
    assert!(parse_report_body(&vec![b' '; MAX_BODY_BYTES + 1]).is_err());
    let mut too_many = valid_report();
    too_many["tabs"] = Value::Array(
        (0..=MAX_TABS)
            .map(|_| json!({"host": "soundcloud.com", "mediaId": null, "title": "Song"}))
            .collect(),
    );
    assert!(validate_report(&too_many).is_err());
}

#[test]
fn test_parses_valid_utf8_json() {
    let body = serde_json::to_vec(&valid_report()).unwrap();
    assert_eq!(parse_report_body(&body).unwrap().0, validated_report());
}

#[test]
fn test_accepts_apple_music_tabs_without_playback() {
    let mut value = valid_report();
    value["tabs"].as_array_mut().unwrap().push(apple_tab());
    let parsed = report(&value);
    assert_eq!(
        parsed.tabs[2],
        tab(
            "music.apple.com",
            None,
            "Daughter from Hell - Album by Gracie Abrams - Apple Music"
        )
    );
}

fn playback() -> Value {
    json!({"position": 42.5, "duration": 207.0, "playing": true, "sampledAt": 1_750_000_000_000u64})
}

#[test]
fn test_accepts_and_detaches_apple_playback_fields() {
    let mut value = valid_report();
    value["tabs"]
        .as_array_mut()
        .unwrap()
        .push(merge(apple_tab(), playback()));
    assert_eq!(
        report(&value).tabs[2].playback,
        Some(Playback {
            position: 42.5,
            duration: Some(207.0),
            playing: true,
            sampled_at: 1.75e12
        })
    );
}

#[test]
fn test_accepts_null_playback_duration() {
    let mut value = valid_report();
    let sample = json!({"position": 3.0, "duration": null, "playing": false, "sampledAt": 1_750_000_000_000u64});
    value["tabs"]
        .as_array_mut()
        .unwrap()
        .push(merge(apple_tab(), sample));
    assert_eq!(
        report(&value).tabs[2].playback.as_ref().unwrap().duration,
        None
    );
}

#[test]
fn test_rejects_invalid_playback_fields() {
    let with = |extra: Value| merge(merge(apple_tab(), playback()), extra);
    let mut missing_sampled_at = merge(apple_tab(), playback());
    missing_sampled_at
        .as_object_mut()
        .unwrap()
        .remove("sampledAt");
    let invalid_tabs = [
        merge(
            json!({"host": "soundcloud.com", "mediaId": null, "title": "T"}),
            playback(),
        ),
        merge(apple_tab(), json!({"position": 42.5})),
        missing_sampled_at,
        with(json!({"position": -1})),
        with(json!({"position": 24 * 60 * 60 + 1})),
        with(json!({"position": true})),
        with(json!({"position": "42"})),
        with(json!({"duration": -1})),
        with(json!({"duration": "207"})),
        with(json!({"playing": 1})),
        with(json!({"sampledAt": null})),
        with(json!({"sampledAt": -5})),
        with(json!({"sampledAt": 9e15})),
    ];
    for tab in invalid_tabs {
        let mut value = valid_report();
        value["tabs"].as_array_mut().unwrap().push(tab);
        assert_rejected(&value);
    }
}

// --- RequestValidationTests -----------------------------------------------

fn request(content_type: &str, path: &str, method: &str) -> Vec<u8> {
    let body_length = serde_json::to_vec(&valid_report()).unwrap().len();
    format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:52846\r\nContent-Type: {content_type}\r\nContent-Length: {body_length}"
    )
    .into_bytes()
}

fn default_request() -> Vec<u8> {
    request("application/json", "/tabs", "POST")
}

fn replace(haystack: &[u8], from: &str, to: &str) -> Vec<u8> {
    String::from_utf8(haystack.to_vec())
        .unwrap()
        .replace(from, to)
        .into_bytes()
}

fn append(head: &[u8], extra: &str) -> Vec<u8> {
    [head, extra.as_bytes()].concat()
}

#[test]
fn test_accepts_only_exact_json_media_type_for_tabs() {
    match parse_request_head(&default_request()).unwrap() {
        RequestHead::Report { content_length } => assert!(content_length > 0),
        other => panic!("{other:?}"),
    }
    for content_type in [
        "text/plain",
        "application/json; charset=utf-8",
        "application/problem+json",
    ] {
        let error = parse_request_head(&request(content_type, "/tabs", "POST")).unwrap_err();
        assert_eq!(error.status, 415);
    }
}

#[test]
fn test_rejects_wrong_route_method_host_and_framing() {
    let base = default_request();
    let cases = [
        (request("application/json", "/tabs?x=1", "POST"), 404),
        (request("application/json", "/tabs", "PUT"), 405),
        (
            replace(&base, "Host: 127.0.0.1:52846", "Host: example.com"),
            403,
        ),
        (replace(&base, "Content-Length:", "X-Length:"), 411),
        (append(&base, "\r\nTransfer-Encoding: chunked"), 400),
        (append(&base, "\r\nContent-Length: 1"), 400),
        (append(&base, "\r\n Folded: value"), 400),
        (append(&base, "\r\nX-Control: value\x7f"), 400),
    ];
    for (head, status) in cases {
        assert_eq!(
            parse_request_head(&head).unwrap_err().status,
            status,
            "{}",
            String::from_utf8_lossy(&head)
        );
    }
}

#[test]
fn test_state_route_has_no_request_body() {
    assert_eq!(
        parse_request_head(b"GET /state HTTP/1.1\r\nHost: localhost:52846").unwrap(),
        RequestHead::State
    );
    assert!(
        parse_request_head(b"GET /state HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1").is_err()
    );
}

// --- ServicePolicyTests ---------------------------------------------------

const BROWSER: &str = "Google.Chrome_123";

#[test]
fn test_service_identity_and_labels_cover_both_services() {
    assert_eq!(
        service_for_host("soundcloud.com"),
        Some(Service::SoundCloud)
    );
    assert_eq!(
        service_for_host("www.soundcloud.com"),
        Some(Service::SoundCloud)
    );
    assert_eq!(
        service_for_host("music.youtube.com"),
        Some(Service::YouTubeMusic)
    );
    assert_eq!(
        service_label_for_host("www.soundcloud.com", ""),
        "SoundCloud"
    );
    assert_eq!(
        service_label_for_host("music.youtube.com", ""),
        "YouTube Music"
    );
}

#[test]
fn test_master_service_and_paused_tabs_suppress_browser_activity() {
    let mut r = validated_report();
    assert!(browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("soundcloud.com")
    ));
    assert!(!browser_track_is_allowed(BROWSER, Some(&r), None));
    r.enabled = false;
    assert!(!browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("soundcloud.com")
    ));
    r.enabled = true;
    r.services.youtube_music = false;
    assert!(!browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("music.youtube.com")
    ));
    assert!(browser_track_is_allowed("MusicPlayer.exe", Some(&r), None));
    assert!(browser_track_is_allowed(BROWSER, None, None));
}

#[test]
fn test_enabled_tabs_filters_master_and_service_settings() {
    let mut r = validated_report();
    r.services.youtube_music = false;
    assert_eq!(
        enabled_tabs(Some(&r)),
        vec![&tab("soundcloud.com", None, "Song by Artist")]
    );
    r.enabled = false;
    assert!(enabled_tabs(Some(&r)).is_empty());
}

fn with_apple() -> Report {
    let mut value = valid_report();
    value["tabs"].as_array_mut().unwrap().push(apple_tab());
    report(&value)
}

#[test]
fn test_apple_music_identity_label_and_service_gating() {
    assert_eq!(
        service_for_host("music.apple.com"),
        Some(Service::AppleMusic)
    );
    assert_eq!(service_label_for_host("music.apple.com", ""), "Apple Music");
    assert_eq!(service_for_host("www.apple.com"), None);

    let mut r = with_apple();
    let apple = r.tabs[2].clone();
    assert!(browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("music.apple.com")
    ));
    assert!(enabled_tabs(Some(&r)).contains(&&apple));
    r.services.apple_music = false;
    assert!(!browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("music.apple.com")
    ));
    assert!(!enabled_tabs(Some(&r)).contains(&&apple));
    r.services.apple_music = true;
    r.enabled = false;
    assert!(!browser_track_is_allowed(
        BROWSER,
        Some(&r),
        Some("music.apple.com")
    ));
}

#[test]
fn test_untitled_service_tab_attributes_only_apple_music() {
    assert_eq!(untitled_service_tab(Some(&validated_report())), None);
    assert_eq!(untitled_service_tab(None), None);
    let mut r = with_apple();
    assert_eq!(
        untitled_service_tab(Some(&r)).map(|t| t.host.as_str()),
        Some("music.apple.com")
    );
    r.services.apple_music = false;
    assert_eq!(untitled_service_tab(Some(&r)), None);
    r.services.apple_music = true;
    r.enabled = false;
    assert_eq!(untitled_service_tab(Some(&r)), None);
}

#[test]
fn test_has_unpublishable_audible_tab_flags_blocked_and_disabled() {
    let clean = with_apple();
    assert!(!has_unpublishable_audible_tab(Some(&clean)));
    let mut with_video = clean.clone();
    with_video
        .tabs
        .push(tab("youtube.com", None, "Some Clip - YouTube"));
    assert!(has_unpublishable_audible_tab(Some(&with_video)));
    let mut disabled = clean.clone();
    disabled.services.soundcloud = false;
    assert!(has_unpublishable_audible_tab(Some(&disabled)));
    with_video.enabled = false;
    assert!(!has_unpublishable_audible_tab(Some(&with_video)));
    assert!(!has_unpublishable_audible_tab(None));
}

#[test]
fn test_report_freshness_is_bounded() {
    assert!(report_is_fresh(100.0, 100.0));
    assert!(report_is_fresh(100.0, 190.0));
    assert!(!report_is_fresh(100.0, 191.0));
    assert!(!report_is_fresh(101.0, 100.0));
    assert!(!report_is_fresh(0.0, 100.0));
}

#[test]
fn test_lone_surrogate_escapes_are_accepted_like_python() {
    // A title cut in the middle of an emoji arrives as a lone surrogate.
    assert_eq!(
        replace_lone_surrogates(
            r#"{"t":"a\ud83d","u":"\ud83d\ude00","v":"\\ud83d","w":"\udc00x"}"#
        ),
        r#"{"t":"a\uFFFD","u":"\ud83d\ude00","v":"\\ud83d","w":"\uFFFDx"}"#
    );
    assert_eq!(
        replace_lone_surrogates(r#"{"t":"plain"}"#),
        r#"{"t":"plain"}"#
    );
    let body = br#"{"protocol":5,"enabled":true,"services":{"appleMusic":true,"soundcloud":true,"youtubeMusic":true},"tabs":[{"host":"soundcloud.com","mediaId":null,"title":"Song by Artist \ud83c"}]}"#;
    let (report, _) = parse_report_body(body).unwrap();
    assert_eq!(report.tabs[0].title, "Song by Artist \u{FFFD}");
}
