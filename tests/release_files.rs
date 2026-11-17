//! Checks that the release inputs (assets, executable resources, the WiX
//! installer, the Store manifest, build scripts, workflows and release docs)
//! stay consistent with each other and with the Cargo version.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use regex::Regex;
use roxmltree::{Document, Node};
use sha2::{Digest, Sha256};

/// Cargo.toml is the single version source for Chunes.exe, the MSI, and the MSIX.
const VERSION: &str = env!("CARGO_PKG_VERSION");

const WIX_NS: &str = "http://schemas.microsoft.com/wix/2006/wi";
const UTIL_NS: &str = "http://schemas.microsoft.com/wix/UtilExtension";
const APPX_P: &str = "http://schemas.microsoft.com/appx/manifest/foundation/windows10";
const APPX_UAP: &str = "http://schemas.microsoft.com/appx/manifest/uap/windows10";
const APPX_UAP5: &str = "http://schemas.microsoft.com/appx/manifest/uap/windows10/5";
const APPX_RESCAP: &str =
    "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities";
const ASM_V1: &str = "urn:schemas-microsoft-com:asm.v1";
const ASM_V3: &str = "urn:schemas-microsoft-com:asm.v3";
const COMPATIBILITY: &str = "urn:schemas-microsoft-com:compatibility.v1";
const WINDOWS_SETTINGS_2016: &str = "http://schemas.microsoft.com/SMI/2016/WindowsSettings";

// ---------------------------------------------------------------------------
// File helpers
// ---------------------------------------------------------------------------

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn workflows_dir() -> PathBuf {
    root().join(".github").join("workflows")
}

fn read_bytes(relative: &str) -> Vec<u8> {
    let path = root().join(relative);
    fs::read(&path).unwrap_or_else(|error| panic!("read {}: {error}", path.display()))
}

/// Reads a text file the way Python's `read_text` does: newlines are
/// normalized to `\n`, so checks behave the same on a CRLF checkout.
fn read_path(path: &Path) -> String {
    let text =
        fs::read_to_string(path).unwrap_or_else(|error| panic!("read {}: {error}", path.display()));
    text.replace("\r\n", "\n")
}

fn read(relative: &str) -> String {
    read_path(&root().join(relative))
}

fn workflow(name: &str) -> String {
    read_path(&workflows_dir().join(name))
}

/// Every `*.yml` in .github/workflows, sorted by file name.
fn all_workflows() -> Vec<(String, String)> {
    let mut paths: Vec<PathBuf> = fs::read_dir(workflows_dir())
        .expect("list workflows")
        .map(|entry| entry.expect("workflow entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "yml"))
        .collect();
    paths.sort();
    paths
        .into_iter()
        .map(|path| {
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let text = read_path(&path);
            (name, text)
        })
        .collect()
}

/// File names directly inside `relative` (not recursive), sorted.
fn file_names(relative: &str) -> Vec<String> {
    let mut names: Vec<String> = fs::read_dir(root().join(relative))
        .unwrap_or_else(|error| panic!("list {relative}: {error}"))
        .map(|entry| entry.expect("directory entry"))
        .filter(|entry| entry.file_type().is_ok_and(|kind| kind.is_file()))
        .map(|entry| entry.file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).unwrap_or_else(|error| panic!("bad regex {pattern}: {error}"))
}

fn normalize_whitespace(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn cargo_toml() -> toml::Table {
    read("Cargo.toml").parse().expect("Cargo.toml parses")
}

// ---------------------------------------------------------------------------
// XML helpers
// ---------------------------------------------------------------------------

/// Parses an XML file into a document that lives for the rest of the test.
fn parse_xml(relative: &str) -> Document<'static> {
    let text: &'static str = Box::leak(read(relative).into_boxed_str());
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    Document::parse(text).unwrap_or_else(|error| panic!("parse {relative}: {error}"))
}

fn is_element(node: &Node, ns: &str, name: &str) -> bool {
    node.is_element() && node.tag_name().namespace() == Some(ns) && node.tag_name().name() == name
}

/// Direct children named `{ns}name`.
fn children<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    name: &'static str,
) -> impl Iterator<Item = Node<'a, 'i>> {
    node.children()
        .filter(move |child| is_element(child, ns, name))
}

/// The first direct child named `{ns}name`.
fn child<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    name: &'static str,
) -> Option<Node<'a, 'i>> {
    children(node, ns, name).next()
}

/// Follows a path of direct children, like ElementTree's `find("a/b/c")`.
fn find_path<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    path: &[&'static str],
) -> Option<Node<'a, 'i>> {
    find_all_path(node, ns, path).into_iter().next()
}

/// Every node reached by a path of direct children, like `findall("a/b/c")`.
fn find_all_path<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    path: &[&'static str],
) -> Vec<Node<'a, 'i>> {
    let mut current = vec![node];
    for name in path {
        current = current
            .into_iter()
            .flat_map(|parent| children(parent, ns, name))
            .collect();
    }
    current
}

/// Descendants (not the node itself) named `{ns}name`, like `findall(".//name")`.
fn descendants<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    name: &'static str,
) -> impl Iterator<Item = Node<'a, 'i>> {
    node.descendants()
        .skip(1)
        .filter(move |item| is_element(item, ns, name))
}

/// The first direct child named `{ns}name` whose `Id` attribute is `id`.
fn child_with_id<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    name: &'static str,
    id: &str,
) -> Option<Node<'a, 'i>> {
    children(node, ns, name).find(|item| item.attribute("Id") == Some(id))
}

/// The first descendant named `{ns}name` whose `Id` attribute is `id`.
fn descendant_with_id<'a, 'i: 'a>(
    node: Node<'a, 'i>,
    ns: &'static str,
    name: &'static str,
    id: &str,
) -> Option<Node<'a, 'i>> {
    descendants(node, ns, name).find(|item| item.attribute("Id") == Some(id))
}

/// A required attribute, failing the test when it is missing.
fn attr<'a>(node: Node<'a, '_>, name: &str) -> &'a str {
    node.attribute(name)
        .unwrap_or_else(|| panic!("<{}> has no {name} attribute", node.tag_name().name()))
}

/// The element's leading text, like ElementTree's `.text or ""`.
fn text<'a>(node: Node<'a, '_>) -> &'a str {
    node.text().unwrap_or("")
}

fn wix() -> Document<'static> {
    parse_xml("installer/Chunes.wxs")
}

fn product<'a>(doc: &'a Document<'static>) -> Node<'a, 'static> {
    child(doc.root_element(), WIX_NS, "Product").expect("Wix/Product")
}

fn wix_property<'a>(product: Node<'a, 'static>, id: &str) -> Node<'a, 'static> {
    child_with_id(product, WIX_NS, "Property", id).unwrap_or_else(|| panic!("Property {id}"))
}

