import asyncio
import json
import struct
import unittest
from unittest import mock

import presence


def rpc_frame(opcode, payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("<II", opcode, len(body)) + body


class FakeReader:
    def __init__(self, data=b"", error=None):
        self.data = data
        self.error = error

    async def readexactly(self, length):
        if self.error is not None:
            raise self.error
        if len(self.data) < length:
            partial, self.data = self.data, b""
            raise asyncio.IncompleteReadError(partial, length)
        result, self.data = self.data[:length], self.data[length:]
        return result


class FakeClient:
    response_timeout = 0.1

    def __init__(self, data=b"", error=None):
        self.sock_reader = FakeReader(data, error)
        self.sent = []

    def send_data(self, opcode, payload):
        self.sent.append((opcode, payload))


class FakeWriter:
    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


def tab_report_request(report):
    body = json.dumps(report).encode()
    head = (
        "POST /tabs HTTP/1.1\r\n"
        "Host: 127.0.0.1:52846\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("ascii")
    return head + body


async def _run_tab_report(report):
    reader = asyncio.StreamReader()
    reader.feed_data(tab_report_request(report))
    reader.feed_eof()
    writer = FakeWriter()
    await presence._handle_tab_report(reader, writer)
    return writer.written


def send_tab_report(report):
    written = asyncio.run(_run_tab_report(report))
    header, _, payload = written.partition(b"\r\n\r\n")
    return header, json.loads(payload)


class DiscordFrameTests(unittest.TestCase):
    def read(self, *frames, error=None):
        client = FakeClient(b"".join(frames), error)
        return asyncio.run(presence._read_output(client)), client

    def test_accepts_nonce_command_response_without_evt(self):
        payload = {"cmd": "SET_ACTIVITY", "data": {}, "nonce": "one"}
        result, _ = self.read(rpc_frame(presence.RPC_FRAME, payload))
        self.assertEqual(result, payload)

    def test_accepts_nonce_command_response_with_null_evt(self):
        payload = {
            "cmd": "SET_ACTIVITY",
            "data": {},
            "evt": None,
            "nonce": "two",
        }
        result, _ = self.read(rpc_frame(presence.RPC_FRAME, payload))
        self.assertEqual(result, payload)

    def test_ignores_dispatch_event_then_returns_command_response(self):
        event = {"cmd": "DISPATCH", "evt": "READY", "data": {}}
        response = {"cmd": "SET_ACTIVITY", "data": {}, "nonce": "three"}
        with mock.patch("builtins.print"):
            result, _ = self.read(
                rpc_frame(presence.RPC_FRAME, event),
                rpc_frame(presence.RPC_FRAME, response),
            )
        self.assertEqual(result, response)

    def test_error_and_close_frames_fail_immediately(self):
        error = {
            "cmd": "SET_ACTIVITY",
            "evt": "ERROR",
            "data": {"message": "not allowed"},
            "nonce": "four",
        }
        with self.assertRaises(presence._base.ServerError):
            self.read(rpc_frame(presence.RPC_FRAME, error))
        with self.assertRaises(presence._base.PipeClosed):
            self.read(rpc_frame(presence.RPC_CLOSE, {"message": "bye"}))

    def test_ping_is_answered_before_command_response(self):
        ping = {"time": 123}
        response = {"cmd": "SET_ACTIVITY", "data": {}, "nonce": "five"}
        result, client = self.read(
            rpc_frame(presence.RPC_PING, ping),
            rpc_frame(presence.RPC_FRAME, response),
        )
        self.assertEqual(result, response)
        self.assertEqual(client.sent, [(presence.RPC_PONG, ping)])

    def test_pong_is_ignored_before_command_response(self):
        response = {"cmd": "SET_ACTIVITY", "data": {}, "nonce": "six"}
        result, client = self.read(
            rpc_frame(presence.RPC_PONG, {"time": 123}),
            rpc_frame(presence.RPC_FRAME, response),
        )
        self.assertEqual(result, response)
        self.assertEqual(client.sent, [])

    def test_incomplete_and_timed_out_reads_are_mapped(self):
        with self.assertRaises(presence._base.PipeClosed):
            self.read(b"short")
        with self.assertRaises(presence._base.ResponseTimeout):
            self.read(error=asyncio.TimeoutError())
        with self.assertRaises(presence._base.PipeClosed):
            self.read(error=ConnectionResetError())


class DesktopProtocolTests(unittest.TestCase):
    def test_success_responses_advertise_requested_protocol(self):
        for status in (200, 204):
            for version in (3, 4):
                with self.subTest(status=status, version=version):
                    reply = presence._http_reply(
                        status, b"{}" if status == 200 else b"", version
                    )
                    self.assertIn(f"X-Chunes-Protocol: {version}\r\n".encode(), reply)
        self.assertNotIn(b"X-Chunes-Protocol", presence._http_reply(400))

    def test_tab_report_response_carries_current_track_and_host(self):
        presence.set_status(track="Real Song - Real Artist", host="music.youtube.com")
        report = {
            "enabled": True,
            "services": {"appleMusic": False, "soundcloud": False, "youtubeMusic": True},
            "tabs": [],
        }
        header, payload = send_tab_report(report)
        self.assertIn(b"200 OK", header)
        self.assertEqual(
            payload,
            {
                "status": "ok",
                "track": "Real Song - Real Artist",
                "host": "music.youtube.com",
            },
        )

    def test_tab_report_response_omits_track_and_host_when_stopped(self):
        presence.set_status(track=None, host=None)
        report = {
            "enabled": True,
            "services": {"appleMusic": False, "soundcloud": False, "youtubeMusic": False},
            "tabs": [],
        }
        _, payload = send_tab_report(report)
        self.assertEqual(
            payload, {"status": "ok", "track": None, "host": None}
        )

    def test_protocol4_page_metadata_owns_provider_track_identity(self):
        tab = {
            "host": "music.youtube.com",
            "mediaId": "a1B2c3D4e5F",
            "title": "Previous Song | YouTube Music",
            "metadata": {"title": "Next Song", "artist": "Next Artist", "artwork": None},
        }

        self.assertEqual(
            presence.protocol4_page_track(tab, 4), ("Next Song", "Next Artist")
        )
        self.assertIsNone(presence.protocol4_page_track(tab, 3))

    def test_classification_fallback_and_labels_cover_both_services(self):
        report = {
            "enabled": True,
            "services": {"soundcloud": True, "youtubeMusic": True},
            "tabs": [
                {
                    "host": "soundcloud.com",
                    "mediaId": None,
                    "title": "Cloud Song by Cloud Artist",
                },
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "Video Song | YouTube Music",
                },
            ],
        }
        self.assertEqual(
            presence.classify_host("Cloud Song", report), "soundcloud.com"
        )
        self.assertEqual(
            presence.classify_host("Video Song", report), "music.youtube.com"
        )
        self.assertEqual(
            presence.fallback_track(report),
            ("Cloud Song", "Cloud Artist", "soundcloud.com", None),
        )
        youtube_report = dict(report, tabs=[report["tabs"][1]])
        self.assertEqual(
            presence.fallback_track(youtube_report),
            (
                "Video Song",
                "",
                "music.youtube.com",
                "a1B2c3D4e5F",
            ),
        )
        youtube_dash_report = {
            "enabled": True,
            "services": {"soundcloud": True, "youtubeMusic": True},
            "tabs": [
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "My Track - My Artist - YouTube Music",
                }
            ],
        }
        self.assertEqual(
            presence.fallback_track(youtube_dash_report),
            (
                "My Track",
                "My Artist",
                "music.youtube.com",
                "a1B2c3D4e5F",
            ),
        )
        self.assertEqual(
            presence.protocol.service_label_for_host("soundcloud.com"),
            "SoundCloud",
        )
        self.assertEqual(
            presence.protocol.service_label_for_host("music.youtube.com"),
            "YouTube Music",
        )

    def test_apple_music_is_attributed_by_tab_presence_not_title(self):
        report = {
            "enabled": True,
            "services": {
                "appleMusic": True,
                "soundcloud": True,
                "youtubeMusic": True,
            },
            "tabs": [
                {
                    "host": "music.apple.com",
                    "mediaId": None,
                    "title": "Album Name - Album by Some Artist - Apple Music",
                }
            ],
        }

        # The web player keeps the page name while playing, so the playing
        # title never matches the tab title and no fallback track can be
        # rebuilt from it; attribution comes from the audible tab instead.
        self.assertIsNone(presence.classify_host("Real Song", report))
        self.assertIsNone(presence.fallback_track(report))
        self.assertEqual(
            presence.protocol.untitled_service_tab(report), report["tabs"][0]
        )
        self.assertEqual(
            presence.protocol.service_label_for_host("music.apple.com"),
            "Apple Music",
        )

        report["services"]["appleMusic"] = False
        self.assertIsNone(presence.protocol.untitled_service_tab(report))

    def test_fallback_ignores_youtube_music_generic_placeholder_title(self):
        # The tab title lags a beat behind real playback after switching
        # tracks/providers; until it updates past the bare "YouTube Music"
        # placeholder, fallback_track must not publish it as a fake track.
        generic_report = {
            "enabled": True,
            "services": {"youtubeMusic": True},
            "tabs": [
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "YouTube Music",
                }
            ],
        }
        self.assertIsNone(presence.fallback_track(generic_report))

        real_report = dict(
            generic_report,
            tabs=[
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "Real Song | YouTube Music",
                }
            ],
        )
        self.assertEqual(
            presence.fallback_track(real_report),
            ("Real Song", "", "music.youtube.com", "a1B2c3D4e5F"),
        )

    def test_fallback_switches_cleanly_from_soundcloud_to_youtube_music(self):
        # Reproduces a provider switch: SoundCloud playing, then stopped and
        # replaced by an audible YouTube Music tab whose title hasn't caught
        # up yet. No stale SoundCloud data or YTM placeholder junk should
        # leak into the result at either step.
        soundcloud_report = {
            "enabled": True,
            "services": {"soundcloud": True, "youtubeMusic": True},
            "tabs": [
                {
                    "host": "soundcloud.com",
                    "mediaId": None,
                    "title": "Cloud Song by Cloud Artist",
                }
            ],
        }
        self.assertEqual(
            presence.fallback_track(soundcloud_report),
            ("Cloud Song", "Cloud Artist", "soundcloud.com", None),
        )

        mid_switch_report = {
            "enabled": True,
            "services": {"soundcloud": True, "youtubeMusic": True},
            "tabs": [
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "YouTube Music",
                }
            ],
        }
        self.assertIsNone(presence.fallback_track(mid_switch_report))

        settled_report = dict(
            mid_switch_report,
            tabs=[
                {
                    "host": "music.youtube.com",
                    "mediaId": "a1B2c3D4e5F",
                    "title": "New Song | YouTube Music",
                }
            ],
        )
        self.assertEqual(
            presence.fallback_track(settled_report),
            ("New Song", "", "music.youtube.com", "a1B2c3D4e5F"),
        )


