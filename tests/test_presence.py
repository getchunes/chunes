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
    def test_success_responses_advertise_protocol_v2(self):
        for status in (200, 204):
            with self.subTest(status=status):
                reply = presence._http_reply(status, b"{}" if status == 200 else b"")
                self.assertIn(b"X-Chunes-Protocol: 2\r\n", reply)
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
    VIDEO_ID = "a1B2c3D4e5F"
    ALBUM_ART = (
        "https://yt3.googleusercontent.com/album-art=w544-h544-l90-rj"
    )

    def setUp(self):
        presence._artwork_cache.clear()
        presence._ytm_client = None

    @staticmethod
    def youtube_music_response(video_id, thumbnails):
        return {
            "contents": {
                "singleColumnMusicWatchNextResultsRenderer": {
                    "tabbedRenderer": {
                        "watchNextTabbedResultsRenderer": {
                            "tabs": [
                                {
                                    "tabRenderer": {
                                        "content": {
                                            "musicQueueRenderer": {
                                                "content": {
                                                    "playlistPanelRenderer": {
                                                        "contents": [
                                                            {
                                                                "playlistPanelVideoRenderer": {
                                                                    "videoId": video_id,
                                                                    "thumbnail": {
                                                                        "thumbnails": thumbnails
                                                                    },
                                                                }
                                                            }
                                                        ]
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }

    def test_youtube_music_client_values_come_from_youtube_music(self):
        page = (
            '<script>ytcfg.set({"INNERTUBE_API_KEY":"public-key",'
            '"INNERTUBE_CLIENT_VERSION":"1.20260718.01.00",'
            '"VISITOR_DATA":"visitor"});</script>'
        )
        with mock.patch.object(presence, "_http_get", return_value=page) as get:
            self.assertEqual(
                presence._youtube_music_client(),
                {
                    "INNERTUBE_API_KEY": "public-key",
                    "INNERTUBE_CLIENT_VERSION": "1.20260718.01.00",
                    "VISITOR_DATA": "visitor",
                },
            )
        get.assert_called_once_with(
            "https://music.youtube.com/", {"Cookie": "SOCS=CAI"}
        )

    def test_youtube_music_uses_exact_square_music_artwork(self):
        response = self.youtube_music_response(
            self.VIDEO_ID,
            [
                {
                    "url": "https://i.ytimg.com/vi/a1B2c3D4e5F/maxresdefault.jpg",
                    "width": 1280,
                    "height": 720,
                },
                {"url": self.ALBUM_ART, "width": 544, "height": 544},
            ],
        )
        client = {
            "INNERTUBE_API_KEY": "public-key",
            "INNERTUBE_CLIENT_VERSION": "1.20260718.01.00",
            "VISITOR_DATA": "visitor",
        }
        with (
            mock.patch.object(presence, "_youtube_music_client", return_value=client),
            mock.patch.object(
                presence, "_http_post_json", return_value=response
            ) as post,
        ):
            self.assertEqual(
                presence._find_youtube_music_artwork(self.VIDEO_ID),
                self.ALBUM_ART,
            )

        url, body, headers = post.call_args.args
        self.assertIn("music.youtube.com/youtubei/v1/next", url)
        self.assertEqual(body["videoId"], self.VIDEO_ID)
        self.assertEqual(body["playlistId"], f"RDAMVM{self.VIDEO_ID}")
        self.assertEqual(headers["X-Goog-Visitor-Id"], "visitor")

    def test_youtube_music_falls_back_to_video_thumbnail_without_soundcloud(self):
        response = self.youtube_music_response(
            self.VIDEO_ID,
            [
                {
                    "url": "https://i.ytimg.com/vi/a1B2c3D4e5F/hqdefault.jpg",
                    "width": 480,
                    "height": 360,
                }
            ],
        )
        client = {
            "INNERTUBE_API_KEY": "public-key",
            "INNERTUBE_CLIENT_VERSION": "1.20260718.01.00",
        }
        with (
            mock.patch.object(presence, "_youtube_music_client", return_value=client),
            mock.patch.object(presence, "_http_post_json", return_value=response),
            mock.patch.object(presence, "_find_soundcloud_artwork") as soundcloud,
        ):
            self.assertEqual(
                presence.find_artwork(
                    "Track", "Artist", "music.youtube.com", self.VIDEO_ID
                ),
                "https://i.ytimg.com/vi/a1B2c3D4e5F/hqdefault.jpg"
            )
        soundcloud.assert_not_called()

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
        with (
            mock.patch.object(presence, "_http_get", return_value=response) as get,
            mock.patch.object(presence, "_find_soundcloud_artwork") as soundcloud,
        ):
            art = presence.find_artwork(
                "Real Song", "Some Artist", "music.apple.com", None, "brave"
            )

        soundcloud.assert_not_called()
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
        with (
            mock.patch.object(presence, "_http_get", side_effect=OSError("down")),
            mock.patch.object(presence, "_find_soundcloud_artwork") as soundcloud,
        ):
            self.assertIsNone(
                presence.find_artwork(
                    "Real Song", "Some Artist", "music.apple.com", None, "brave"
                )
            )
        soundcloud.assert_not_called()

    def test_unidentified_browser_track_is_not_sent_to_soundcloud(self):
        with mock.patch.object(presence, "_find_soundcloud_artwork") as soundcloud:
            self.assertIsNone(
                presence.find_artwork(
                    "Video", "Channel", source="Google.Chrome_123"
                )
            )
        soundcloud.assert_not_called()


if __name__ == "__main__":
    unittest.main()
