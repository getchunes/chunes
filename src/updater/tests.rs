//! Ported from `tests/test_updater.py`.

use std::sync::{Arc, Mutex};

use serde_json::json;

use super::*;

fn release(version: &str) -> Value {
    let filename = format!("Chunes-{version}-x64.msi");
    json!({
        "tag_name": format!("v{version}"),
        "draft": false,
        "prerelease": false,
        "immutable": true,
        "assets": [{
            "name": filename,
            "state": "uploaded",
            "size": 1234,
            "digest": format!("sha256:{}", "ab".repeat(32)),
            "browser_download_url": format!(
                "https://github.com/getchunes/chunes/releases/download/v{version}/{filename}"
            ),
        }],
    })
}

fn release_with(version: &str, overrides: Value) -> Value {
    let mut value = release(version);
    let fields = value.as_object_mut().unwrap();
    for (key, field) in overrides.as_object().unwrap() {
        fields.insert(key.clone(), field.clone());
    }
    value
}

// VersionTests

#[test]
fn test_versions_are_strict_stable_semver_triplets() {
    assert_eq!(parse_version("v1.2.3"), Ok((1, 2, 3)));
    assert_eq!(is_newer_version("1.0.1", "1.0.0"), Ok(true));
    for value in ["1.2", "1.2.3.4", "01.2.3", "1.2.3-rc.1", "latest"] {
        assert!(parse_version(value).is_err(), "{value} parsed");
    }
}

// ReleaseNoticeSelectionTests

#[test]
fn test_selects_newest_published_release_including_prereleases() {
    let releases = [
        release_with("1.0.1", json!({"prerelease": true})),
        release("1.2.0"),
        release_with("1.1.0", json!({"prerelease": true})),
    ];
    let notice = select_release_notice(&releases, "1.0.0").unwrap().unwrap();
    assert_eq!(notice.version, "1.2.0");
    assert_eq!(
        notice.page_url,
        "https://github.com/getchunes/chunes/releases/tag/v1.2.0"
    );
}

#[test]
fn test_ignores_drafts_malformed_tags_and_current_versions() {
    let releases = [
        release_with("2.0.0", json!({"draft": true})),
        release("1.0.0"),
        release("1.1.0-beta"),
    ];
    assert_eq!(select_release_notice(&releases, "1.0.0"), Ok(None));
}

#[test]
fn test_release_notice_does_not_require_an_installer_asset() {
    let releases = [release_with(
        "1.0.1",
        json!({"prerelease": true, "assets": []}),
    )];
    let notice = select_release_notice(&releases, "1.0.0").unwrap().unwrap();
    assert_eq!(notice.version, "1.0.1");
}

// ReleaseFetchTests

fn page_url(page: u32) -> String {
    format!("{RELEASES_URL}?per_page=100&page={page}")
}

type Calls = Arc<Mutex<Vec<(String, Vec<(String, String)>)>>>;

/// A getter that answers each request with the next of `bodies` and records
/// every URL and header set it was asked for.
fn canned(bodies: Vec<String>) -> (impl Fn(&http::Request) -> Result<String, String>, Calls) {
    let calls = Arc::new(Mutex::new(Vec::new()));
    let log = calls.clone();
    let bodies = Mutex::new(bodies.into_iter());
    let get = move |request: &http::Request| {
        assert_eq!(request.timeout, Duration::from_secs(15));
        assert_eq!(request.max_bytes, 5_000_000);
        assert!(request.no_redirects);
        log.lock().unwrap().push((
            request.url.to_owned(),
            request
                .headers
                .iter()
                .map(|(k, v)| ((*k).to_owned(), (*v).to_owned()))
                .collect(),
        ));
        Ok(bodies.lock().unwrap().next().expect("unexpected request"))
    };
    (get, calls)
}

#[test]
fn test_release_catalog_includes_prereleases() {
    let (get, calls) = canned(vec!["[]".into()]);
    assert_eq!(fetch_releases(&get), Ok(vec![]));
    let calls = calls.lock().unwrap();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].0, page_url(1));
    let user_agent = format!("Chunes/{CURRENT_VERSION}");
    let expected = [
        ("Accept", "application/vnd.github+json"),
        ("X-GitHub-Api-Version", "2026-03-10"),
        ("User-Agent", user_agent.as_str()),
    ];
    let headers: Vec<(&str, &str)> = calls[0]
        .1
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    assert_eq!(headers, expected);
}

#[test]
fn test_release_catalog_follows_full_pages() {
    let first = Value::Array(vec![release("1.1.0"); 100]).to_string();
    let second = Value::Array(vec![release("2.0.0")]).to_string();
    let (get, calls) = canned(vec![first, second]);
    let releases = fetch_releases(&get).unwrap();
    assert_eq!(releases.len(), 101);
    let calls = calls.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert_eq!(calls[1].0, page_url(2));
}