class ArtworkTests(unittest.TestCase):
    def setUp(self):
        presence._artwork_cache.clear()

    def test_page_metadata_artwork_needs_no_desktop_network_request(self):
        artwork = "https://i.ytimg.com/vi/a1B2c3D4e5F/hqdefault.jpg"
        with mock.patch.object(presence, "_http_get") as get:
            self.assertEqual(
                presence.find_artwork(
                    "Track",
                    "Artist",
                    "music.youtube.com",
                    "a1B2c3D4e5F",
                    metadata={"title": "Track", "artist": "Artist", "artwork": artwork},
                ),
                artwork,
            )
        get.assert_not_called()

    def test_apple_music_artwork_comes_from_itunes_search_only(self):
        small = (
            "https://is1-ssl.mzstatic.com/image/thumb/Music/cover/100x100bb.jpg"
        )
        response = json.dumps(
            {
                "results": [
                    {"trackName": "Real Song", "artworkUrl100": small},
                ]
            }
        )
        with mock.patch.object(presence, "_http_get", return_value=response) as get:
            art = presence.find_artwork(
                "Real Song", "Some Artist", "music.apple.com", None, "brave"
            )

        self.assertEqual(
            art,
            "https://is1-ssl.mzstatic.com/image/thumb/Music/cover/500x500bb.jpg",
        )

    def test_get_playing_track_ignores_stale_last_updated_time(self):
        from datetime import datetime, timezone, timedelta
        fake_session = mock.Mock()
        fake_session.source_app_user_model_id = "msedge.exe"
        playback_info = mock.Mock()
        playback_info.playback_status = presence.PlaybackStatus.PLAYING
        fake_session.get_playback_info.return_value = playback_info

        props = mock.Mock()
        props.title = "Hot In Herre"
        props.artist = "Nelly"
        fake_session.try_get_media_properties_async = mock.AsyncMock(return_value=props)

        timeline = mock.Mock()
        timeline.position = timedelta(seconds=0)
        timeline.end_time = timedelta(seconds=228)
        # last_updated_time is 300 seconds (5 mins) in the past
        timeline.last_updated_time = datetime.now(timezone.utc) - timedelta(seconds=300)
        fake_session.get_timeline_properties.return_value = timeline

        fake_mgr = mock.Mock()
        fake_mgr.get_sessions.return_value = [fake_session]

        with mock.patch.object(presence.SessionManager, "request_async", mock.AsyncMock(return_value=fake_mgr)):
            result = asyncio.run(presence.get_playing_track(["msedge"]))
            self.assertIsNotNone(result)
            title, artist, pos, dur, source = result
            self.assertEqual(title, "Hot In Herre")
            self.assertEqual(artist, "Nelly")
            self.assertEqual(pos, 0.0)  # Should NOT be 300+ seconds!
            self.assertEqual(dur, 228.0)

    def test_apple_music_artwork_failure_yields_no_art(self):
        with mock.patch.object(presence, "_http_get", side_effect=OSError("down")):
            self.assertIsNone(
                presence.find_artwork(
                    "Real Song", "Some Artist", "music.apple.com", None, "brave"
                )
            )

    def test_unidentified_browser_track_is_not_sent_to_soundcloud(self):
        with mock.patch.object(presence, "_legacy_soundcloud_info") as soundcloud:
            self.assertIsNone(
                presence.find_artwork(
                    "Video", "Channel", source="Google.Chrome_123"
                )
            )
        soundcloud.assert_not_called()


