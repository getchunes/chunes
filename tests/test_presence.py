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
                    "title": "Video Song - Video Artist - YouTube Music",
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
                "Video Artist",
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

    def test_youtube_music_never_falls_back_to_video_or_soundcloud_art(self):
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
            self.assertIsNone(
                presence.find_artwork(
                    "Track", "Artist", "music.youtube.com", self.VIDEO_ID
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
