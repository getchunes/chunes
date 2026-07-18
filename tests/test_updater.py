import hashlib
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
        asset = updater.select_release_asset(release(), current="1.0.0")
        self.assertEqual(asset.version, "1.1.0")
        self.assertEqual(asset.filename, "Chunes-1.1.0-x64.msi")
        self.assertEqual(asset.sha256, "ab" * 32)
        self.assertEqual(asset.size, 1234)

    def test_returns_none_when_current_is_latest(self):
        self.assertIsNone(
            updater.select_release_asset(release("1.0.0"), current="1.0.0")
        )

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


class ReleaseFetchTests(unittest.TestCase):
    def test_release_check_rejects_an_off_github_redirect(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = "https://example.com/releases/latest"

        with mock.patch.object(
            updater.urllib.request, "urlopen", return_value=response
        ):
            with self.assertRaises(updater.UpdateError):
                updater.fetch_latest_release()


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
        self.assertIn("ExitCode -notin @(0, 3010)", updater._UPDATE_HELPER_SCRIPT)
        self.assertIn("CHUNES_OLD_EXE_SHA256", updater._UPDATE_HELPER_SCRIPT)

    @unittest.skipUnless(os.name == "nt", "Authenticode is Windows-only")
    def test_unsigned_file_is_rejected_by_windows_trust(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsigned.msi"
            path.write_bytes(b"not a signed MSI")
            with self.assertRaises(updater.UpdateError):
                updater.verify_authenticode(path)


class UpdateControllerTests(unittest.TestCase):
    def test_pending_automatic_check_rechecks_opt_out_before_network(self):
        with (
            mock.patch.object(updater.time, "sleep"),
            mock.patch.object(
                updater.settings, "automatic_updates_enabled", return_value=False
            ),
            mock.patch.object(updater, "fetch_latest_release") as fetch,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=False, delay=8)
        fetch.assert_not_called()

    def test_successful_helper_handoff_stops_the_running_app(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "Chunes-1.1.0-x64.msi"
            installer.write_bytes(b"verified")
            stop = mock.Mock()
            with (
                mock.patch.object(
                    updater, "fetch_latest_release", return_value=release()
                ),
                mock.patch.object(updater, "_ask_download", return_value=True),
                mock.patch.object(
                    updater, "download_asset", return_value=installer
                ),
                mock.patch.object(updater, "verify_and_launch"),
                mock.patch("builtins.print"),
            ):
                controller = updater.UpdateController(stop, "1.0.0")
                controller._run(manual=True, delay=0)

            stop.assert_called_once_with()
            self.assertTrue(installer.exists())

    def test_automatic_failure_is_logged_but_not_shown(self):
        with tempfile.TemporaryDirectory() as directory:
            installer = Path(directory) / "Chunes-1.1.0-x64.msi"
            installer.write_bytes(b"bad")
            with (
                mock.patch.object(updater, "fetch_latest_release", return_value=release()),
                mock.patch.object(updater, "_ask_download", return_value=True),
                mock.patch.object(updater, "download_asset", return_value=installer),
                mock.patch.object(
                    updater.settings, "automatic_updates_enabled", return_value=True
                ),
                mock.patch.object(
                    updater,
                    "verify_and_launch",
                    side_effect=updater.UpdateError("unsigned"),
                ),
                mock.patch.object(updater, "_message") as message,
                mock.patch("builtins.print") as output,
            ):
                controller = updater.UpdateController(mock.Mock(), "1.0.0")
                controller._run(manual=False, delay=0)

            message.assert_not_called()
            self.assertFalse(installer.exists())
            self.assertTrue(
                any("Update failed" in str(call) for call in output.call_args_list)
            )

    def test_manual_failure_is_shown(self):
        with (
            mock.patch.object(
                updater,
                "fetch_latest_release",
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
                updater, "fetch_latest_release", return_value=release("1.0.0")
            ),
            mock.patch.object(updater, "_message") as message,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController(mock.Mock(), "1.0.0")
            controller._run(manual=True, delay=0)

        message.assert_called_once()
        self.assertIn("up to date", message.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
