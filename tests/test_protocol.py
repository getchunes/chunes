import copy
import json
import unittest

import protocol


VALID_REPORT = {
    "enabled": True,
    "services": {"soundcloud": True, "youtubeMusic": True},
    "tabs": [
        {"host": "soundcloud.com", "mediaId": None, "title": "Song by Artist"},
        {
            "host": "music.youtube.com",
            "mediaId": "a1B2c3D4e5F",
            "title": "Track - Artist",
        },
    ],
}

# Apple Music's web player keeps the page name while playing, so its tab
# titles never contain the playing track.
APPLE_TAB = {
    "host": "music.apple.com",
    "mediaId": None,
    "title": "Daughter from Hell - Album by Gracie Abrams - Apple Music",
}


class ReportValidationTests(unittest.TestCase):
    def test_accepts_and_detaches_exact_payload(self):
        value = copy.deepcopy(VALID_REPORT)

        report = protocol.validate_report(value)
        value["services"]["soundcloud"] = False
        value["tabs"][0]["title"] = "Changed"

        self.assertEqual(report, VALID_REPORT)

    def test_rejects_non_exact_payloads(self):
        invalid = []

        missing_top_level = copy.deepcopy(VALID_REPORT)
        del missing_top_level["tabs"]
        invalid.append(missing_top_level)

        extra_top_level = copy.deepcopy(VALID_REPORT)
        extra_top_level["version"] = 1
        invalid.append(extra_top_level)

        numeric_master = copy.deepcopy(VALID_REPORT)
        numeric_master["enabled"] = 1
        invalid.append(numeric_master)

        missing_service = copy.deepcopy(VALID_REPORT)
        del missing_service["services"]["youtubeMusic"]
        invalid.append(missing_service)

        extra_service = copy.deepcopy(VALID_REPORT)
        extra_service["services"]["youtube"] = True
        invalid.append(extra_service)

        numeric_service = copy.deepcopy(VALID_REPORT)
        numeric_service["services"]["soundcloud"] = 1
        invalid.append(numeric_service)

        # Apple Music has no services flag on the wire; the v2 schema stays
        # exactly two service keys so older apps keep accepting reports.
        apple_service = copy.deepcopy(VALID_REPORT)
        apple_service["services"]["appleMusic"] = True
        invalid.append(apple_service)

        apple_media_id = copy.deepcopy(VALID_REPORT)
        apple_media_id["tabs"].append({**APPLE_TAB, "mediaId": "a1B2c3D4e5F"})
        invalid.append(apple_media_id)

        tabs_object = copy.deepcopy(VALID_REPORT)
        tabs_object["tabs"] = {}
        invalid.append(tabs_object)

        extra_tab_member = copy.deepcopy(VALID_REPORT)
        extra_tab_member["tabs"][0]["audible"] = True
        invalid.append(extra_tab_member)

        soundcloud_media_id = copy.deepcopy(VALID_REPORT)
        soundcloud_media_id["tabs"][0]["mediaId"] = "a1B2c3D4e5F"
        invalid.append(soundcloud_media_id)

        invalid_youtube_media_id = copy.deepcopy(VALID_REPORT)
        invalid_youtube_media_id["tabs"][1]["mediaId"] = "not-a-video-id"
        invalid.append(invalid_youtube_media_id)

        invalid_host = copy.deepcopy(VALID_REPORT)
        invalid_host["tabs"][0]["host"] = "SoundCloud.com:443"
        invalid.append(invalid_host)

        long_title = copy.deepcopy(VALID_REPORT)
        long_title["tabs"][0]["title"] = "x" * 513
        invalid.append(long_title)

        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(protocol.ProtocolError) as raised:
                    protocol.validate_report(value)
                self.assertEqual(raised.exception.status, 400)

    def test_rejects_duplicate_json_members_and_nonstandard_constants(self):
        bodies = [
            b'{"enabled":true,"enabled":false,"services":{},"tabs":[]}',
            (
                b'{"enabled":true,"services":{"soundcloud":true,'
                b'"soundcloud":false,"youtubeMusic":true},"tabs":[]}'
            ),
            (
                b'{"enabled":true,"services":{"soundcloud":true,'
                b'"youtubeMusic":true},"tabs":[{"host":"soundcloud.com",'
                b'"title":NaN}]}'
            ),
        ]

        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(protocol.ProtocolError) as raised:
                    protocol.parse_report_body(body)
                self.assertEqual(raised.exception.status, 400)

    def test_rejects_body_limits(self):
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_report_body(b"")
        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_report_body(b" " * (protocol.MAX_BODY_BYTES + 1))

        too_many = copy.deepcopy(VALID_REPORT)
        too_many["tabs"] = [
            {"host": "soundcloud.com", "mediaId": None, "title": "Song"}
            for _ in range(protocol.MAX_TABS + 1)
        ]
        with self.assertRaises(protocol.ProtocolError):
            protocol.validate_report(too_many)

    def test_parses_valid_utf8_json(self):
        body = json.dumps(VALID_REPORT, ensure_ascii=False).encode("utf-8")
        self.assertEqual(protocol.parse_report_body(body), VALID_REPORT)

    def test_accepts_apple_music_tabs_in_v2_reports(self):
        value = copy.deepcopy(VALID_REPORT)
        value["tabs"].append(copy.deepcopy(APPLE_TAB))

        report = protocol.validate_report(value)

        self.assertIn(APPLE_TAB, report["tabs"])


