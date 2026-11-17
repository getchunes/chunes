//! What the tray menu shows.
//!
//! A port of the menu half of `chunes.py`, kept free of Win32 so it is tested
//! on any host. The tray rebuilds it every time it opens, so the status lines
//! and checkboxes are always current.

/// Something a menu item does when chosen.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command {
    ToggleAutostart,
    ToggleAutomaticUpdates,
    CheckForUpdates,
    ToggleArtwork,
    ToggleTrackButton,
    ToggleGetChunesButton,
    OpenLog,
    Quit,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Entry {
    /// A status line, shown greyed out.
    Text(String),
    Check {
        label: &'static str,
        checked: bool,
        command: Command,
    },
    Action {
        label: &'static str,
        command: Command,
    },
    Separator,
}

/// What the presence engine is doing, for the status lines.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Status {
    pub track: Option<String>,
    /// `None` when no fresh Chune ID report has arrived.
    pub extension_enabled: Option<bool>,
}

/// The current value of each checkbox.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Checks {
    pub autostart: bool,
    pub automatic_updates: bool,
    pub artwork: bool,
    pub track_button: bool,
    pub get_chunes_button: bool,
}

pub const TRAY_INTRO_MESSAGE: &str =
    "Chunes is running in the system tray. Right-click the tray icon for status and settings.";

pub fn current_track_text(status: &Status) -> String {
    match &status.track {
        Some(track) if !track.is_empty() => track.clone(),
        _ => "Nothing playing".into(),
    }
}

pub fn extension_state_text(status: &Status) -> String {
    match status.extension_enabled {
        None => "Chune ID: not connected".into(),
        Some(true) => "Chune ID: on".into(),
        Some(false) => "Chune ID: off".into(),
    }
}

pub fn version_text() -> String {
    format!("Chunes v{}", env!("CARGO_PKG_VERSION"))
}

/// Tray items, minus anything the Store build is not allowed to offer.
pub fn build(status: &Status, store_updates: bool, checks: Checks) -> Vec<Entry> {
    let mut items = vec![
        Entry::Text(current_track_text(status)),
        Entry::Text(extension_state_text(status)),
        Entry::Text(version_text()),
        Entry::Check {
            label: "Start with Windows",
            checked: checks.autostart,
            command: Command::ToggleAutostart,
        },
        Entry::Separator,
    ];
    if !store_updates {
        // The Store updates the packaged build, so it never checks GitHub
        // releases or offers to install one.
        items.push(Entry::Check {
            label: "Automatically check for updates",
            checked: checks.automatic_updates,
            command: Command::ToggleAutomaticUpdates,
        });
        items.push(Entry::Action {
            label: "Check for updates now",
            command: Command::CheckForUpdates,
        });
    }
    items.extend([
        Entry::Check {
            label: "Look up online album art",
            checked: checks.artwork,
            command: Command::ToggleArtwork,
        },
        Entry::Check {
            label: "Show \"Play on...\" button",
            checked: checks.track_button,
            command: Command::ToggleTrackButton,
        },
        Entry::Check {
            label: "Show \"Get Chunes\" button",
            checked: checks.get_chunes_button,
            command: Command::ToggleGetChunesButton,
        },
        Entry::Separator,
        Entry::Action {
            label: "Open log",
            command: Command::OpenLog,
        },
        Entry::Action {
            label: "Quit",
            command: Command::Quit,
        },
    ]);
    items
}

/// Win32 menus treat `&` as a mnemonic marker, so a track title has to
/// double it to show it.
pub fn escape_menu_text(text: &str) -> String {
    text.replace('&', "&&")
}

#[cfg(test)]
mod tests {
    //! Ported from `TrayStatusTests` and `PackagedTrayTests` in
    //! `tests/test_chunes.py`.
    use super::*;

    fn labels(store_updates: bool) -> Vec<String> {
        build(&Status::default(), store_updates, Checks::default())
            .into_iter()
            .filter_map(|entry| match entry {
                Entry::Check { label, .. } | Entry::Action { label, .. } => Some(label.to_owned()),
                _ => None,
            })
            .collect()
    }

    #[test]
    fn test_dynamic_menu_text_reflects_presence_snapshot() {
        let mut status = Status {
            track: Some("Song - Artist".into()),
            extension_enabled: Some(true),
        };
        assert_eq!(current_track_text(&status), "Song - Artist");
        assert_eq!(extension_state_text(&status), "Chune ID: on");
        assert_eq!(
            version_text(),
            format!("Chunes v{}", env!("CARGO_PKG_VERSION"))
        );
        status = Status {
            track: None,
            extension_enabled: Some(false),
        };
        assert_eq!(current_track_text(&status), "Nothing playing");
        assert_eq!(extension_state_text(&status), "Chune ID: off");
        status.extension_enabled = None;
        assert_eq!(extension_state_text(&status), "Chune ID: not connected");
    }

    #[test]
    fn test_the_menu_shows_the_status_lines_first() {
        let status = Status {
            track: Some("Rock & Roll - Artist".into()),
            extension_enabled: Some(true),
        };
        let items = build(&status, false, Checks::default());
        assert_eq!(items[0], Entry::Text("Rock & Roll - Artist".into()));
        assert_eq!(items[1], Entry::Text("Chune ID: on".into()));
        assert_eq!(escape_menu_text("Rock & Roll"), "Rock && Roll");
    }

    #[test]
    fn test_checkboxes_follow_the_settings() {
        let checks = Checks {
            autostart: true,
            get_chunes_button: true,
            ..Checks::default()
        };
        let checked: Vec<_> = build(&Status::default(), false, checks)
            .into_iter()
            .filter_map(|entry| match entry {
                Entry::Check {
                    label,
                    checked: true,
                    ..
                } => Some(label),
                _ => None,
            })
            .collect();
        assert_eq!(
            checked,
            ["Start with Windows", "Show \"Get Chunes\" button"]
        );
    }

    #[test]
    fn test_the_packaged_menu_drops_the_github_update_items() {
        let unpackaged = labels(false);
        let store = labels(true);
        for item in ["Check for updates now", "Automatically check for updates"] {
            assert!(unpackaged.iter().any(|l| l == item));
            assert!(!store.iter().any(|l| l == item));
        }
        // Everything the Store build still owns has to survive.
        for kept in [
            "Start with Windows",
            "Look up online album art",
            "Show \"Play on...\" button",
            "Show \"Get Chunes\" button",
            "Open log",
            "Quit",
        ] {
            assert!(store.iter().any(|l| l == kept), "{kept}");
        }
    }
}
