//! Which reported browser tab a Windows media-session title belongs to.
//!
//! A port of `classify_tab`, `identified_tab`, `resolve_tab` and
//! `page_reported_track` from `presence.py`.

use crate::protocol::{self, Report, Tab};

const GENERIC_TITLES: [&str; 4] = ["youtube music", "soundcloud", "apple music", "youtube"];

pub fn normalize_title(title: &str) -> String {
    title
        .to_lowercase()
        .replace(['\u{2019}', '\u{2018}'], "'")
        .replace(['\u{201C}', '\u{201D}'], "\"")
        .trim()
        .to_owned()
}

pub fn is_generic_title(title: &str) -> bool {
    GENERIC_TITLES.contains(&normalize_title(title).as_str())
}

/// Match a playing title to its reported audible browser tab.
pub fn classify_tab<'a>(title: &str, report: Option<&'a Report>) -> Option<&'a Tab> {
    if !report.is_some_and(|r| r.enabled) {
        return None;
    }
    let wanted = normalize_title(title);
    if wanted.is_empty() || GENERIC_TITLES.contains(&wanted.as_str()) {
        return None;
    }
    protocol::enabled_tabs(report).into_iter().find(|tab| {
        // The page names the track it is playing. Apple Music's tab title
        // never does, so this is the only title that can match it.
        std::iter::once(tab.title.as_str())
            .chain(tab.metadata.as_ref().map(|m| m.title.as_str()))
            .any(|candidate| {
                let candidate = normalize_title(candidate);
                !candidate.is_empty()
                    && (candidate.contains(&wanted) || wanted.contains(&candidate))
            })
    })
}

/// True unless the tab's own playback sample says it is paused.
pub fn tab_reports_playing(tab: &Tab) -> bool {
    tab.playback.as_ref().is_none_or(|p| p.playing)
}

/// The single audible tab that names the track it is playing.
pub fn identified_tab(report: Option<&Report>) -> Option<&Tab> {
    let mut identified = protocol::enabled_tabs(report)
        .into_iter()
        .filter(|tab| tab.metadata.is_some() && tab_reports_playing(tab));
    match (identified.next(), identified.next()) {
        (Some(tab), None) => Some(tab),
        _ => None,
    }
}

/// Resolve the audible browser tab a media-session title belongs to, or
/// `None` when it cannot be safely attributed.
pub fn resolve_tab<'a>(title: &str, source: &str, report: Option<&'a Report>) -> Option<&'a Tab> {
    let tab = classify_tab(title, report);
    if tab.is_some() || !protocol::is_browser_source(source) {
        return tab;
    }
    if let Some(tab) = identified_tab(report) {
        return Some(tab);
    }
    if protocol::has_unpublishable_audible_tab(report) {
        return None;
    }
    match protocol::enabled_tabs(report).as_slice() {
        [only] => Some(only),
        _ => None,
    }
}

/// Current track as the playing page itself reports it, when supplied.
pub fn page_reported_track(tab: Option<&Tab>) -> Option<(&str, &str)> {
    tab?.metadata
        .as_ref()
        .map(|m| (m.title.as_str(), m.artist.as_str()))
}

#[cfg(test)]
mod tests {
    //! Ported from `ResolveTabTests` in `tests/test_presence.py`.
    use super::*;
    use crate::protocol::{PageMetadata, Playback, Services};

    const CHROME: &str = "Google.Chrome_1";

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

    fn apple() -> Tab {
        tab(
            "music.apple.com",
            None,
            "Album - Album by Artist - Apple Music",
        )
    }

    fn yt_video() -> Tab {
        tab("youtube.com", None, "Cool Clip - YouTube")
    }

    fn soundcloud() -> Tab {
        tab("soundcloud.com", None, "Cloud Song by Cloud Artist")
    }

    fn meta(title: &str, artist: &str) -> Option<PageMetadata> {
        Some(PageMetadata {
            title: title.into(),
            artist: artist.into(),
            artwork: None,
        })
    }