fn wix_custom_action<'a>(product: Node<'a, 'static>, id: &str) -> Node<'a, 'static> {
    child_with_id(product, WIX_NS, "CustomAction", id)
        .unwrap_or_else(|| panic!("CustomAction {id}"))
}

fn wix_control<'a>(product: Node<'a, 'static>, id: &str) -> Node<'a, 'static> {
    descendant_with_id(product, WIX_NS, "Control", id).unwrap_or_else(|| panic!("Control {id}"))
}

fn wix_registry_search<'a>(product: Node<'a, 'static>, property_id: &str) -> Node<'a, 'static> {
    child(wix_property(product, property_id), WIX_NS, "RegistrySearch")
        .unwrap_or_else(|| panic!("Property {property_id}/RegistrySearch"))
}

// ---------------------------------------------------------------------------
// Canonical assets
// ---------------------------------------------------------------------------

#[test]
fn test_assets_match_canonical_release_hashes() {
    let expected = [
        (
            "assets/logo.svg",
            "a64b28c856723e8f7619a26244a736c64e16f8af558329bd25379fe198bfd0f5",
        ),
        (
            "assets/logo-512.png",
            "d93fb81ef2b43438d63d83536910923a1be4c7529c55de3591200bac62de679b",
        ),
        (
            "assets/chunes-tray-64.png",
            "133e33cecd70d07dbda8ea1e5608bfd902b517546eda79773f3d867abe59f8fc",
        ),
        (
            "assets/chunes-tray.ico",
            "9118d9a90d162898a875e13ae71d03bc1504b8306f606f796bd804b81cf3a21d",
        ),
    ];
    for (relative, digest) in expected {
        let mut contents = read_bytes(relative);
        if relative.ends_with(".svg") {
            contents = String::from_utf8(contents)
                .expect("svg is utf-8")
                .replace("\r\n", "\n")
                .into_bytes();
        }
        let actual: String = Sha256::digest(&contents)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(actual, digest, "{relative}");
    }
}

// ---------------------------------------------------------------------------
// Packaging
// ---------------------------------------------------------------------------