class TitleMatchTests(unittest.TestCase):
    def test_exact_and_normalized_titles_match(self):
        self.assertTrue(presence._titles_match("Real Song", "real song"))
        self.assertTrue(presence._titles_match("It's Mine", "It’s Mine"))

    def test_high_coverage_substring_matches(self):
        # A media session reports "HUMBLE" for the track spelled "HUMBLE.":
        # the shorter covers 6/7 of the longer, above the floor.
        self.assertTrue(presence._titles_match("HUMBLE", "HUMBLE."))

    def test_low_coverage_substring_is_rejected(self):
        # "Gimme Dat" covers only 0.64 of "Gimme Dat Ting".
        self.assertFalse(presence._titles_match("Gimme Dat Ting", "Gimme Dat"))

    def test_unrelated_titles_do_not_match(self):
        self.assertFalse(presence._titles_match("365", "party 4 u"))

    def test_empty_titles_never_match(self):
        self.assertFalse(presence._titles_match("", "Song"))
        self.assertFalse(presence._titles_match("Song", ""))


class ProviderDurationGuardTests(unittest.TestCase):
    def setUp(self):
        presence._artwork_cache.clear()

    def test_apple_duration_not_taken_from_unmatched_result(self):
        # The only result's title matches too loosely to trust its length;
        # its artwork may still be adopted, its duration must not be.
        response = json.dumps(
            {
                "results": [
                    {
                        "trackName": "Gimme Dat",
                        "trackTimeMillis": 204000,
                        "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg",
                    }
                ]
            }
        )
        with mock.patch.object(presence, "_http_get", return_value=response):
            art, dur = presence._find_apple_music_info("Gimme Dat Ting", "Davido")
        self.assertEqual(dur, 0.0)
        self.assertEqual(art, "https://is1.mzstatic.com/a/500x500bb.jpg")

    def test_apple_duration_taken_only_from_matched_result(self):
        response = json.dumps(
            {
                "results": [
                    {
                        "trackName": "Unrelated",
                        "trackTimeMillis": 999000,
                        "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg",
                    },
                    {
                        "trackName": "Real Song",
                        "trackTimeMillis": 180000,
                        "artworkUrl100": "https://is1.mzstatic.com/b/100x100bb.jpg",
                    },
                ]
            }
        )
        with mock.patch.object(presence, "_http_get", return_value=response):
            art, dur = presence._find_apple_music_info("Real Song", "Artist")
        self.assertEqual(dur, 180.0)
        self.assertEqual(art, "https://is1.mzstatic.com/b/500x500bb.jpg")

    def test_soundcloud_page_metadata_has_no_duration_guess(self):
        art, dur = presence.find_artwork_and_info(
            "Gimme Dat Ting",
            "Davido",
            "soundcloud.com",
            metadata={
                "title": "Gimme Dat Ting",
                "artist": "Davido",
                "artwork": "https://i1.sndcdn.com/x-large.jpg",
            },
        )
        self.assertEqual(dur, 0.0)
        self.assertEqual(art, "https://i1.sndcdn.com/x-large.jpg")

    def test_zero_background_position_keeps_provider_anchor(self):
        seen = {("Track", "Artist"): (1_000, 180.0)}
        self.assertEqual(
            presence.provider_duration_start("Track", "Artist", 0.0, 180.0, seen, 1_004),
            1_000,
        )
        self.assertEqual(
            presence.provider_duration_start("Track", "Artist", 0.0, 180.0, seen, 1_211),
            1_211,
        )

    def test_stale_low_position_keeps_provider_anchor(self):
        seen = {("Track", "Artist"): (1_000, 180.0)}
        self.assertEqual(
            presence.provider_duration_start("Track", "Artist", 5.0, 180.0, seen, 1_020),
            1_000,
        )


