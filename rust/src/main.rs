//! Console proof of concept: the Chunes presence loop without the tray,
//! artwork lookups, or the Apple Music timing workarounds.
//!
//! It serves the Chune ID endpoint, reads the Windows media session, applies
//! the same tab attribution and service policy as `presence.py`, and
//! publishes a Listening activity to the local Discord client.

#[cfg(not(windows))]
fn main() {
    eprintln!("chunes-poc reads the Windows media session and only runs on Windows.");
    std::process::exit(1);
}

#[cfg(windows)]
#[tokio::main]
async fn main() {
    engine::run().await;
}

#[cfg(windows)]
mod engine {
    use std::time::Duration;

    use chunes::attribution;
    use chunes::discord::{self, Activity};
    use chunes::media::{self, Track};
    use chunes::protocol;
    use chunes::tab_server::{self, SharedState};

    const CLIENT_ID: &str = "1527834085383213106";
    const SOURCES: [&str; 6] = ["Brave", "chrome", "msedge", "firefox", "opera", "vivaldi"];
    const POLL: Duration = Duration::from_secs(5);
    const RESEND_SECONDS: f64 = 60.0;

    /// The activity to publish for `track`, or `None` when it must not be.
    fn activity_for(
        track: Track,
        state: &SharedState,
        now: f64,
    ) -> Option<(Activity, Option<String>)> {
        let state = state.lock().unwrap();
        let report = state.fresh_report(now);
        let tab = attribution::resolve_tab(&track.title, &track.source, report);
        let (mut title, mut artist) = (track.title.clone(), track.artist.clone());
        let mut generic = attribution::is_generic_title(&title);
        if let Some((page_title, page_artist)) = attribution::page_reported_track(tab) {
            (title, artist) = (page_title.to_owned(), page_artist.to_owned());
            generic = false;
        }
        let host = tab.map(|t| t.host.clone());
        if generic || !protocol::browser_track_is_allowed(&track.source, report, host.as_deref()) {
            return None;
        }
        let label = host
            .as_deref()
            .map(|h| protocol::service_label_for_host(h, ""))
            .unwrap_or("");
        let start = (now - track.position) as i64;
        let activity = Activity {
            details: title,
            state: (!artist.is_empty()).then_some(artist),
            start: (start > 0).then_some(start),
            end: (start > 0 && track.duration > 0.0)
                .then_some((start as f64 + track.duration) as i64),
            large_image: None,
            large_text: (!label.is_empty()).then(|| label.to_owned()),
            buttons: tab
                .and_then(|t| t.track_url.clone())
                .map(|url| vec![(format!("Play on {label}"), url)])
                .unwrap_or_default(),
        };
        Some((activity, host))
    }

    /// What the extension is told is playing; also the future tray's status.
    fn set_status(state: &SharedState, track: Option<String>, host: Option<String>) {
        let mut shared = state.lock().unwrap();
        shared.track = track;
        shared.host = host;
    }

    pub async fn run() {
        let state = SharedState::default();
        let server_state = state.clone();
        tokio::spawn(async move {
            if let Err(error) = tab_server::serve(server_state).await {
                eprintln!("Chune ID endpoint stopped: {error}");
            }
        });
        let sources: Vec<String> = SOURCES.iter().map(|s| s.to_string()).collect();
        let pid = std::process::id();

        loop {
            let mut rpc = match discord::connect(CLIENT_ID).await {
                Ok(rpc) => rpc,
                Err(error) => {
                    println!("{error}; retrying in 10s");
                    tokio::time::sleep(Duration::from_secs(10)).await;
                    continue;
                }
            };
            println!("Connected to Discord. Watching for music...");
            let mut last: Option<(Activity, f64)> = None;

            loop {
                let sources = sources.clone();
                let track = tokio::task::spawn_blocking(move || media::playing_track(&sources))
                    .await
                    .ok()
                    .and_then(Result::ok)
                    .flatten();
                let now = tab_server::now();
                let next = track.and_then(|t| activity_for(t, &state, now));

                let result = match (&next, &last) {
                    (Some((activity, host)), previous) => {
                        let unchanged = previous.as_ref().is_some_and(|(sent, at)| {
                            sent.details == activity.details
                                && sent.state == activity.state
                                && sent.buttons == activity.buttons
                                && sent
                                    .start
                                    .zip(activity.start)
                                    .is_none_or(|(a, b)| (a - b).abs() <= 4)
                                && now - at < RESEND_SECONDS
                        });
                        if unchanged {
                            Ok(())
                        } else {
                            println!(
                                "Now playing: {} - {}",
                                activity.details,
                                activity.state.as_deref().unwrap_or("")
                            );
                            let shown = match &activity.state {
                                Some(artist) => format!("{} - {artist}", activity.details),
                                None => activity.details.clone(),
                            };
                            set_status(&state, Some(shown), host.clone());
                            let sent = rpc.set_activity(pid, Some(activity)).await;
                            if sent.is_ok() {
                                last = Some((activity.clone(), now));
                            }
                            sent
                        }
                    }
                    (None, Some(_)) => {
                        println!("Playback stopped, clearing status.");
                        last = None;
                        set_status(&state, None, None);
                        rpc.set_activity(pid, None).await
                    }
                    (None, None) => Ok(()),
                };
                if let Err(error) = result {
                    println!("RPC hiccup ({error}), reconnecting...");
                    break;
                }
                tokio::time::sleep(POLL).await;
            }
        }
    }
}