#[test]
fn test_release_catalog_requires_a_list() {
    let (get, _) = canned(vec!["{}".into()]);
    let error = fetch_releases(&get).unwrap_err();
    assert!(error.0.contains("invalid releases"), "{error}");
}

// UpdateControllerTests

type Recorded<T> = Arc<Mutex<Vec<T>>>;

struct Recorder {
    messages: Recorded<(String, Icon)>,
    opened: Recorded<String>,
    slept: Recorded<Duration>,
}

/// Hooks whose browser and message box only record what they were asked.
fn hooks(
    enabled: bool,
    releases: Result<Vec<Value>, UpdateError>,
    browser_works: bool,
) -> (Hooks, Recorder) {
    let recorder = Recorder {
        messages: Arc::default(),
        opened: Arc::default(),
        slept: Arc::default(),
    };
    let (messages, opened, slept) = (
        recorder.messages.clone(),
        recorder.opened.clone(),
        recorder.slept.clone(),
    );
    let hooks = Hooks {
        automatic_updates_enabled: Box::new(move || enabled),
        fetch_releases: Box::new(move || releases.clone()),
        open_url: Box::new(move |url| {
            opened.lock().unwrap().push(url.to_owned());
            browser_works
        }),
        message: Box::new(move |text, icon| messages.lock().unwrap().push((text.to_owned(), icon))),
        sleep: Box::new(move |delay| slept.lock().unwrap().push(delay)),
    };
    (hooks, recorder)
}

#[test]
fn test_pending_automatic_check_rechecks_opt_out_before_network() {
    let (mut hooks, recorder) = hooks(false, Ok(vec![]), true);
    hooks.fetch_releases = Box::new(|| panic!("fetched releases after the opt-out"));
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    let outcome = controller.run(false, Duration::from_secs(8));
    assert_eq!(outcome, Outcome::Canceled);
    assert_eq!(*recorder.slept.lock().unwrap(), [Duration::from_secs(8)]);
}

#[test]
fn test_newer_unsigned_prerelease_opens_its_release_page() {
    let releases = vec![release_with(
        "1.0.1",
        json!({"prerelease": true, "assets": []}),
    )];
    let (hooks, recorder) = hooks(true, Ok(releases), true);
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    let Outcome::Opened(notice) = controller.run(false, Duration::ZERO) else {
        panic!("no release page was opened");
    };
    assert_eq!(notice.version, "1.0.1");
    assert_eq!(
        notice.page_url,
        "https://github.com/getchunes/chunes/releases/tag/v1.0.1"
    );
    assert_eq!(*recorder.opened.lock().unwrap(), [notice.page_url]);
}

#[test]
fn test_automatic_failure_is_logged_but_not_shown() {
    let (hooks, recorder) = hooks(true, Ok(vec![release("1.1.0")]), false);
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    let outcome = controller.run(false, Duration::ZERO);
    assert!(recorder.messages.lock().unwrap().is_empty());
    let Outcome::Failed(logged) = outcome else {
        panic!("the check did not fail");
    };
    assert!(logged.contains("Update failed"), "{logged}");
}

#[test]
fn test_manual_failure_is_shown() {
    let (hooks, recorder) = hooks(true, Err(UpdateError("offline".into())), true);
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    controller.run(true, Duration::ZERO);
    let messages = recorder.messages.lock().unwrap();
    assert_eq!(messages.len(), 1);
    assert!(messages[0].0.contains("Update failed"), "{}", messages[0].0);
    assert_eq!(
        messages[0],
        ("Update failed: UpdateError: offline".into(), Icon::Error)
    );
}

#[test]
fn test_manual_current_version_is_reported() {
    let (hooks, recorder) = hooks(true, Ok(vec![release("1.0.0")]), true);
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    controller.run(true, Duration::ZERO);
    let messages = recorder.messages.lock().unwrap();
    assert_eq!(messages.len(), 1);
    assert!(messages[0].0.contains("up to date"), "{}", messages[0].0);
    assert_eq!(
        messages[0],
        ("Chunes 1.0.0 is up to date.".into(), Icon::Information)
    );
}

#[test]
fn test_default_browser_failure_is_reported() {
    let notice = ReleaseNotice {
        version: "1.0.1".into(),
        page_url: "https://github.com/getchunes/chunes/releases/tag/v1.0.1".into(),
    };
    assert!(open_release_page(&notice, &|_| false).is_err());
}

// Beyond the Python suite.

#[test]
fn test_check_now_during_a_check_says_so() {
    let (hooks, recorder) = hooks(true, Ok(vec![]), true);
    let controller = UpdateController::with_hooks("1.0.0", hooks);
    *controller.lock_checking() = true;
    controller.check_now();
    assert_eq!(
        *recorder.messages.lock().unwrap(),
        [(
            "An update check is already in progress.".to_owned(),
            Icon::Information
        )]
    );
}