class AppleTimingHelperTests(unittest.TestCase):
    def test_anchor_start_is_stable_across_polls(self):
        anchors = {}
        key = ("Song", "Artist")
        first = presence.apple_track_start(key, 1000.0, anchors, None)
        # A later poll with a later wall clock keeps the original anchor, so
        # the elapsed bar grows 1:1 instead of following Apple's counter.
        again = presence.apple_track_start(key, 1200.0, anchors, None)
        self.assertEqual(first, 1000)
        self.assertEqual(again, 1000)

    def test_new_track_without_previous_anchors_at_now(self):
        anchors = {}
        self.assertEqual(
            presence.apple_track_start(("B", "x"), 1200.0, anchors, None), 1200
        )

    def test_gapless_change_back_dates_to_previous_track_end(self):
        anchors = {}
        # Previous track started at 1000 and is 180s long, due to end at 1180;
        # the new track is noticed 4s later at 1184 (natural detection lag).
        start = presence.apple_track_start(("B", "x"), 1184.0, anchors, (1000, 180.0))
        self.assertEqual(start, 1180)

    def test_skip_prediction_in_the_future_falls_back_to_now(self):
        anchors = {}
        # The previous track would end at 1180, but the user skipped ahead, so
        # now (1100) precedes the predicted end: anchor fresh instead.
        start = presence.apple_track_start(("B", "x"), 1100.0, anchors, (1000, 180.0))
        self.assertEqual(start, 1100)

    def test_large_gap_prediction_falls_back_to_now(self):
        anchors = {}
        # The previous track ended at 1180 but the next starts 60s later (a
        # pause), well outside the gapless margin: anchor fresh.
        start = presence.apple_track_start(("B", "x"), 1240.0, anchors, (1000, 180.0))
        self.assertEqual(start, 1240)

    def test_prediction_requires_a_known_previous_duration(self):
        anchors = {}
        # The previous track's duration was never locked, so its end is unknown.
        start = presence.apple_track_start(("B", "x"), 1184.0, anchors, (1000, 0.0))
        self.assertEqual(start, 1184)

    def test_anchor_dict_is_capped(self):
        anchors = {}
        for i in range(150):
            presence.apple_track_start((f"t{i}", ""), 1000.0 + i, anchors, None)
        self.assertLessEqual(len(anchors), 100)

    def test_locked_duration_prefers_itunes_and_holds(self):
        locks = {}
        key = ("Song", "Artist")
        # First poll: GSMTC duration still 0, iTunes has the real length.
        self.assertEqual(presence.apple_locked_duration(key, 0.0, 200.0, locks), 200.0)
        # Later poll: GSMTC flips to a bogus value; the locked length holds.
        self.assertEqual(presence.apple_locked_duration(key, 373.0, 373.0, locks), 200.0)

    def test_locked_duration_falls_back_to_gsmtc_when_itunes_absent(self):
        locks = {}
        key = ("Song", "Artist")
        # No iTunes match and no GSMTC duration yet: nothing to lock.
        self.assertEqual(presence.apple_locked_duration(key, 0.0, 0.0, locks), 0.0)
        self.assertNotIn(key, locks)
        # GSMTC populates: lock the first non-zero value against later flips.
        self.assertEqual(presence.apple_locked_duration(key, 250.0, 0.0, locks), 250.0)
        self.assertEqual(presence.apple_locked_duration(key, 999.0, 0.0, locks), 250.0)


