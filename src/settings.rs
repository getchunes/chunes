//! Persistent per-user Chunes settings under `HKCU\Software\Chunes`.
//!
//! A port of `settings.py`. Each switch is a `REG_DWORD` of 0 or 1; anything
//! else, or no value at all, reads as the switch's default.

use crate::presence::Switches;
use crate::registry::Value;

pub const SETTINGS_KEY: &str = r"Software\Chunes";
pub const AUTO_UPDATE_VALUE: &str = "AutomaticallyCheckForUpdates";
pub const ARTWORK_VALUE: &str = "LookUpOnlineCoverArt";
pub const TRACK_BUTTON_VALUE: &str = "ShowTrackButton";
pub const GET_CHUNES_BUTTON_VALUE: &str = "ShowGetChunesButton";
pub const TRAY_INTRO_VALUE: &str = "TrayIntroShown";

/// How a stored value reads as a switch.
pub fn bool_from(value: Option<Value>, default: bool) -> bool {
    match value {
        Some(Value::Dword(stored @ (0 | 1))) => stored == 1,
        _ => default,
    }
}

#[cfg(windows)]
pub fn get_bool(name: &str, default: bool) -> bool {
    bool_from(crate::registry::get(SETTINGS_KEY, name), default)
}

#[cfg(not(windows))]
pub fn get_bool(_name: &str, default: bool) -> bool {
    default
}

#[cfg(windows)]
pub fn set_bool(name: &str, enabled: bool) {
    if let Err(error) = crate::registry::set_dword(SETTINGS_KEY, name, u32::from(enabled)) {
        crate::log!("Could not save setting {name}: {error}");
    }
}

#[cfg(not(windows))]
pub fn set_bool(_name: &str, _enabled: bool) {}

pub fn automatic_updates_enabled() -> bool {
    get_bool(AUTO_UPDATE_VALUE, true)
}

pub fn set_automatic_updates(enabled: bool) {
    set_bool(AUTO_UPDATE_VALUE, enabled);
}

pub fn artwork_enabled() -> bool {
    get_bool(ARTWORK_VALUE, true)
}

pub fn set_artwork_enabled(enabled: bool) {
    set_bool(ARTWORK_VALUE, enabled);
}

pub fn track_button_enabled() -> bool {
    get_bool(TRACK_BUTTON_VALUE, true)
}

pub fn set_track_button_enabled(enabled: bool) {
    set_bool(TRACK_BUTTON_VALUE, enabled);
}

/// Off by default: this one advertises Chunes to whoever opens the profile
/// rather than describing what is playing, so it stays the user's own choice.
pub fn get_chunes_button_enabled() -> bool {
    get_bool(GET_CHUNES_BUTTON_VALUE, false)
}

pub fn set_get_chunes_button_enabled(enabled: bool) {
    set_bool(GET_CHUNES_BUTTON_VALUE, enabled);
}

/// The app has no window, so the first launch shows a one-time notification
/// pointing at the tray icon.
pub fn tray_intro_shown() -> bool {
    get_bool(TRAY_INTRO_VALUE, false)
}

pub fn set_tray_intro_shown() {
    set_bool(TRAY_INTRO_VALUE, true);
}

/// The switches the presence engine reads on every publish.
pub fn switches() -> Switches {
    Switches {
        artwork: artwork_enabled(),
        track_button: track_button_enabled(),
        get_chunes_button: get_chunes_button_enabled(),
    }
}

#[cfg(test)]
mod tests {
    //! Ported from `tests/test_settings.py`.
    use super::*;

    #[test]
    fn test_reads_only_boolean_dword_values() {
        for (stored, expected) in [(1, true), (0, false), (2, true)] {
            assert_eq!(bool_from(Some(Value::Dword(stored)), true), expected);
        }
    }

    #[test]
    fn test_missing_or_wrong_type_uses_default() {
        assert!(!bool_from(None, false));
        assert!(!bool_from(Some(Value::String("1".into())), false));
        assert!(!bool_from(Some(Value::Other), false));
    }

    #[test]
    fn test_writes_boolean_dword_values() {
        assert_eq!(u32::from(true), 1);
        assert_eq!(u32::from(false), 0);
    }

    #[test]
    fn test_public_settings_default_on() {
        // Off Windows nothing is stored, so every switch reads its default.
        assert!(automatic_updates_enabled());
        assert!(artwork_enabled());
        assert!(track_button_enabled());
    }

    #[test]
    fn test_the_get_chunes_button_defaults_off() {
        // It advertises Chunes to whoever opens the profile rather than
        // describing what is playing, so nobody gets it without asking.
        assert!(!get_chunes_button_enabled());
        assert!(!tray_intro_shown());
    }
}
