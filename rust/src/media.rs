//! The active Windows media session, via the `windows` crate's projection of
//! `Windows.Media.Control`. Replaces `get_playing_track` and the
//! `winrt-Windows.Media.Control` wheels in `presence.py`.

use std::time::{SystemTime, UNIX_EPOCH};

use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
};

/// 100 ns ticks between 1601-01-01 (WinRT `DateTime`) and the Unix epoch.
const UNIX_EPOCH_TICKS: i64 = 116_444_736_000_000_000;

#[derive(Debug, Clone, PartialEq)]
pub struct Track {
    pub title: String,
    pub artist: String,
    pub position: f64,
    pub duration: f64,
    pub source: String,
}

fn seconds(ticks: i64) -> f64 {
    ticks as f64 / 10_000_000.0
}

/// The first playing session whose source matches `allowed_sources`.
///
/// Blocks on WinRT async calls, so call it from `spawn_blocking`.
pub fn playing_track(allowed_sources: &[String]) -> windows::core::Result<Option<Track>> {
    let manager = SessionManager::RequestAsync()?.get()?;
    for session in manager.GetSessions()? {
        let source = session.SourceAppUserModelId()?.to_string();
        let lowered = source.to_lowercase();
        if !allowed_sources.is_empty()
            && !allowed_sources
                .iter()
                .any(|a| lowered.contains(&a.to_lowercase()))
        {
            continue;
        }
        if session.GetPlaybackInfo()?.PlaybackStatus()? != PlaybackStatus::Playing {
            continue;
        }
        let properties = session.TryGetMediaPropertiesAsync()?.get()?;
        let title = properties.Title()?.to_string();
        if title.is_empty() {
            continue;
        }
        let artist = properties.Artist()?.to_string();
        let timeline = session.GetTimelineProperties()?;
        let mut position = seconds(timeline.Position()?.Duration);
        let duration = seconds(timeline.EndTime()?.Duration);

        // position is a snapshot taken at LastUpdatedTime, not "now";
        // browsers refresh it infrequently, so extrapolate forward. Inactive
        // background tabs reset position on a track change but leave
        // LastUpdatedTime pointing at the previous track's start.
        let updated = seconds(timeline.LastUpdatedTime()?.UniversalTime - UNIX_EPOCH_TICKS);
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64());
        let elapsed = now - updated;
        let recent = elapsed > 0.0 && elapsed < 15.0;
        let mid_track = elapsed > 0.0 && elapsed < 3600.0 && position > 15.0;
        if recent || mid_track {
            position += elapsed;
        }
        if duration > 0.0 && position >= duration {
            position = 0.0;
        }
        return Ok(Some(Track {
            title,
            artist,
            position,
            duration,
            source,
        }));
    }
    Ok(None)
}