class RequestValidationTests(unittest.TestCase):
    @staticmethod
    def request(content_type="application/json", path="/tabs", method="POST"):
        body_length = len(json.dumps(VALID_REPORT).encode("utf-8"))
        return (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: 127.0.0.1:52846\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {body_length}"
        ).encode("ascii")

    def test_accepts_only_exact_json_media_type_for_tabs(self):
        request = protocol.parse_request_head(self.request())
        self.assertEqual(request.action, "report")
        self.assertGreater(request.content_length, 0)

        for content_type in (
            "",
            "text/plain",
            "application/json; charset=utf-8",
            "application/problem+json",
        ):
            with self.subTest(content_type=content_type):
                with self.assertRaises(protocol.ProtocolError) as raised:
                    protocol.parse_request_head(self.request(content_type))
                self.assertEqual(raised.exception.status, 415)

    def test_rejects_wrong_route_method_host_and_framing(self):
        cases = [
            (self.request(path="/tabs?x=1"), 404),
            (self.request(method="PUT"), 405),
            (
                self.request().replace(
                    b"Host: 127.0.0.1:52846", b"Host: example.com"
                ),
                403,
            ),
            (self.request().replace(b"Content-Length:", b"X-Length:"), 411),
            (
                self.request()
                + b"\r\nTransfer-Encoding: chunked",
                400,
            ),
            (self.request() + b"\r\nContent-Length: 1", 400),
            (self.request() + b"\r\n Folded: value", 400),
            (self.request() + b"\r\nX-Control: value\x7f", 400),
        ]

        for request, status in cases:
            with self.subTest(status=status, request=request):
                with self.assertRaises(protocol.ProtocolError) as raised:
                    protocol.parse_request_head(request)
                self.assertEqual(raised.exception.status, status)

    def test_state_route_has_no_request_body(self):
        request = protocol.parse_request_head(
            b"GET /state HTTP/1.1\r\nHost: localhost:52846"
        )
        self.assertEqual(request.action, "state")

        with self.assertRaises(protocol.ProtocolError):
            protocol.parse_request_head(
                b"GET /state HTTP/1.1\r\nHost: localhost\r\nContent-Length: 1"
            )


class ServicePolicyTests(unittest.TestCase):
    def test_service_identity_and_labels_cover_both_services(self):
        self.assertEqual(protocol.service_for_host("soundcloud.com"), "soundcloud")
        self.assertEqual(
            protocol.service_for_host("www.soundcloud.com"), "soundcloud"
        )
        self.assertEqual(
            protocol.service_for_host("music.youtube.com"), "youtubeMusic"
        )
        self.assertEqual(
            protocol.service_label_for_host("www.soundcloud.com"), "SoundCloud"
        )
        self.assertEqual(
            protocol.service_label_for_host("music.youtube.com"),
            "YouTube Music",
        )

    def test_master_service_and_paused_tabs_suppress_browser_activity(self):
        report = copy.deepcopy(VALID_REPORT)
        browser = "Google.Chrome_123"

        self.assertTrue(
            protocol.browser_track_is_allowed(browser, report, "soundcloud.com")
        )
        self.assertFalse(protocol.browser_track_is_allowed(browser, report, None))

        report["enabled"] = False
        self.assertFalse(
            protocol.browser_track_is_allowed(browser, report, "soundcloud.com")
        )

        report["enabled"] = True
        report["services"]["youtubeMusic"] = False
        self.assertFalse(
            protocol.browser_track_is_allowed(
                browser, report, "music.youtube.com"
            )
        )

        self.assertTrue(
            protocol.browser_track_is_allowed("MusicPlayer.exe", report, None)
        )
        self.assertTrue(protocol.browser_track_is_allowed(browser, None, None))

    def test_enabled_tabs_filters_master_and_service_settings(self):
        report = copy.deepcopy(VALID_REPORT)
        report["services"]["youtubeMusic"] = False
        self.assertEqual(
            protocol.enabled_tabs(report),
            [
                {
                    "host": "soundcloud.com",
                    "mediaId": None,
                    "title": "Song by Artist",
                }
            ],
        )
        report["enabled"] = False
        self.assertEqual(protocol.enabled_tabs(report), [])

    def test_apple_music_identity_label_and_default_enablement(self):
        self.assertEqual(
            protocol.service_for_host("music.apple.com"), "appleMusic"
        )
        self.assertEqual(
            protocol.service_label_for_host("music.apple.com"), "Apple Music"
        )
        self.assertIsNone(protocol.service_for_host("www.apple.com"))

        report = copy.deepcopy(VALID_REPORT)
        report["tabs"].append(copy.deepcopy(APPLE_TAB))
        browser = "Google.Chrome_123"

        # No services flag exists for Apple Music: the extension only reports
        # its tabs while the toggle is on, so a reported tab means enabled.
        self.assertTrue(
            protocol.browser_track_is_allowed(browser, report, "music.apple.com")
        )
        self.assertIn(APPLE_TAB, protocol.enabled_tabs(report))

        report["enabled"] = False
        self.assertFalse(
            protocol.browser_track_is_allowed(browser, report, "music.apple.com")
        )

    def test_untitled_service_tab_attributes_only_apple_music(self):
        report = copy.deepcopy(VALID_REPORT)
        self.assertIsNone(protocol.untitled_service_tab(report))
        self.assertIsNone(protocol.untitled_service_tab(None))

        report["tabs"].append(copy.deepcopy(APPLE_TAB))
        self.assertEqual(protocol.untitled_service_tab(report), APPLE_TAB)

        report["enabled"] = False
        self.assertIsNone(protocol.untitled_service_tab(report))

    def test_report_freshness_is_bounded(self):
        self.assertTrue(protocol.report_is_fresh(100, now=100))
        self.assertTrue(protocol.report_is_fresh(100, now=190))
        self.assertFalse(protocol.report_is_fresh(100, now=191))
        self.assertFalse(protocol.report_is_fresh(101, now=100))
        self.assertFalse(protocol.report_is_fresh(0, now=100))


if __name__ == "__main__":
    unittest.main()
