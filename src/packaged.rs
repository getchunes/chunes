//! Whether this process is running from the MSIX package Chunes ships to the
//! Microsoft Store.
//!
//! A port of `packaged.py`. The Store build and the MSI build are the same
//! executable, so the differences between them are decided at runtime. A
//! packaged process gets its updates from the Store and registers autostart
//! through the package manifest, so both of those features behave
//! differently here.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Matches Package/Identity/@Name in installer/msix/AppxManifest.xml.
pub const PACKAGE_IDENTITY_NAME: &str = "dubsector.dev.Chunes";

static FAMILY_NAME: OnceLock<Option<String>> = OnceLock::new();

#[cfg(windows)]
fn query_package_family_name() -> Option<String> {
    use windows::core::PWSTR;
    use windows::Win32::Foundation::{ERROR_INSUFFICIENT_BUFFER, ERROR_SUCCESS};
    use windows::Win32::Storage::Packaging::Appx::GetCurrentPackageFamilyName;

    let mut length = 0u32;
    // SAFETY: a length query with no buffer.
    let result = unsafe { GetCurrentPackageFamilyName(&mut length, None) };
    // APPMODEL_ERROR_NO_PACKAGE means unpackaged. A success with a
    // zero-length buffer, or anything else unexpected, is not a package
    // identity we are willing to act on.
    if result != ERROR_INSUFFICIENT_BUFFER || length == 0 {
        return None;
    }
    let mut buffer = vec![0u16; length as usize];
    // SAFETY: `buffer` holds `length` UTF-16 units.
    let result =
        unsafe { GetCurrentPackageFamilyName(&mut length, Some(PWSTR(buffer.as_mut_ptr()))) };
    if result != ERROR_SUCCESS {
        return None;
    }
    let end = buffer
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(buffer.len());
    let name = String::from_utf16_lossy(&buffer[..end]);
    (!name.is_empty()).then_some(name)
}

#[cfg(not(windows))]
fn query_package_family_name() -> Option<String> {
    None
}

/// The package family name, or `None` when running unpackaged. Queried once.
pub fn package_family_name() -> Option<&'static str> {
    FAMILY_NAME
        .get_or_init(query_package_family_name)
        .as_deref()
}

/// True when running from an MSIX package rather than the MSI install.
pub fn is_packaged() -> bool {
    package_family_name().is_some()
}

/// Where the log goes, given `%LOCALAPPDATA%` and the package family.
///
/// MSIX virtualization redirects `%LOCALAPPDATA%` writes into the package's
/// LocalCache, but "Open log" hands the path to an outside process that only
/// sees the real filesystem. Log straight to the redirect target so both
/// views agree on where the file is.
pub fn log_dir(local_app_data: &Path, family: Option<&str>) -> PathBuf {
    match family {
        None => local_app_data.join("Chunes"),
        Some(family) => local_app_data
            .join("Packages")
            .join(family)
            .join("LocalCache")
            .join("Local")
            .join("Chunes"),
    }
}

#[cfg(test)]
mod tests {
    //! Ported from `PackageIdentityTests` in `tests/test_packaged.py` and the
    //! log location cases in `tests/test_chunes.py`.
    use super::*;

    #[test]
    fn test_the_test_runner_itself_is_not_packaged() {
        assert_eq!(package_family_name(), None);
        assert!(!is_packaged());
    }

    #[test]
    fn test_the_packaged_log_lives_at_the_virtualization_target() {
        let family = "dubsector.dev.Chunes_0r43qeyp9fzw6";
        let dir = log_dir(Path::new("/local"), Some(family));
        // "Open log" hands this path to an unpackaged process, so it must be
        // the real on-disk location, not the virtualized view.
        let tail: Vec<_> = dir
            .components()
            .rev()
            .take(5)
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect();
        assert_eq!(tail, ["Chunes", "Local", "LocalCache", family, "Packages"]);
    }

    #[test]
    fn test_the_unpackaged_log_stays_in_localappdata() {
        let dir = log_dir(Path::new("/local"), None);
        assert_eq!(dir, Path::new("/local").join("Chunes"));
        assert!(!dir.components().any(|c| c.as_os_str() == "Packages"));
    }
}
