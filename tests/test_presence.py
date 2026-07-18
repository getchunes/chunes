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
    def test_success_responses_advertise_protocol_v1(self):
        for status in (200, 204):
            with self.subTest(status=status):
                reply = presence._http_reply(status, b"{}" if status == 200 else b"")
                self.assertIn(b"X-Chunes-Protocol: 1\r\n", reply)
        self.assertNotIn(b"X-Chunes-Protocol", presence._http_reply(400))

    def test_classification_fallback_and_labels_cover_both_services(self):
        report = {
            "enabled": True,
            "services": {"soundcloud": True, "youtubeMusic": True},
            "tabs": [
                {"host": "soundcloud.com", "title": "Cloud Song by Cloud Artist"},
                {
                    "host": "music.youtube.com",
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
            ("Cloud Song", "Cloud Artist", "soundcloud.com"),
        )
        youtube_report = dict(report, tabs=[report["tabs"][1]])
        self.assertEqual(
            presence.fallback_track(youtube_report),
            ("Video Song", "Video Artist", "music.youtube.com"),
        )
        self.assertEqual(
            presence.protocol.service_label_for_host("soundcloud.com"),
            "SoundCloud",
        )
        self.assertEqual(
            presence.protocol.service_label_for_host("music.youtube.com"),
            "YouTube Music",
        )


if __name__ == "__main__":
    unittest.main()
