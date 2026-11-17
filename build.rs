//! Links the Windows icon, application manifest, and version resource into
//! Chunes.exe.
//!
//! Resources are only compiled when both the host and the target are Windows,
//! so `cargo check --target x86_64-pc-windows-msvc` still works from other
//! hosts, where this script does nothing.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};

const RESOURCE_SCRIPT: &str = "installer/chunes.rc";
const MANIFEST: &str = "installer/chunes.manifest";
const ICON: &str = "assets/chunes-tray.ico";

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed={RESOURCE_SCRIPT}");
    println!("cargo:rerun-if-changed={MANIFEST}");
    println!("cargo:rerun-if-changed={ICON}");

    let target_is_windows = env::var("CARGO_CFG_TARGET_OS").is_ok_and(|os| os == "windows");
    if !(target_is_windows && cfg!(windows)) {
        return;
    }

    let root = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").expect("OUT_DIR"));

    let header = version_header(&root.join(ICON), &root.join(MANIFEST));
    fs::write(out_dir.join("chunes_version.h"), header).expect("write chunes_version.h");

    embed_resource::compile_for(root.join(RESOURCE_SCRIPT), ["Chunes"], embed_resource::NONE)
        .manifest_required()
        .unwrap_or_else(|error| panic!("could not compile {RESOURCE_SCRIPT}: {error}"));
}

/// Builds the header that installer/chunes.rc includes, carrying the package
/// version as `x.y.z.0` and the absolute resource file paths.
fn version_header(icon: &Path, manifest: &Path) -> String {
    let pre = env::var("CARGO_PKG_VERSION_PRE").unwrap_or_default();
    assert!(
        pre.is_empty(),
        "Chunes.exe needs a stable x.y.z package version, not a pre-release"
    );
    let parts = ["MAJOR", "MINOR", "PATCH"].map(|name| {
        let value = env::var(format!("CARGO_PKG_VERSION_{name}")).expect("package version");
        let number: u16 = value
            .parse()
            .unwrap_or_else(|_| panic!("version part {value} does not fit a Windows version"));
        number
    });
    let [major, minor, patch] = parts;

    format!(
        "#define CHUNES_VERSION_BINARY {major},{minor},{patch},0\n\
         #define CHUNES_VERSION_STRING \"{major}.{minor}.{patch}.0\"\n\
         #define CHUNES_ICON_FILE \"{}\"\n\
         #define CHUNES_MANIFEST_FILE \"{}\"\n",
        rc_path(icon),
        rc_path(manifest),
    )
}

/// Spells a path for a resource script string, where a backslash would start
/// an escape sequence.
fn rc_path(path: &Path) -> String {
    let path = path.to_str().expect("resource path is not UTF-8");
    let path = path.strip_prefix(r"\\?\").unwrap_or(path);
    path.replace('\\', "/")
}