class ResolveTabTests(unittest.TestCase):
    APPLE = {
        "host": "music.apple.com",
        "mediaId": None,
        "title": "Album - Album by Artist - Apple Music",
    }
    YT_VIDEO = {
        "host": "youtube.com",
        "mediaId": None,
        "title": "Cool Clip - YouTube",
    }

    def _report(self, tabs, **services):
        base = {"appleMusic": True, "soundcloud": True, "youtubeMusic": True}
        base.update(services)
        return {"enabled": True, "services": base, "tabs": tabs}

    def test_apple_attributed_when_only_apple_is_audible(self):
        report = self._report([self.APPLE])
        self.assertEqual(
            presence.resolve_tab("Real Song", "Google.Chrome_1", report), self.APPLE
        )

    def test_youtube_video_is_never_published_as_apple(self):
        report = self._report([self.APPLE, self.YT_VIDEO])
        # The media session reports the video's title while Apple is the only
        # enabled tab; it must not be attributed to Apple Music.
        self.assertIsNone(
            presence.resolve_tab("Cool Clip", "Google.Chrome_1", report)
        )
        # A title matching nothing must also stay unattributed while the
        # blocked video is audible.
        self.assertIsNone(
            presence.resolve_tab("Mystery Title", "Google.Chrome_1", report)
        )

    def test_matched_music_tab_resolves_despite_a_co_audible_video(self):
        soundcloud = {
            "host": "soundcloud.com",
            "mediaId": None,
            "title": "Cloud Song by Cloud Artist",
        }
        report = self._report([soundcloud, self.YT_VIDEO])
        self.assertEqual(
            presence.resolve_tab("Cloud Song", "Google.Chrome_1", report),
            soundcloud,
        )

    def test_generic_ytm_transient_still_attributes_without_other_audio(self):
        ytm = {
            "host": "music.youtube.com",
            "mediaId": "a1B2c3D4e5F",
            "title": "YouTube Music",
        }
        report = self._report([ytm])
        self.assertEqual(
            presence.resolve_tab("Some Real Track", "Google.Chrome_1", report),
            ytm,
        )

    def test_disabled_service_audible_blocks_attribution(self):
        soundcloud = {
            "host": "soundcloud.com",
            "mediaId": None,
            "title": "Cloud Song by Cloud Artist",
        }
        report = self._report([self.APPLE, soundcloud], soundcloud=False)
        self.assertIsNone(
            presence.resolve_tab("Real Song", "Google.Chrome_1", report)
        )


