//! The presence loop: poll the media session every five seconds, decide with
//! `presence::Engine`, and publish to Discord.
//!
//! A port of `main()` in `presence.py` and `run_engine()` in `chunes.py`.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use crate::discord;
use crate::http;
use crate::log;
use crate::media;
use crate::presence::{self, Action, Artwork, Config, Engine, Switches, BROWSER_USER_AGENT};
use crate::tab_server::{self, SharedState};

const RECONNECT_DELAY: Duration = Duration::from_secs(10);
const SERVER_RETRY_DELAY: Duration = Duration::from_secs(30);

fn config_path() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("config.json")))
        .unwrap_or_else(|| PathBuf::from("config.json"))
}

fn load_config() -> Config {
    Config::load(&config_path()).unwrap_or_else(|error| {
        log!("{error}; using the default configuration.");
        Config::default()
    })
}

fn itunes_get(url: &str) -> Result<String, String> {
    http::get(&http::Request {
        url,
        headers: &[("User-Agent", BROWSER_USER_AGENT)],
        timeout: Duration::from_secs(10),
        max_bytes: 5_000_000,
        no_redirects: false,
    })
}

fn set_status(state: &SharedState, track: Option<String>, host: Option<String>) {
    let mut shared = state.lock().unwrap_or_else(|e| e.into_inner());
    shared.track = track;
    shared.host = host;
}

/// Keep the Chune ID endpoint up, rebinding if the port was busy.
async fn serve_extension(state: SharedState) {
    loop {
        if let Err(error) = tab_server::serve(state.clone()).await {
            log!("Chune ID endpoint stopped ({error}), retrying in 30s");
        }
        tokio::time::sleep(SERVER_RETRY_DELAY).await;
    }
}

/// Run the presence engine until the process exits. `switches` is read on
/// every poll, so a tray toggle takes effect on the next publish.
pub async fn run(state: SharedState, switches: fn() -> Switches) {
    tokio::spawn(serve_extension(state.clone()));
    let config = load_config();
    let client_id = config.client_id.clone();
    let sources = config.sources.clone();
    let engine = Arc::new(Mutex::new(Engine::new(
        config,
        Artwork::new(Box::new(itunes_get)),
    )));
    let pid = std::process::id();
    let mut connected_before = false;

    loop {
        let mut rpc = match discord::connect(&client_id).await {
            Ok(rpc) => rpc,
            Err(error) => {
                log!("Could not reach Discord ({error}), retrying in 10s");
                tokio::time::sleep(RECONNECT_DELAY).await;
                continue;
            }
        };
        if connected_before {
            log!("Reconnected to Discord.");
        } else {
            log!("Connected to Discord. Watching for music...");
            connected_before = true;
        }
        // Discord forgot the activity; force a re-send next poll.
        engine.lock().unwrap_or_else(|e| e.into_inner()).forget();

        loop {
            let allowed = sources.clone();
            let track = tokio::task::spawn_blocking(move || media::playing_track(&allowed))
                .await
                .ok()
                .and_then(Result::ok)
                .flatten();
            let report = {
                let shared = state.lock().unwrap_or_else(|e| e.into_inner());
                shared.fresh_report(tab_server::now()).cloned()
            };
            let step = engine.clone();
            let action = tokio::task::spawn_blocking(move || {
                step.lock().unwrap_or_else(|e| e.into_inner()).poll(
                    track,
                    report.as_ref(),
                    switches(),
                    tab_server::now(),
                )
            })
            .await
            .unwrap_or(Action::Keep);

            let sent = match action {
                Action::Publish {
                    activity,
                    status,
                    host,
                } => {
                    set_status(&state, Some(status), host);
                    let sent = rpc.set_activity(pid, Some(&activity)).await;
                    if sent.is_ok() {
                        engine.lock().unwrap_or_else(|e| e.into_inner()).published();
                    }
                    sent
                }
                Action::Clear => {
                    set_status(&state, None, None);
                    rpc.set_activity(pid, None).await
                }
                Action::Keep => Ok(()),
            };
            if let Err(error) = sent {
                log!("RPC hiccup ({error}), reconnecting...");
                break;
            }
            tokio::time::sleep(Duration::from_secs(presence::POLL_SECONDS)).await;
        }
    }
}