#[test]
fn test_release_metadata_is_synchronized_with_cargo_version() {
    assert!(
        regex(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$").is_match(VERSION),
        "{VERSION}"
    );
    let cargo = cargo_toml();
    assert_eq!(cargo["package"]["version"].as_str(), Some(VERSION));

    // Cargo.lock's own entry follows Cargo.toml.
    let lock: toml::Table = read("Cargo.lock").parse().expect("Cargo.lock parses");
    let packages = lock["package"].as_array().expect("Cargo.lock packages");
    let chunes = packages
        .iter()
        .find(|item| item["name"].as_str() == Some("chunes"))
        .expect("Cargo.lock lists chunes");
    assert_eq!(chunes["version"].as_str(), Some(VERSION));

    let wix = read("installer/Chunes.wxs");
    let fallback = regex(r#"<\?define ProductVersion="([^"]+)" \?>"#)
        .captures(&wix)
        .expect("Chunes.wxs defines a fallback ProductVersion");
    assert_eq!(&fallback[1], VERSION);

    assert!(!root().join("installer").join("version_info.txt").exists());
    assert!(!root().join("Chunes.spec").exists());
}

#[test]
fn test_executable_resources_carry_the_cargo_version_and_icon() {
    let cargo = cargo_toml();
    let binaries: Vec<&str> = cargo["bin"]
        .as_array()
        .expect("[[bin]]")
        .iter()
        .map(|binary| binary["name"].as_str().expect("bin name"))
        .collect();
    assert_eq!(binaries, ["Chunes"]);
    assert!(cargo["build-dependencies"]
        .as_table()
        .expect("[build-dependencies]")
        .contains_key("embed-resource"));

    let build = read("build.rs");
    // Resources need rc.exe, so only a Windows host building for Windows
    // compiles them; any other host can still check the Windows target.
    for needle in [
        r#"env::var("CARGO_CFG_TARGET_OS")"#,
        "if !(target_is_windows && cfg!(windows))",
        r#""installer/chunes.rc""#,
        r#""assets/chunes-tray.ico""#,
        r#""installer/chunes.manifest""#,
    ] {
        assert!(build.contains(needle), "build.rs lacks {needle}");
    }
    for path in ["build.rs", "{RESOURCE_SCRIPT}", "{MANIFEST}", "{ICON}"] {
        let needle = format!("\"cargo:rerun-if-changed={path}\"");
        assert!(build.contains(&needle), "build.rs lacks {needle}");
    }
    for needle in [
        "CARGO_PKG_VERSION_{name}",
        r#"["MAJOR", "MINOR", "PATCH"]"#,
        "#define CHUNES_VERSION_BINARY {major},{minor},{patch},0",
        r##"#define CHUNES_VERSION_STRING \"{major}.{minor}.{patch}.0\""##,
        r#"compile_for(root.join(RESOURCE_SCRIPT), ["Chunes"]"#,
        ".manifest_required()",
    ] {
        assert!(build.contains(needle), "build.rs lacks {needle}");
    }

    let rc = read("installer/chunes.rc");
    let lines: Vec<&str> = rc.lines().map(str::trim).collect();
    assert!(lines.contains(&r#"#include "chunes_version.h""#));
    // The tray loads its icon by resource ID 1.
    assert!(lines.contains(&"1 ICON CHUNES_ICON_FILE"));
    assert!(lines.contains(&"1 24 CHUNES_MANIFEST_FILE"));
    for line in [
        "1 VERSIONINFO",
        "FILEVERSION CHUNES_VERSION_BINARY",
        "PRODUCTVERSION CHUNES_VERSION_BINARY",
        "FILEFLAGSMASK 0x3fL",
        "FILEFLAGS 0x0L",
        "FILEOS 0x40004L",
        "FILETYPE 0x1L",
        "FILESUBTYPE 0x0L",
        r#"BLOCK "040904B0""#,
        r#"VALUE "Translation", 0x409, 1200"#,
    ] {
        assert!(lines.contains(&line), "chunes.rc lacks {line}");
    }
    let strings: BTreeMap<String, String> = regex(r#"(?m)^\s*VALUE "(\w+)", ("[^"]*"|\w+)\s*$"#)
        .captures_iter(&rc)
        .map(|captures| (captures[1].to_owned(), captures[2].to_owned()))
        .collect();
    let expected: BTreeMap<String, String> = [
        ("CompanyName", r#""Chunes""#),
        ("FileDescription", r#""Chunes Discord music presence""#),
        ("FileVersion", "CHUNES_VERSION_STRING"),
        ("InternalName", r#""Chunes""#),
        ("LegalCopyright", r#""Copyright 2026 Chunes contributors""#),
        ("OriginalFilename", r#""Chunes.exe""#),
        ("ProductName", r#""Chunes""#),
        ("ProductVersion", "CHUNES_VERSION_STRING"),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_owned(), value.to_owned()))
    .collect();
    assert_eq!(strings, expected);
}

#[test]
fn test_executable_manifest_is_as_invoker_and_per_monitor_v2() {
    let doc = parse_xml("installer/chunes.manifest");
    let root = doc.root_element();
    assert!(is_element(&root, ASM_V1, "assembly"));

    let level = find_path(
        root,
        ASM_V3,
        &[
            "trustInfo",
            "security",
            "requestedPrivileges",
            "requestedExecutionLevel",
        ],
    )
    .expect("requestedExecutionLevel");
    assert_eq!(attr(level, "level"), "asInvoker");
    assert_eq!(attr(level, "uiAccess"), "false");

    let supported: Vec<&str> = find_all_path(
        root,
        COMPATIBILITY,
        &["compatibility", "application", "supportedOS"],
    )
    .into_iter()
    .map(|item| attr(item, "Id"))
    .collect();
    assert_eq!(supported, ["{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}"]);

    let settings = find_path(root, ASM_V3, &["application", "windowsSettings"])
        .expect("application/windowsSettings");
    let awareness = child(settings, WINDOWS_SETTINGS_2016, "dpiAwareness").expect("dpiAwareness");
    assert_eq!(awareness.text(), Some("PerMonitorV2"));

    let controls = find_path(
        root,
        ASM_V1,
        &["dependency", "dependentAssembly", "assemblyIdentity"],
    )
    .expect("assemblyIdentity");
    assert_eq!(attr(controls, "name"), "Microsoft.Windows.Common-Controls");
    assert_eq!(attr(controls, "version"), "6.0.0.0");
    assert_eq!(attr(controls, "publicKeyToken"), "6595b64144ccf1df");
}

#[test]
fn test_build_scripts_build_the_executable_with_cargo() {
    let build_exe = read("scripts/build-exe.ps1");
    for needle in [
        "& cargo build --locked --release",
        r#""target\release\Chunes.exe""#,
        r#"(Join-Path $dist "Chunes.exe")"#,
        "0x8664",
        "Push-Location -LiteralPath $root",
    ] {
        assert!(build_exe.contains(needle), "build-exe.ps1 lacks {needle}");
    }

    let get_version = read("scripts/get-version.ps1");
    assert!(get_version.contains(r#"Join-Path $root "Cargo.toml""#));
    assert!(get_version.contains(r"\[package\]"));

    let skip = regex(
        r#"(?m)if \(-not \$SkipExecutable\) \{\s+& \(Join-Path \$PSScriptRoot "build-exe\.ps1"\)\s+\}"#,
    );
    for name in ["build.ps1", "build-msix.ps1"] {
        let script = read(&format!("scripts/{name}"));
        assert!(
            script.contains(r#"(Join-Path $PSScriptRoot "get-version.ps1")"#),
            "{name}"
        );
        assert!(skip.is_match(&script), "{name}");
    }
    for name in file_names("scripts") {
        if !name.ends_with(".ps1") {
            continue;
        }
        let script = read(&format!("scripts/{name}"));
        for obsolete in ["python", "PyInstaller", "Chunes.spec", "version_info"] {
            assert!(!script.contains(obsolete), "{name} mentions {obsolete}");
        }
    }
}

#[test]
fn test_msi_identity_script_reads_the_property_table_read_only() {
    let script = read("scripts/verify-msi.ps1");
    for needle in [
        "New-Object -ComObject WindowsInstaller.Installer",
        // 0 is msiOpenDatabaseModeReadOnly.
        r#""OpenDatabase" $invoke @($resolved, 0)"#,
        "SELECT `Property`, `Value` FROM `Property`",
        r#"$expectedProductName = "Chunes""#,
        r#"$expectedManufacturer = "Chunes""#,
        r#"$expectedUpgradeCode = "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}""#,
        "ProductVersion = $version",
        "if ($actual -cne $expected[$name])",
        "Installer $name is",
        "invalid stable version:",
    ] {
        assert!(script.contains(needle), "verify-msi.ps1 lacks {needle}");
    }
}

#[test]
fn test_workflows_build_with_cargo_and_verify_msis_without_python() {
    assert!(!workflows_dir().join("rust.yml").exists());
    for (name, workflow) in all_workflows() {
        for obsolete in [
            "from version import",
            "PyInstaller",
            "msi_identity import",
            "verify_msi_identity",
        ] {
            assert!(!workflow.contains(obsolete), "{name} mentions {obsolete}");
        }
    }
    for name in ["ci.yml", "release-unsigned.yml", "release-msix.yml"] {
        let workflow = workflow(name);
        for needle in [
            "rustup show active-toolchain || rustup toolchain install",
            "cargo test --locked",
            r"& .\scripts\get-version.ps1",
        ] {
            assert!(workflow.contains(needle), "{name} lacks {needle}");
        }
    }
}

#[test]
fn test_installer_is_per_user_x64_with_stable_upgrade_code() {
    let doc = wix();
    let product = product(&doc);
    assert_eq!(
        attr(product, "UpgradeCode"),
        "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}"
    );
    let package = child(product, WIX_NS, "Package").expect("Package");
    assert_eq!(attr(package, "InstallScope"), "perUser");
    assert_eq!(attr(package, "InstallPrivileges"), "limited");
    assert_eq!(attr(package, "Platform"), "x64");
    assert!(child(product, WIX_NS, "MajorUpgrade").is_some());
}

#[test]
fn test_installer_wires_shortcut_icon_and_uninstall_cleanup() {
    let doc = wix();
    let product = product(&doc);
    assert!(descendants(product, WIX_NS, "Shortcut").next().is_some());
    let icon = child(product, WIX_NS, "Icon").expect("Icon");
    assert_eq!(attr(icon, "Id"), "ChunesIcon");
    let remove_folders: Vec<_> = descendants(product, WIX_NS, "RemoveFolder").collect();
    assert!(remove_folders.len() >= 2);
    assert!(remove_folders
        .iter()
        .all(|item| attr(*item, "On") == "uninstall"));
}

#[test]
fn test_installer_brands_ui_and_sets_publisher_url() {
    let doc = wix();
    let product = product(&doc);
    let publisher = wix_property(product, "ARPPUBLISHER");
    assert_eq!(attr(publisher, "Value"), "https://github.com/getchunes");
    let variables: BTreeMap<&str, &str> = children(product, WIX_NS, "WixVariable")
        .map(|item| (attr(item, "Id"), attr(item, "Value")))
        .collect();
    assert_eq!(
        variables["WixUIDialogBmp"],
        r"$(var.ProjectDir)\installer\assets\chunes-dialog.bmp"
    );
    // Without this the stock WiX placeholder logo shows in every dialog banner.
    assert_eq!(
        variables["WixUIBannerBmp"],
        r"$(var.ProjectDir)\installer\assets\chunes-banner.bmp"
    );
    let bitmap = child_with_id(product, WIX_NS, "Binary", "ChunesExitDialogBitmap")
        .expect("Binary ChunesExitDialogBitmap");
    assert_eq!(
        attr(bitmap, "SourceFile"),
        r"$(var.ProjectDir)\installer\assets\chunes-exit-dialog.bmp"
    );
    for (relative, width, height) in [
        ("installer/assets/chunes-banner.bmp", 493, 58),
        ("installer/assets/chunes-dialog.bmp", 493, 312),
    ] {
        let header = read_bytes(relative);
        assert!(header.len() >= 26, "{relative}");
        assert_eq!(&header[..2], b"BM", "{relative}");
        let read_i32 = |start: usize| {
            i32::from_le_bytes(header[start..start + 4].try_into().expect("4 bytes"))
        };
        assert_eq!(read_i32(18), width, "{relative}");
        assert_eq!(read_i32(22), height, "{relative}");
    }

    let updates = wix_property(product, "ARPURLUPDATEINFO");
    let comments = wix_property(product, "ARPCOMMENTS");
    assert_eq!(
        attr(updates, "Value"),
        "https://github.com/getchunes/chunes/releases"
    );
    assert!(attr(comments, "Value").contains("Chune ID browser extension"));
}

#[test]
fn test_first_install_privacy_checkboxes_and_opt_outs_are_authored() {
    let doc = wix();
    let product = product(&doc);
    let settings = [
        (
            "AutoUpdate",
            "AUTO_UPDATE",
            "EXISTING_AUTO_UPDATE",
            "AutomaticallyCheckForUpdates",
            "PreserveAutoUpdateOptOut",
        ),
        (
            "Artwork",
            "ARTWORK",
            "EXISTING_ARTWORK",
            "LookUpOnlineCoverArt",
            "PreserveArtworkOptOut",
        ),
    ];
    for (control_id, property_id, search_id, value_name, action_id) in settings {
        let checkbox = wix_control(product, control_id);
        assert_eq!(attr(checkbox, "Property"), property_id);
        assert_eq!(attr(checkbox, "CheckBoxValue"), "1");

        let setting_property = wix_property(product, property_id);
        assert_eq!(attr(setting_property, "Value"), "1");
        assert_eq!(attr(setting_property, "Secure"), "yes");

        let search = wix_registry_search(product, search_id);
        assert_eq!(attr(search, "Name"), value_name);
        assert_eq!(attr(search, "Win64"), "yes");

        let custom = wix_custom_action(product, action_id);
        assert_eq!(attr(custom, "Value"), "0");
    }
}

#[test]
fn test_a_fresh_install_starts_with_windows_but_an_upgrade_honors_opt_out() {
    let doc = wix();
    let product = product(&doc);
    let component = descendant_with_id(product, WIX_NS, "Component", "AutostartSetting")
        .expect("Component AutostartSetting");
    assert_eq!(attr(component, "Win64"), "yes");
    let condition = child(component, WIX_NS, "Condition").expect("Condition");
    assert_eq!(text(condition).trim(), r#"AUTOSTART = "1""#);
    let value = child(component, WIX_NS, "RegistryValue").expect("RegistryValue");
    assert_eq!(attr(value, "Root"), "HKCU");
    assert_eq!(
        attr(value, "Key"),
        r"Software\Microsoft\Windows\CurrentVersion\Run"
    );
    assert_eq!(attr(value, "Name"), "Chunes");
    // Quoted so Chunes.exe parses it back to exactly one argument.
    assert_eq!(attr(value, "Value"), r#""[#ChunesExe]""#);

    let setting = wix_property(product, "AUTOSTART");
    assert_eq!(attr(setting, "Value"), "1");
    assert_eq!(attr(setting, "Secure"), "yes");
    let search = wix_registry_search(product, "EXISTING_AUTOSTART");
    assert_eq!(
        attr(search, "Key"),
        r"Software\Microsoft\Windows\CurrentVersion\Run"
    );
    assert_eq!(attr(search, "Name"), "Chunes");

    let opt_out = wix_custom_action(product, "PreserveAutostartOptOut");
    assert_eq!(attr(opt_out, "Property"), "AUTOSTART");
    assert_eq!(attr(opt_out, "Value"), "0");
    // Only an existing installation can have opted out; a first install
    // has no Run value yet and must still get one.
    for sequence in ["InstallUISequence", "InstallExecuteSequence"] {
        let scheduled = children(product, WIX_NS, sequence)
            .flat_map(|item| children(item, WIX_NS, "Custom"))
            .find(|item| item.attribute("Action") == Some("PreserveAutostartOptOut"))
            .unwrap_or_else(|| panic!("{sequence}/Custom PreserveAutostartOptOut"));
        assert_eq!(
            attr(scheduled, "After"),
            "PreserveArtworkOptOut",
            "{sequence}"
        );
        let condition = normalize_whitespace(text(scheduled));
        assert!(condition.contains("EXISTING_INSTALL_PATH"), "{sequence}");
        assert!(condition.contains("NOT EXISTING_AUTOSTART"), "{sequence}");
    }
}

#[test]
fn test_album_art_installer_wording_is_service_neutral() {
    let doc = wix();
    let product = product(&doc);
    let checkbox = wix_control(product, "Artwork");
    let explanation = wix_control(product, "ArtworkPrivacyText");
    assert_eq!(attr(checkbox, "Text"), "Look up online album art");
    assert_eq!(
        attr(explanation, "Text"),
        "When enabled, Chunes may use the current track title and artist \
         to find online album art. You can change this later from the tray menu."
    );
    assert!(!attr(checkbox, "Text").contains("SoundCloud"));
    assert!(!attr(explanation, "Text").contains("SoundCloud"));

    // The tray menu labels its artwork toggle with the same words.
    let tray_source = read("src/menu.rs");
    assert!(tray_source.contains(r#""Look up online album art""#));
    let privacy = read("PRIVACY.md");
    assert!(privacy.contains("## Online album artwork"));
    assert!(privacy.contains("provider-hosted artwork URL sent locally by Chune ID"));
    assert!(privacy.contains("itunes.apple.com/search"));
    assert!(privacy.contains("https://www.apple.com/legal/privacy/"));
    // The provider lookups those policies covered are gone with protocol 3.
    assert!(!privacy.contains("https://soundcloud.com/pages/privacy"));
    assert!(!privacy.contains("https://policies.google.com/privacy"));
}

#[test]
fn test_success_exit_launch_is_default_checked_and_install_only() {
    let doc = wix();
    let product = product(&doc);
    let extension_text = wix_control(product, "ExtensionText");
    let extension_link = wix_control(product, "ExtensionLink");
    let label = wix_property(product, "WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT");
    let checked = wix_property(product, "WIXUI_EXITDIALOGOPTIONALCHECKBOX");
    let target = wix_property(product, "WixShellExecTarget");
    let action = wix_custom_action(product, "LaunchChunes");

    assert_eq!(attr(extension_link, "Type"), "Hyperlink");
    assert!(attr(extension_text, "Text").contains("required browser companion"));
    assert!(attr(extension_link, "Text").contains(
        "https://chromewebstore.google.com/detail/chune-id/ofbfkbhgfhoapckgjcpmcohbhnogpfjd"
    ));
    assert_eq!(attr(label, "Value"), "Launch Chunes when setup finishes");
    assert_eq!(attr(checked, "Value"), "1");
    assert_eq!(attr(target, "Value"), "[#ChunesExe]");
    assert_eq!(attr(action, "BinaryKey"), "WixCA");
    assert_eq!(attr(action, "DllEntry"), "WixShellExec");
    assert_eq!(attr(action, "Impersonate"), "yes");
    assert_eq!(attr(product, "Id"), "*");
    assert!(child(product, WIX_NS, "MajorUpgrade").is_some());

    let finish_events: Vec<_> = descendants(product, WIX_NS, "Publish")
        .filter(|item| {
            item.attribute("Dialog") == Some("ChuneIdExitDialog")
                && item.attribute("Control") == Some("Finish")
        })
        .collect();
    let launch = finish_events
        .iter()
        .find(|item| {
            item.attribute("Event") == Some("DoAction")
                && item.attribute("Value") == Some("LaunchChunes")
        })
        .expect("Finish DoAction LaunchChunes");
    let end = finish_events
        .iter()
        .find(|item| item.attribute("Event") == Some("EndDialog"))
        .expect("Finish EndDialog");
    assert_eq!(
        normalize_whitespace(text(*launch)),
        "WIXUI_EXITDIALOGOPTIONALCHECKBOX = 1 AND NOT Installed"
    );
    let order = |node: Node| -> i64 { attr(node, "Order").parse().expect("Order is a number") };
    assert!(order(*launch) < order(*end));
    assert!(!descendants(product, WIX_NS, "Custom")
        .any(|item| item.attribute("Action") == Some("LaunchChunes")));
}

#[test]
fn test_installer_closes_running_chunes_before_replacing_files() {
    let doc = wix();
    let product = product(&doc);
    let close = child(product, UTIL_NS, "CloseApplication").expect("util:CloseApplication");
    assert_eq!(attr(close, "Target"), "Chunes.exe");
    assert_eq!(attr(close, "CloseMessage"), "yes");
    assert_eq!(attr(close, "ElevatedCloseMessage"), "yes");
    assert_eq!(attr(close, "Timeout"), "5");
    assert_eq!(attr(close, "TerminateProcess"), "0");
    assert_eq!(attr(close, "RebootPrompt"), "no");

    let set_cmd = descendant_with_id(product, WIX_NS, "CustomAction", "SetCloseChunesCmd")
        .expect("CustomAction SetCloseChunesCmd");
    assert_eq!(set_cmd.attribute("Property"), Some("WixQuietExecCmdLine"));
    assert_eq!(
        set_cmd.attribute("Value"),
        Some(
            r#""[SystemFolder]cmd.exe" /c "taskkill /F /IM Chunes.exe & ping 127.0.0.1 -n 3 > nul""#
        )
    );

    let close_early = descendant_with_id(product, WIX_NS, "CustomAction", "CloseChunesEarly")
        .expect("CustomAction CloseChunesEarly");
    assert_eq!(close_early.attribute("BinaryKey"), Some("WixCA"));
    assert_eq!(close_early.attribute("DllEntry"), Some("WixQuietExec"));
    assert_eq!(close_early.attribute("Execute"), Some("immediate"));
    assert_eq!(close_early.attribute("Return"), Some("ignore"));

    let exec_seq = descendants(product, WIX_NS, "InstallExecuteSequence")
        .next()
        .expect("InstallExecuteSequence");
    let custom = |action: &str| {
        children(exec_seq, WIX_NS, "Custom")
            .find(|item| item.attribute("Action") == Some(action))
            .unwrap_or_else(|| panic!("InstallExecuteSequence/Custom {action}"))
    };
    assert_eq!(
        custom("SetCloseChunesCmd").attribute("Before"),
        Some("CloseChunesEarly")
    );
    assert_eq!(
        custom("CloseChunesEarly").attribute("Before"),
        Some("InstallValidate")
    );
}

#[test]
fn test_unsigned_manual_warning_is_versioned_and_covers_upgrades() {
    let doc = wix();
    let product = product(&doc);
    let warning = descendant_with_id(product, WIX_NS, "Dialog", "UnsignedWarningDlg")
        .expect("Dialog UnsignedWarningDlg");
    let text_all = children(warning, WIX_NS, "Control")
        .map(|control| control.attribute("Text").unwrap_or(""))
        .collect::<Vec<_>>()
        .join(" ");
    for required in [
        "UNSIGNED MANUAL v$(var.ProductVersion)",
        "intentionally unsigned",
        "Unknown publisher",
        "immutable GitHub release for this version",
        "never installs updates by itself",
    ] {
        assert!(text_all.contains(required), "warning lacks {required}");
    }

    let content: Vec<Node> = ["UnsignedText", "ReleaseLink", "FutureUpdateText"]
        .into_iter()
        .map(|control_id| {
            child_with_id(warning, WIX_NS, "Control", control_id)
                .unwrap_or_else(|| panic!("Control {control_id}"))
        })
        .collect();
    let number = |node: Node, name: &str| -> i64 {
        attr(node, name)
            .parse()
            .unwrap_or_else(|_| panic!("{name} is a number"))
    };
    let bottom = |node: Node| number(node, "Y") + number(node, "Height");
    for pair in content.windows(2) {
        assert!(bottom(pair[0]) <= number(pair[1], "Y"));
    }
    assert!(bottom(*content.last().unwrap()) <= 234);

    let routes: Vec<_> = descendants(product, WIX_NS, "Publish")
        .filter(|route| route.attribute("Dialog") == Some("WelcomeDlg"))
        .collect();
    let warning_route = routes
        .iter()
        .find(|route| route.attribute("Value") == Some("UnsignedWarningDlg"))
        .expect("WelcomeDlg route to UnsignedWarningDlg");
    let direct_route = routes
        .iter()
        .find(|route| route.attribute("Value") == Some("InstallDirDlg"))
        .expect("WelcomeDlg route to InstallDirDlg");
    assert_eq!(text(*warning_route).trim(), "NOT Installed");
    assert_eq!(text(*direct_route).trim(), "NOT Installed");

    let source = read("installer/Chunes.wxs");
    assert!(source.contains("<?ifdef UnsignedManualRelease ?>"));
    let build = read("scripts/build.ps1");
    assert!(build.contains("[switch]$UnsignedManualRelease"));
    assert!(build.contains(r#""-dUnsignedManualRelease=1""#));
}

#[test]
fn test_maintenance_restores_install_path_and_autostart_helpers_are_safe() {
    let doc = wix();
    let product = product(&doc);
    let search = wix_registry_search(product, "EXISTING_INSTALL_PATH");
    assert_eq!(attr(search, "Name"), "InstallPath");
    assert_eq!(attr(search, "Win64"), "yes");
    let restore = wix_custom_action(product, "RestoreInstallFolder");
    assert_eq!(attr(restore, "Property"), "INSTALLFOLDER");
    assert_eq!(attr(restore, "Value"), "[EXISTING_INSTALL_PATH]");

    let migrate = wix_custom_action(product, "MigrateOwnedAutostart");
    let remove = wix_custom_action(product, "RemoveOwnedAutostart");
    assert_eq!(attr(migrate, "FileKey"), "ChunesExe");
    assert_eq!(attr(migrate, "ExeCommand"), "--migrate-autostart");
    assert_eq!(attr(remove, "FileKey"), "ChunesExe");
    assert_eq!(attr(remove, "ExeCommand"), "--remove-autostart-if-owned");

    let actions = |sequence: &'static str| -> BTreeMap<&str, &str> {
        find_all_path(product, WIX_NS, &[sequence, "Custom"])
            .into_iter()
            .map(|item| (attr(item, "Action"), text(item)))
            .collect()
    };
    let execute_actions = actions("InstallExecuteSequence");
    let ui_actions = actions("InstallUISequence");
    assert!(ui_actions["RestoreInstallFolder"].contains("Installed OR WIX_UPGRADE_DETECTED"));
    assert!(execute_actions["RestoreInstallFolder"].contains("Installed OR WIX_UPGRADE_DETECTED"));
    assert!(execute_actions["RestoreInstallFolder"].contains("EXISTING_INSTALL_PATH"));
    assert!(execute_actions["MigrateOwnedAutostart"].contains("NOT REMOVE"));
    assert!(execute_actions["RemoveOwnedAutostart"].contains("NOT UPGRADINGPRODUCTCODE"));
}

#[test]
fn test_python_sources_and_requirements_are_removed() {
    // The app is the Rust crate; nothing in the repository runs Python.
    for directory in [".", "tests"] {
        let python: Vec<String> = file_names(directory)
            .into_iter()
            .filter(|name| name.ends_with(".py"))
            .collect();
        assert!(
            python.is_empty(),
            "Python files remain in {directory}: {python:?}"
        );
    }
    for name in [
        "requirements.txt",
        "requirements-build.txt",
        "requirements-windows-py313.lock",
    ] {
        assert!(!root().join(name).exists(), "{name} still exists");
    }
    let requirements: Vec<String> = file_names(".")
        .into_iter()
        .filter(|name| name.starts_with("requirements"))
        .collect();
    assert!(
        requirements.is_empty(),
        "requirements files remain: {requirements:?}"
    );
}

#[test]
fn test_workflows_and_dependabot_have_no_python() {
    let pip = regex(r"(?i)\bpip\b");
    for (name, workflow) in all_workflows() {
        let lower = workflow.to_lowercase();
        for obsolete in ["python", "setup-python", "unittest"] {
            assert!(!lower.contains(obsolete), "{name} mentions {obsolete}");
        }
        assert!(!pip.is_match(&workflow), "{name} mentions pip");
    }
    let dependabot = read(".github/dependabot.yml");
    let ecosystems: Vec<&str> =
        regex(r#"(?m)^\s*-?\s*package-ecosystem:\s*["']?([\w-]+)["']?\s*$"#)
            .captures_iter(&dependabot)
            .map(|captures| captures.get(1).unwrap().as_str())
            .collect();
    assert!(!ecosystems.contains(&"pip"), "dependabot still updates pip");
    assert!(ecosystems.contains(&"cargo"), "{ecosystems:?}");
    assert!(ecosystems.contains(&"github-actions"), "{ecosystems:?}");
    assert!(!pip.is_match(&dependabot), "dependabot.yml mentions pip");
}

#[test]
fn test_wix_download_is_hash_locked() {
    let script = read("scripts/get-wix.ps1");
    assert!(script.contains("wix3141rtm/wix314-binaries.zip"));
    assert!(script.contains("6AC824E1642D6F7277D0ED7EA09411A508F6116BA6FAE0AA5F2C7DAA2FF43D31"));
    assert!(script.contains("Get-FileHash -Algorithm SHA256"));
    assert!(script.contains(r#""dark.exe""#));
}

#[test]
fn test_explicit_wix_directory_does_not_fall_back_to_path() {
    let script = read("scripts/build.ps1");
    assert!(script.contains(r#"$wixBinProvided = $PSBoundParameters.ContainsKey("WixBin")"#));
    assert!(
        regex(r"(?m)if \(-not \$wixBinProvided\) \{\s+\$candle = \(Get-Command candle\.exe")
            .is_match(&script)
    );
}

#[test]
fn test_installer_finish_launch_links_wix_util_extension() {
    let script = read("scripts/build.ps1");
    assert!(script.matches("-ext WixUtilExtension").count() >= 2);
}

#[test]
fn test_unsigned_manual_workflow_has_all_guards() {
    let workflow = workflow("release-unsigned.yml");
    for needle in [
        "workflow_dispatch:",
        "confirm_unsigned:",
        "RELEASE_VERSION: ${{ inputs.version }}",
        r#"CONFIRM_UNSIGNED -cne "true""#,
        "refs/heads/main",
        "group: stable-release",
        "permissions: {}",
        "environment: unsigned-manual-release",
    ] {
        assert!(
            workflow.contains(needle),
            "release-unsigned.yml lacks {needle}"
        );
    }
    assert!(!regex(r"(?m)^\s+push:\s*$").is_match(&workflow));
    assert!(regex(r"(?s)build:\s+name:.*?permissions:\s+contents: read").is_match(&workflow));
    assert!(regex(
        r"(?s)environment: unsigned-manual-release.*?permissions:\s+actions: read\s+contents: write"
    )
    .is_match(&workflow));
    for needle in [
        "get-wix.ps1",
        r#"Remove-Item -LiteralPath "build", "dist""#,
        "-UnsignedManualRelease",
        "ProductVersion = $expectedFileVersion",
    ] {
        assert!(
            workflow.contains(needle),
            "release-unsigned.yml lacks {needle}"
        );
    }
    assert_eq!(
        workflow
            .matches(r"& .\scripts\verify-msi.ps1 -Path $msi -ExpectedVersion $env:RELEASE_VERSION")
            .count(),
        2
    );
    for needle in [
        r".\scripts\build.ps1 -WixBin $env:WIX_BIN",
        "@($exe.FullName, $embeddedExe.FullName, $msi)",
        "Unsigned MSI does not contain the exact freshly built Chunes.exe",
        r#"(Join-Path $env:WIX_BIN "dark.exe")"#,
        "//w:File[@Name='Chunes.exe']",
    ] {
        assert!(
            workflow.contains(needle),
            "release-unsigned.yml lacks {needle}"
        );
    }
    assert!(workflow.matches("SignatureStatus]::NotSigned").count() >= 2);
    for needle in [
        r#""Chunes-$env:RELEASE_VERSION-x64.msi""#,
        "compression-level: 0",
        "retention-days: 1",
        "unsigned_sha256:",
        "releases?per_page=100",
        "--paginate --slurp",
        "Where-Object { $_.tag_name -ceq $tag }",
        "Release version must be newer than existing",
    ] {
        assert!(
            workflow.contains(needle),
            "release-unsigned.yml lacks {needle}"
        );
    }
    assert!(!workflow.contains("--jq"));
    assert!(workflow.contains("$tagRefs = @(git ls-remote --tags origin"));
    assert!(!workflow.contains("ls-remote --exit-code"));
    assert!(workflow.contains("git/refs"));
    assert!(workflow.contains("sha=$env:GITHUB_SHA"));
    assert!(workflow.matches("prerelease=false").count() >= 2);
    assert!(workflow.matches("make_latest=true").count() >= 2);
    assert!(!workflow.contains("PREVIOUS_LATEST_ID"));
    assert!(!workflow.contains("--latest"));
    assert!(workflow.contains("$release.immutable -ne $true"));
    for notice in [
        "UNSIGNED MANUAL RELEASE",
        "UNKNOWN PUBLISHER",
        "immutable",
        "SHA-256",
        "checksum verifies byte equality",
        "installed manually",
    ] {
        assert!(
            workflow.contains(notice),
            "release-unsigned.yml lacks {notice}"
        );
    }
    assert!(!workflow.contains("signpath/github-action"));
    for forbidden in [
        "--clobber",
        "gh release delete",
        "--method DELETE",
        "git push --delete",
    ] {
        assert!(
            !workflow.contains(forbidden),
            "release-unsigned.yml has {forbidden}"
        );
    }
}

#[test]
fn test_ci_derives_and_verifies_current_unsigned_package_identity() {
    let workflow = workflow("ci.yml");
    assert!(!workflow.contains("1.0.0"));
    for needle in [
        r"& .\scripts\get-version.ps1",
        r#"$fileVersion = "$version.0""#,
        r"dist\Chunes-$version-x64.msi",
        r".\scripts\build.ps1 -WixBin $env:WIX_BIN",
        r"& .\scripts\verify-msi.ps1 -Path $msiPath -ExpectedVersion $version",
        ">PerMonitorV2</dpiAwareness>",
        "SignatureStatus]::NotSigned",
        // The Rust checks that used to run in rust.yml.
        "cargo fmt --check",
        "cargo clippy --locked --all-targets -- -D warnings",
        "cargo test --locked",
    ] {
        assert!(workflow.contains(needle), "ci.yml lacks {needle}");
    }
}

#[test]
fn test_release_policy_documents_the_unsigned_release_path() {
    let readme = read("README.md");
    let security = read("SECURITY.md");
    let runbook = read("RELEASING.md");
    let combined = [readme.as_str(), security.as_str(), runbook.as_str()].join("\n");
    let normalized = normalize_whitespace(&combined);
    for required in [
        "Unknown publisher",
        "immutable",
        "unsigned-manual-release",
        "make_latest=true",
        "never downloads",
    ] {
        assert!(
            normalized.contains(required),
            "release docs lack {required}"
        );
    }
    for obsolete in [
        "confirm_v1_recreation",
        "zero-download",
        "Delete the old `v1.0.0`",
        "SignPath",
        "code-signing",
    ] {
        assert!(
            !combined.contains(obsolete),
            "release docs mention {obsolete}"
        );
    }
    assert!(!runbook.contains("version_info.txt"));
    assert!(!runbook.contains("PyInstaller"));
    for runbook_item in [
        "Cargo.toml",
        "installer/Chunes.wxs",
        "Publish unsigned manual release",
        "CodeQL",
        "UpgradeCode",
        "clean per-user installation",
        "in-place upgrade",
        "Never store secret values",
    ] {
        assert!(
            runbook.contains(runbook_item),
            "RELEASING.md lacks {runbook_item}"
        );
    }
}

#[test]
fn test_all_workflow_actions_are_full_sha_pinned() {
    let uses = regex(r"(?m)^\s*uses:\s*([^\s#]+)");
    let pinned = regex(r"^[^@]+@[0-9a-f]{40}$");
    for (name, workflow) in all_workflows() {
        let actions: Vec<&str> = uses
            .captures_iter(&workflow)
            .map(|captures| captures.get(1).unwrap().as_str())
            .collect();
        for action in &actions {
            assert!(pinned.is_match(action), "{name}: {action}");
        }
        let checkout_count = actions
            .iter()
            .filter(|action| action.starts_with("actions/checkout@"))
            .count();
        assert!(
            workflow.matches("persist-credentials: false").count() >= checkout_count,
            "{name}"
        );
    }
}

// ---------------------------------------------------------------------------
// MSIX packaging
// ---------------------------------------------------------------------------

const MSIX_ASSETS: &str = "installer/msix/assets";

fn appx() -> Document<'static> {
    parse_xml("installer/msix/AppxManifest.xml")
}

fn identity<'a>(doc: &'a Document<'static>) -> Node<'a, 'static> {
    child(doc.root_element(), APPX_P, "Identity").expect("Package/Identity")
}

fn application<'a>(doc: &'a Document<'static>) -> Node<'a, 'static> {
    find_path(doc.root_element(), APPX_P, &["Applications", "Application"])
        .expect("Applications/Application")
}

#[test]
fn test_package_version_tracks_cargo_version() {
    // Partner Center reserves the fourth part and never lets a version
    // number be reused, so this has to stay exact.
    let doc = appx();
    assert_eq!(attr(identity(&doc), "Version"), format!("{VERSION}.0"));
}

#[test]
fn test_identity_matches_the_reserved_partner_center_product() {
    let doc = appx();
    let identity = identity(&doc);
    assert_eq!(attr(identity, "Name"), "dubsector.dev.Chunes");
    assert_eq!(
        attr(identity, "Publisher"),
        "CN=75E3A6B2-AC96-45EC-9B42-5EB66C83F2D2"
    );
    assert_eq!(attr(identity, "ProcessorArchitecture"), "x64");
    let publisher = find_path(
        doc.root_element(),
        APPX_P,
        &["Properties", "PublisherDisplayName"],
    )
    .expect("Properties/PublisherDisplayName");
    assert_eq!(publisher.text(), Some("dubsector.dev"));
}

#[test]
fn test_runtime_detects_the_declared_package_identity() {
    let doc = appx();
    assert_eq!(
        chunes::packaged::PACKAGE_IDENTITY_NAME,
        attr(identity(&doc), "Name")
    );
}

#[test]
fn test_full_trust_desktop_application_is_declared() {
    let doc = appx();
    let application = application(&doc);
    assert_eq!(attr(application, "Executable"), "Chunes.exe");
    assert_eq!(
        attr(application, "EntryPoint"),
        "Windows.FullTrustApplication"
    );
    let capabilities = child(doc.root_element(), APPX_P, "Capabilities").expect("Capabilities");
    let capability = child(capabilities, APPX_RESCAP, "Capability").expect("rescap:Capability");
    assert_eq!(attr(capability, "Name"), "runFullTrust");
    let family = find_path(
        doc.root_element(),
        APPX_P,
        &["Dependencies", "TargetDeviceFamily"],
    )
    .expect("Dependencies/TargetDeviceFamily");
    assert_eq!(attr(family, "Name"), "Windows.Desktop");
}

#[test]
fn test_autostart_is_a_manifest_startup_task_the_tray_can_toggle() {
    let doc = appx();
    let extensions = child(application(&doc), APPX_P, "Extensions").expect("Extensions");
    let extension = child(extensions, APPX_UAP5, "Extension").expect("uap5:Extension");
    assert_eq!(attr(extension, "Category"), "windows.startupTask");
    let task = child(extension, APPX_UAP5, "StartupTask").expect("uap5:StartupTask");
    assert_eq!(attr(task, "TaskId"), chunes::startup_task::TASK_ID);
    // Chunes is only useful while it is running, so autostart is on by
    // default. Windows honors a later opt out across Store updates, and
    // Settings and Task Manager can override the app either way.
    assert_eq!(attr(task, "Enabled"), "true");
}

#[test]
fn test_every_declared_logo_ships_its_qualified_assets() {
    let doc = appx();
    let application = application(&doc);
    let visual = child(application, APPX_UAP, "VisualElements").expect("uap:VisualElements");
    let tile = child(visual, APPX_UAP, "DefaultTile").expect("uap:DefaultTile");
    let logos = [
        find_path(doc.root_element(), APPX_P, &["Properties", "Logo"])
            .and_then(|logo| logo.text())
            .expect("Properties/Logo"),
        attr(visual, "Square44x44Logo"),
        attr(visual, "Square150x150Logo"),
        attr(tile, "Square71x71Logo"),
        attr(tile, "Wide310x150Logo"),
    ];
    let assets = file_names(MSIX_ASSETS);
    for logo in logos {
        assert!(logo.starts_with("assets\\"), "{logo}");
        let file = logo.replace('\\', "/");
        let stem = Path::new(&file)
            .file_stem()
            .expect("logo has a file name")
            .to_string_lossy()
            .into_owned();
        let prefix = format!("{stem}.scale-");
        let scaled: Vec<&String> = assets
            .iter()
            .filter(|name| name.starts_with(&prefix) && name.ends_with(".png"))
            .collect();
        assert!(
            scaled.contains(&&format!("{stem}.scale-100.png")),
            "{logo}: {scaled:?}"
        );
        assert!(scaled.len() >= 5, "{logo}: {scaled:?}");
    }

    // The app list and taskbar resolve target sizes rather than scales.
    let assets_dir = root().join(MSIX_ASSETS);
    for size in [16, 24, 32, 48, 256] {
        for name in [
            format!("Square44x44Logo.targetsize-{size}.png"),
            format!("Square44x44Logo.targetsize-{size}_altform-unplated.png"),
        ] {
            assert!(assets_dir.join(&name).is_file(), "{name}");
        }
    }
}

#[test]
fn test_build_script_rewrites_only_the_version_and_indexes_all_scales() {
    let script = read("scripts/build-msix.ps1");
    for needle in [
        r#"$packageVersion = "$version.0""#,
        "$identity.Version = $packageVersion",
        // Splitting scales into resource packs only works inside a bundle.
        "$priDocument.resources.RemoveChild($packagingNode)",
        "makeappx.exe",
        "makepri.exe",
        // A signature applied locally is rejected by Partner Center.
        "[switch]$SelfSign",
        "Development sideloading only",
    ] {
        assert!(script.contains(needle), "build-msix.ps1 lacks {needle}");
    }
}

#[test]
fn test_store_workflow_stages_an_unsigned_package_without_publishing() {
    let workflow = workflow("release-msix.yml");
    assert!(workflow.contains("workflow_dispatch:"));
    assert!(!regex(r"(?m)^\s+push:\s*$").is_match(&workflow));
    for needle in [
        "permissions: {}",
        "group: store-package",
        "refs/heads/main",
        "Store packages must be uploaded unsigned",
        "SignatureStatus]::NotSigned",
        "does not contain the exact freshly built Chunes.exe",
        r#""Chunes-$env:RELEASE_VERSION-x64.msix""#,
        "store/v$env:RELEASE_VERSION",
        "already submitted to the Store",
    ] {
        assert!(workflow.contains(needle), "release-msix.yml lacks {needle}");
    }
    // Store packages are staged for Partner Center, never released here.
    for forbidden in [
        "gh release",
        "git/refs",
        "contents: write",
        "signpath/github-action",
    ] {
        assert!(
            !workflow.contains(forbidden),
            "release-msix.yml has {forbidden}"
        );
    }
}

#[test]
fn test_ci_builds_and_checks_the_store_package_on_every_change() {
    let workflow = workflow("ci.yml");
    assert!(workflow.contains("build-msix.ps1 -SkipExecutable"));
    assert!(workflow.contains(r"dist\Chunes-$version-x64.msix"));
    assert!(workflow.contains("resources.pri"));
}

#[test]
fn test_store_path_is_documented_next_to_the_msi_path() {
    let readme = read("README.md");
    let submission = read("STORE_SUBMISSION.md");
    let combined = normalize_whitespace(&(readme + &submission));
    for required in [
        "Microsoft Store",
        "Build Microsoft Store MSIX",
        "cannot be sideloaded",
        "store/v",
        "Partner Center",
    ] {
        assert!(combined.contains(required), "store docs lack {required}");
    }
}