class FallbackTimingTests(unittest.TestCase):
    FB = ("Cloud Song", "Cloud Artist", "soundcloud.com", None)
    NOW = 2_000.0

    def test_no_anchor_publishes_nothing(self):
        # Never saw this track's real position, so the only thing we could
        # show is a frozen 0:00. Publish nothing instead.
        self.assertIsNone(presence.fallback_timing(self.FB, {}, self.NOW))

    def test_recent_anchor_yields_moving_position(self):
        # Real position captured 40s ago on a 180s track: still valid.
        seen = {("Cloud Song", "Cloud Artist"): (self.NOW - 40, 180.0)}
        result = presence.fallback_timing(self.FB, seen, self.NOW)
        self.assertEqual(
            result, ("Cloud Song", "Cloud Artist", 40.0, 180.0, "tab:soundcloud.com")
        )

    def test_anchor_past_track_end_plus_grace_is_dropped(self):
        # Anchored 220s ago on a 180s track (> dur + 30 grace): stale.
        seen = {("Cloud Song", "Cloud Artist"): (self.NOW - 220, 180.0)}
        self.assertIsNone(presence.fallback_timing(self.FB, seen, self.NOW))

    def test_anchor_for_a_different_track_is_ignored(self):
        seen = {("Other Song", "Other Artist"): (self.NOW - 10, 180.0)}
        self.assertIsNone(presence.fallback_timing(self.FB, seen, self.NOW))