    fn reporting_apple(playing: bool) -> Tab {
        Tab {
            playback: Some(Playback {
                position: 42.0,
                duration: Some(207.0),
                playing,
                sampled_at: 1.75e12,
            }),
            metadata: meta("Real Song", "Real Artist"),
            ..apple()
        }
    }

    fn report(tabs: Vec<Tab>) -> Report {
        Report {
            enabled: true,
            services: Services {
                apple_music: true,
                soundcloud: true,
                youtube_music: true,
            },
            tabs,
        }
    }

    #[test]
    fn test_apple_attributed_when_only_apple_is_audible() {
        let r = report(vec![apple()]);
        assert_eq!(resolve_tab("Real Song", CHROME, Some(&r)), Some(&r.tabs[0]));
    }

    #[test]
    fn test_youtube_video_is_never_published_as_apple() {
        let r = report(vec![apple(), yt_video()]);
        assert_eq!(resolve_tab("Cool Clip", CHROME, Some(&r)), None);
        assert_eq!(resolve_tab("Mystery Title", CHROME, Some(&r)), None);
    }

    #[test]
    fn test_matched_music_tab_resolves_despite_a_co_audible_video() {
        let r = report(vec![soundcloud(), yt_video()]);
        assert_eq!(
            resolve_tab("Cloud Song", CHROME, Some(&r)),
            Some(&r.tabs[0])
        );
    }

    #[test]
    fn test_generic_ytm_transient_still_attributes_without_other_audio() {
        let r = report(vec![tab(
            "music.youtube.com",
            Some("a1B2c3D4e5F"),
            "YouTube Music",
        )]);
        assert_eq!(
            resolve_tab("Some Real Track", CHROME, Some(&r)),
            Some(&r.tabs[0])
        );
    }

    #[test]
    fn test_disabled_service_audible_blocks_attribution() {
        let mut r = report(vec![apple(), soundcloud()]);
        r.services.soundcloud = false;
        assert_eq!(resolve_tab("Real Song", CHROME, Some(&r)), None);
    }

    #[test]
    fn test_reporting_apple_tab_survives_a_co_audible_video() {
        let r = report(vec![reporting_apple(true), yt_video()]);
        assert_eq!(resolve_tab("Real Song", CHROME, Some(&r)), Some(&r.tabs[0]));
        assert_eq!(resolve_tab("Cool Clip", CHROME, Some(&r)), Some(&r.tabs[0]));
        assert_eq!(
            page_reported_track(Some(&r.tabs[0])),
            Some(("Real Song", "Real Artist"))
        );
    }

    #[test]
    fn test_paused_reporting_tab_is_not_attributed() {
        let r = report(vec![reporting_apple(false), yt_video()]);
        assert_eq!(resolve_tab("Cool Clip", CHROME, Some(&r)), None);
    }

    #[test]
    fn test_two_reporting_tabs_stay_ambiguous() {
        let apple = Tab {
            metadata: meta("Real Song", "Real Artist"),
            ..apple()
        };
        let cloud = Tab {
            metadata: meta("Cloud Song", "Cloud Artist"),
            ..soundcloud()
        };
        let r = report(vec![apple, cloud, yt_video()]);
        assert_eq!(resolve_tab("Mystery Title", CHROME, Some(&r)), None);
        assert_eq!(
            resolve_tab("Cloud Song", CHROME, Some(&r)),
            Some(&r.tabs[1])
        );
    }

    #[test]
    fn test_apple_metadata_title_matches_the_media_session() {
        let apple = Tab {
            metadata: meta("Real Song", "Real Artist"),
            ..apple()
        };
        let cloud = Tab {
            metadata: meta("Cloud Song", "Cloud Artist"),
            ..soundcloud()
        };
        let r = report(vec![apple, cloud]);
        assert_eq!(classify_tab("Real Song", Some(&r)), Some(&r.tabs[0]));
    }
}
