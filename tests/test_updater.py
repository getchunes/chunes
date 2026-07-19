import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import updater


def release(version="1.1.0", **overrides):
    filename = f"Chunes-{version}-x64.msi"
    value = {
        "tag_name": f"v{version}",
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "assets": [
            {
                "name": filename,
                "state": "uploaded",
                "size": 1234,
                "digest": "sha256:" + "ab" * 32,
                "browser_download_url": (
                    "https://github.com/getchunes/chunes/releases/download/"
                    f"v{version}/{filename}"
                ),
            }
        ],
    }
    value.update(overrides)
    return value


class ReleaseSelectionTests(unittest.TestCase):
    def test_selects_exact_msi_from_newer_stable_release(self):
        asset = updater.select_release_asset(release("1.0.1"), current="1.0.0")
        self.assertEqual(asset.version, "1.0.1")
        self.assertEqual(asset.filename, "Chunes-1.0.1-x64.msi")
        self.assertEqual(asset.sha256, "ab" * 32)
        self.assertEqual(asset.size, 1234)

    def test_returns_none_when_current_is_latest(self):
        self.assertIsNone(
            updater.select_release_asset(
                release("1.0.0", immutable=False), current="1.0.0"
            )
        )

    def test_rejects_newer_mutable_release(self):
        for immutable in (False, None):
            with self.subTest(immutable=immutable):
                candidate = release("1.0.1", immutable=immutable)
                with self.assertRaisesRegex(ValueError, "not immutable"):
                    updater.select_release_asset(candidate, current="1.0.0")

    def test_rejects_prereleases_drafts_and_unstable_versions(self):
        values = [
            release(prerelease=True),
            release(draft=True),
            release("1.1.0-beta"),
        ]
        for value in values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    updater.select_release_asset(value, current="1.0.0")

    def test_rejects_missing_duplicate_or_unready_exact_asset(self):
        missing = release()
        missing["assets"][0]["name"] = "Chunes.exe"

        duplicate = release()
        duplicate["assets"].append(dict(duplicate["assets"][0]))

        unready = release()
        unready["assets"][0]["state"] = "new"

        for value in (missing, duplicate, unready):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    updater.select_release_asset(value, current="1.0.0")

    def test_rejects_missing_digest_or_mismatched_download_url(self):
        no_digest = release()
        no_digest["assets"][0]["digest"] = None

        wrong_url = release()
        wrong_url["assets"][0]["browser_download_url"] = (
            "https://github.com/getchunes/chunes/releases/download/"
            "v1.1.0/Other.msi"
        )

        off_repo = release()
        off_repo["assets"][0]["browser_download_url"] = (
            "https://example.com/Chunes-1.1.0-x64.msi"
        )

        for value in (no_digest, wrong_url, off_repo):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    updater.select_release_asset(value, current="1.0.0")

    def test_versions_are_strict_stable_semver_triplets(self):
        self.assertEqual(updater.parse_version("v1.2.3"), (1, 2, 3))
        self.assertTrue(updater.is_newer_version("1.0.1", current="1.0.0"))
        for value in ("1.2", "1.2.3.4", "01.2.3", "1.2.3-rc.1", "latest"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    updater.parse_version(value)


class ReleaseNoticeSelectionTests(unittest.TestCase):
    def test_selects_newest_published_release_including_prereleases(self):
        releases = [
            release("1.0.1", prerelease=True),
            release("1.2.0"),
            release("1.1.0", prerelease=True),
        ]
        notice = updater.select_release_notice(releases, current="1.0.0")
        self.assertEqual(notice.version, "1.2.0")
        self.assertEqual(
            notice.page_url,
            "https://github.com/getchunes/chunes/releases/tag/v1.2.0",
        )

    def test_ignores_drafts_malformed_tags_and_current_versions(self):
        releases = [
            release("2.0.0", draft=True),
            release("1.0.0"),
            release("1.1.0-beta"),
        ]
        self.assertIsNone(
            updater.select_release_notice(releases, current="1.0.0")
        )

    def test_release_notice_does_not_require_an_installer_asset(self):
        notice = updater.select_release_notice(
            [release("1.0.1", prerelease=True, assets=[])],
            current="1.0.0",
        )
        self.assertEqual(notice.version, "1.0.1")


class ReleaseFetchTests(unittest.TestCase):
    @staticmethod
    def page_url(page=1):
        return f"{updater.RELEASES_URL}?per_page=100&page={page}"

    def test_release_check_uses_api_version_with_immutable_field(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = updater.LATEST_RELEASE_URL
        response.read.return_value = b"{}"

        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            self.assertEqual(updater.fetch_latest_release(), {})

        request = urlopen.call_args.args[0]
        headers = {name.lower(): value for name, value in request.header_items()}
        self.assertEqual(headers["x-github-api-version"], "2026-03-10")

    def test_release_check_rejects_an_off_github_redirect(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.com/releases/latest"

        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_latest_release()

    def test_release_catalog_includes_prereleases(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = self.page_url()
        response.read.return_value = b"[]"

        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            self.assertEqual(updater.fetch_releases(), [])

        self.assertEqual(urlopen.call_args.args[0].full_url, self.page_url())

    def test_release_catalog_follows_full_pages(self):
        first = mock.MagicMock()
        first.__enter__.return_value = first
        first.geturl.return_value = self.page_url(1)
        first.read.return_value = json.dumps([release()] * 100).encode("utf-8")
        second = mock.MagicMock()
        second.__enter__.return_value = second
        second.geturl.return_value = self.page_url(2)
        second.read.return_value = json.dumps([release("2.0.0")]).encode("utf-8")

        with mock.patch.object(
            updater.urllib.request, "urlopen", side_effect=[first, second]
        ) as urlopen:
            releases = updater.fetch_releases()

        self.assertEqual(len(releases), 101)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(
            urlopen.call_args_list[1].args[0].full_url, self.page_url(2)
        )

    def test_release_catalog_requires_a_list(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = self.page_url()
        response.read.return_value = b"{}"
        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaisesRegex(updater.UpdateError, "invalid releases"):
                updater.fetch_releases()


class InstallerVerificationTests(unittest.TestCase):
    def test_sha256_verification_accepts_match_and_rejects_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Chunes-1.1.0-x64.msi"
            path.write_bytes(b"installer bytes")
            digest = hashlib.sha256(b"installer bytes").hexdigest()

            self.assertEqual(updater.verify_sha256(path, digest), digest)
            with self.assertRaises(updater.UpdateError):
                updater.verify_sha256(path, "00" * 32)

    def test_launch_occurs_only_after_digest_and_publisher_checks(self):
        asset = updater.ReleaseAsset(
            "1.1.0", "Chunes-1.1.0-x64.msi", "https://example.invalid", "ab", 1
        )
        events = []

        with (
            mock.patch.object(
                updater,
                "verify_sha256",
                side_effect=lambda path, digest: events.append("sha256"),
            ),
            mock.patch.object(
                updater,
                "verify_authenticode",
                side_effect=lambda path: events.append("authenticode")
                or "SignPath Foundation",
            ),
            mock.patch.object(
                updater,
                "verify_msi_identity",
                side_effect=lambda path, version: events.append(
                    f"identity:{version}"
                ),
            ),
            mock.patch.object(
                updater,
                "launch_install_helper",
                side_effect=lambda selected, path: events.append("helper"),
            ),
            mock.patch("builtins.print"),
        ):
            updater.verify_and_launch(asset, Path("installer.msi"))

        self.assertEqual(
            events,
            ["sha256", "authenticode", "identity:1.1.0", "helper"],
        )

    def test_digest_failure_never_checks_signature_or_launches(self):
        asset = mock.Mock(sha256="ab", filename="installer.msi")
        with (
            mock.patch.object(
                updater,
                "verify_sha256",
                side_effect=updater.UpdateError("bad digest"),
            ),
            mock.patch.object(updater, "verify_authenticode") as authenticode,
            mock.patch.object(updater, "verify_msi_identity") as identity,
            mock.patch.object(updater, "launch_install_helper") as helper,
        ):
            with self.assertRaises(updater.UpdateError):
                updater.verify_and_launch(asset, Path("installer.msi"))
        authenticode.assert_not_called()
        identity.assert_not_called()
        helper.assert_not_called()

    def test_untrusted_publisher_never_launches(self):
        asset = mock.Mock(sha256="ab", filename="installer.msi")
        with (
            mock.patch.object(updater, "verify_sha256"),
            mock.patch.object(
                updater,
                "verify_authenticode",
                side_effect=updater.UpdateError("wrong publisher"),
            ),
            mock.patch.object(updater, "verify_msi_identity") as identity,
            mock.patch.object(updater, "launch_install_helper") as helper,
        ):
            with self.assertRaises(updater.UpdateError):
                updater.verify_and_launch(asset, Path("installer.msi"))
        identity.assert_not_called()
        helper.assert_not_called()

    def test_msi_identity_requires_all_exact_properties(self):
        valid = {
            "ProductName": "Chunes",
            "ProductVersion": "1.1.0",
            "Manufacturer": "Chunes",
            "UpgradeCode": updater.EXPECTED_UPGRADE_CODE,
        }
        with mock.patch.object(
            updater, "_read_msi_properties", return_value=valid
        ):
            self.assertEqual(
                updater.verify_msi_identity("installer.msi", "1.1.0"), valid
            )

        for name in valid:
            changed = dict(valid)
            changed[name] = "unexpected"
            with self.subTest(name=name), mock.patch.object(
                updater, "_read_msi_properties", return_value=changed
            ):
                with self.assertRaises(updater.UpdateError):
                    updater.verify_msi_identity("installer.msi", "1.1.0")

    def test_identity_failure_never_starts_helper(self):
        asset = mock.Mock(sha256="ab", filename="installer.msi", version="1.1.0")
        with (
            mock.patch.object(updater, "verify_sha256"),
            mock.patch.object(
                updater,
                "verify_authenticode",
                return_value="SignPath Foundation",
            ),
            mock.patch.object(
                updater,
                "verify_msi_identity",
                side_effect=updater.UpdateError("wrong product"),
            ),
            mock.patch.object(updater, "launch_install_helper") as helper,
        ):
            with self.assertRaises(updater.UpdateError):
                updater.verify_and_launch(asset, Path("installer.msi"))
        helper.assert_not_called()

    def test_install_helper_receives_only_verified_identity_and_recovery_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            powershell = (
                root
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            powershell.parent.mkdir(parents=True)
            powershell.write_bytes(b"powershell")
            old_executable = root / "Chunes.exe"
            old_executable.write_bytes(b"old chunes")
            msi = root / "Chunes-1.1.0-x64.msi"
            msi.write_bytes(b"signed installer")
            asset = updater.ReleaseAsset(
                "1.1.0",
                msi.name,
                "https://example.invalid",
                hashlib.sha256(msi.read_bytes()).hexdigest(),
                msi.stat().st_size,
            )

            with (
                mock.patch.object(updater.sys, "frozen", True, create=True),
                mock.patch.object(updater.sys, "executable", str(old_executable)),
                mock.patch.dict(os.environ, {"SystemRoot": str(root)}),
                mock.patch.object(updater.os, "getpid", return_value=100),
                mock.patch.object(updater.os, "getppid", return_value=99),
                mock.patch.object(updater.subprocess, "Popen") as popen,
            ):
                updater.launch_install_helper(asset, msi)

        args = popen.call_args.args[0]
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(args[0], str(powershell))
        self.assertIn("-EncodedCommand", args)
        self.assertEqual(environment["CHUNES_UPDATE_VERSION"], "1.1.0")
        self.assertEqual(environment["CHUNES_PARENT_PIDS"], "100,99")
        self.assertEqual(
            environment["CHUNES_EXPECTED_UPGRADE_CODE"],
            updater.EXPECTED_UPGRADE_CODE,
        )
        self.assertEqual(
            environment["CHUNES_OLD_EXE_SHA256"],
            hashlib.sha256(b"old chunes").hexdigest(),
        )
        self.assertIn("Start-PreviousChunes", updater._UPDATE_HELPER_SCRIPT)
        self.assertIn("WaitForExit", updater._UPDATE_HELPER_SCRIPT)
        self.assertIn("ProductVersion", updater._UPDATE_HELPER_SCRIPT)
        self.assertIn("ExitCode -eq 3010", updater._UPDATE_HELPER_SCRIPT)
        reboot_check = updater._UPDATE_HELPER_SCRIPT.index("ExitCode -eq 3010")
        new_launch = updater._UPDATE_HELPER_SCRIPT.index(
            "Start-Process -FilePath $newExecutable"
        )
        self.assertLess(reboot_check, new_launch)
        self.assertIn("CHUNES_OLD_EXE_SHA256", updater._UPDATE_HELPER_SCRIPT)
        self.assertIn('/quiet /norestart', updater._UPDATE_HELPER_SCRIPT)
        self.assertEqual(
            updater._UPDATE_HELPER_SCRIPT.count(
                "Start-Process -FilePath $newExecutable"
            ),
            1,
        )

    @unittest.skipUnless(os.name == "nt", "Authenticode is Windows-only")
    def test_unsigned_file_is_rejected_by_windows_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsigned.msi"
            path.write_bytes(b"not a signed MSI")
            with self.assertRaises(updater.UpdateError):
                updater.verify_authenticode(path)


class UpdateControllerTests(unittest.TestCase):
    def test_update_prompt_states_signpath_verification_requirement(self):
        asset = mock.Mock(version="1.0.1")
        with mock.patch.object(updater, "_message", return_value=6) as message:
            self.assertTrue(updater._ask_download(asset))
        self.assertIn("SignPath Foundation verification", message.call_args.args[0])

    def test_pending_automatic_check_rechecks_opt_out_before_network(self):
        with (
            mock.patch.object(updater.time, "sleep"),
            mock.patch.object(
                updater.settings, "automatic_updates_enabled", return_value=False
            ),
            mock.patch.object(updater, "fetch_releases") as fetch,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=False, delay=8)
        fetch.assert_not_called()

    def test_signed_install_path_is_retained_but_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "Chunes-1.1.0-x64.msi"
            installer.write_bytes(b"verified")
            stop = mock.Mock()
            with (
                mock.patch.object(updater, "_ask_download", return_value=True),
                mock.patch.object(
                    updater, "download_asset", return_value=installer
                ),
                mock.patch.object(updater, "verify_and_launch"),
                mock.patch("builtins.print"),
            ):
                controller = updater.UpdateController(stop, "1.0.0")
                self.assertTrue(controller._offer_signed_install(release()))

            stop.assert_called_once_with()
            self.assertTrue(installer.exists())

    def test_newer_unsigned_prerelease_opens_browser_without_installing(self):
        on_install = mock.Mock()
        with (
            mock.patch.object(
                updater,
                "fetch_releases",
                return_value=[release("1.0.1", prerelease=True, assets=[])],
            ),
            mock.patch.object(updater, "open_release_page") as open_page,
            mock.patch.object(updater, "download_asset") as download,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(on_install, "1.0.0")
            controller._run(manual=False, delay=0)

        notice = open_page.call_args.args[0]
        self.assertEqual(notice.version, "1.0.1")
        self.assertEqual(
            notice.page_url,
            "https://github.com/getchunes/chunes/releases/tag/v1.0.1",
        )
        download.assert_not_called()
        on_install.assert_not_called()

    def test_automatic_failure_is_logged_but_not_shown(self):
        with (
            mock.patch.object(updater, "fetch_releases", return_value=[release()]),
            mock.patch.object(
                updater,
                "open_release_page",
                side_effect=updater.UpdateError("browser unavailable"),
            ),
            mock.patch.object(
                updater.settings, "automatic_updates_enabled", return_value=True
            ),
            mock.patch.object(updater, "_message") as message,
            mock.patch("builtins.print") as output,
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=False, delay=0)

        message.assert_not_called()
        self.assertTrue(
            any("Update failed" in str(call) for call in output.call_args_list)
        )

    def test_manual_failure_is_shown(self):
        with (
            mock.patch.object(
                updater,
                "fetch_releases",
                side_effect=updater.UpdateError("offline"),
            ),
            mock.patch.object(updater, "_message") as message,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=True, delay=0)

        message.assert_called_once()
        self.assertIn("Update failed", message.call_args.args[0])

    def test_manual_current_version_is_reported(self):
        with (
            mock.patch.object(
                updater, "fetch_releases", return_value=[release("1.0.0")]
            ),
            mock.patch.object(updater, "_message") as message,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=True, delay=0)

        message.assert_called_once()
        self.assertIn("up to date", message.call_args.args[0])

    def test_default_browser_failure_is_reported(self):
        notice = updater.ReleaseNotice(
            "1.0.1", "https://github.com/getchunes/chunes/releases/tag/v1.0.1"
        )
        with mock.patch.object(updater.webbrowser, "open", return_value=False):
            with self.assertRaises(updater.UpdateError):
                updater.open_release_page(notice)


if __name__ == "__main__":
    unittest.main()