class AppleExtensionTimingTests(unittest.TestCase):
    NOW = 1_750_000_010.0

    def tab(self, **overrides):
        tab = {
            "host": "music.apple.com",
            "mediaId": None,
            "title": "Apple Music",
            "position": 42.0,
            "duration": 207.0,
            "playing": True,
            # Sampled 2 seconds before NOW.
            "sampledAt": 1_750_000_008_000.0,
        }
        tab.update(overrides)
        return tab

    def test_playing_sample_extrapolates_to_now(self):
        self.assertEqual(
            presence.apple_extension_timing(self.tab(), self.NOW),
            (44.0, 207.0),
        )

    def test_paused_sample_is_used_as_is(self):
        self.assertEqual(
            presence.apple_extension_timing(self.tab(playing=False), self.NOW),
            (42.0, 207.0),
        )

    def test_missing_duration_reports_zero(self):
        self.assertEqual(
            presence.apple_extension_timing(self.tab(duration=None), self.NOW),
            (44.0, 0.0),
        )

    def test_slight_clock_skew_is_tolerated_without_rewinding(self):
        # Sampled "3 seconds in the future" on the browser's clock: position
        # must not be extrapolated backwards.
        tab = self.tab(sampledAt=1_750_000_013_000.0)
        self.assertEqual(presence.apple_extension_timing(tab, self.NOW), (42.0, 207.0))

    def test_stale_sample_returns_none(self):
        tab = self.tab(sampledAt=1_750_000_008_000.0 - 120_000)
        self.assertIsNone(presence.apple_extension_timing(tab, self.NOW))

    def test_far_future_sample_returns_none(self):
        tab = self.tab(sampledAt=1_750_000_008_000.0 + 60_000)
        self.assertIsNone(presence.apple_extension_timing(tab, self.NOW))

    def test_tab_without_playback_fields_returns_none(self):
        tab = {"host": "music.apple.com", "mediaId": None, "title": "Apple Music"}
        self.assertIsNone(presence.apple_extension_timing(tab, self.NOW))

    def test_missing_tab_returns_none(self):
        self.assertIsNone(presence.apple_extension_timing(None, self.NOW))


if __name__ == "__main__":
    unittest.main()
