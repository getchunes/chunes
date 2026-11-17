import json
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


class VersionTests(unittest.TestCase):
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


class UpdateControllerTests(unittest.TestCase):
    def test_pending_automatic_check_rechecks_opt_out_before_network(self):
        with (
            mock.patch.object(updater.time, "sleep"),
            mock.patch.object(
                updater.settings, "automatic_updates_enabled", return_value=False
            ),
            mock.patch.object(updater, "fetch_releases") as fetch,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController("1.0.0")
            controller._run(manual=False, delay=8)
        fetch.assert_not_called()

    def test_newer_unsigned_prerelease_opens_its_release_page(self):
        with (
            mock.patch.object(
                updater,
                "fetch_releases",
                return_value=[release("1.0.1", prerelease=True, assets=[])],
            ),
            mock.patch.object(updater, "open_release_page") as open_page,
            mock.patch("builtins.print"),
        ):
            controller = updater.UpdateController("1.0.0")
            controller._run(manual=False, delay=0)

        notice = open_page.call_args.args[0]
        self.assertEqual(notice.version, "1.0.1")
        self.assertEqual(
            notice.page_url,
            "https://github.com/getchunes/chunes/releases/tag/v1.0.1",
        )

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
            controller = updater.UpdateController("1.0.0")
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
            controller = updater.UpdateController("1.0.0")
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
            controller = updater.UpdateController("1.0.0")
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
