import sys
import unittest
from unittest import mock

import chunes


class AutostartTests(unittest.TestCase):
    def test_checked_state_requires_the_exact_current_command(self):
        with (
            mock.patch.object(chunes, "_launch_argv", return_value=[r"C:\Apps\Chunes.exe"]),
            mock.patch.object(
                chunes, "_command_parts", return_value=[r"C:\Apps\Chunes.exe"]
            ),
        ):
            self.assertTrue(chunes._command_is_current("current"))

        with mock.patch.object(
            chunes, "_command_parts", return_value=[r"D:\Portable\Chunes.exe"]
        ):
            self.assertFalse(chunes._command_is_current("legacy"))

    def test_only_recognized_chunes_commands_are_owned(self):
        cases = [
            ([r"D:\Portable\Chunes.exe"], True),
            ([r"C:\Python\pythonw.exe", r"D:\src\chunes.py"], True),
            ([r"C:\Windows\notepad.exe"], False),
            ([r"D:\Other\Chunes.exe", "--unrelated"], False),
        ]
        for parts, expected in cases:
            with self.subTest(parts=parts), mock.patch.object(
                chunes, "_command_parts", return_value=parts
            ):
                self.assertEqual(chunes._command_is_chunes_owned("command"), expected)

    def test_migration_rewrites_owned_legacy_entry_only(self):
        with (
            mock.patch.object(sys, "frozen", True, create=True),
            mock.patch.object(chunes, "_read_autostart_command", return_value="legacy"),
            mock.patch.object(chunes, "_command_is_current", return_value=False),
            mock.patch.object(chunes, "_command_is_chunes_owned", return_value=True),
            mock.patch.object(chunes, "_set_autostart_command") as write,
            mock.patch("builtins.print"),
        ):
            self.assertTrue(chunes.migrate_legacy_autostart())
        write.assert_called_once_with()

    def test_unrelated_run_value_is_not_overwritten_or_deleted(self):
        icon = mock.Mock()
        with (
            mock.patch.object(chunes, "_read_autostart_command", return_value="other"),
            mock.patch.object(chunes, "_command_is_current", return_value=False),
            mock.patch.object(chunes, "_command_is_chunes_owned", return_value=False),
            mock.patch.object(chunes, "_set_autostart_command") as write,
            mock.patch.object(chunes, "_delete_autostart_command") as delete,
            mock.patch("builtins.print"),
        ):
            chunes.toggle_autostart(icon, None)
        write.assert_not_called()
        delete.assert_not_called()
        icon.update_menu.assert_called_once_with()

    def test_uninstall_helper_removes_owned_current_or_legacy_entry(self):
        with (
            mock.patch.object(chunes, "_read_autostart_command", return_value="value"),
            mock.patch.object(chunes, "_command_is_current", return_value=False),
            mock.patch.object(chunes, "_command_is_chunes_owned", return_value=True),
            mock.patch.object(chunes, "_delete_autostart_command") as delete,
        ):
            self.assertTrue(chunes.remove_owned_autostart())
        delete.assert_called_once_with()

        with (
            mock.patch.object(chunes, "_read_autostart_command", return_value="other"),
            mock.patch.object(chunes, "_command_is_current", return_value=False),
            mock.patch.object(chunes, "_command_is_chunes_owned", return_value=False),
            mock.patch.object(chunes, "_delete_autostart_command") as delete,
        ):
            self.assertFalse(chunes.remove_owned_autostart())
        delete.assert_not_called()


class TrayStatusTests(unittest.TestCase):
    def tearDown(self):
        chunes.presence.set_status(track=None, extension_enabled=None)

    def test_dynamic_menu_text_reflects_presence_snapshot(self):
        chunes.presence.set_status(track="Song - Artist", extension_enabled=True)
        self.assertEqual(chunes.current_track_text(), "Song - Artist")
        self.assertEqual(chunes.extension_state_text(), "Chune ID: on")

        chunes.presence.set_status(track=None, extension_enabled=False)
        self.assertEqual(chunes.current_track_text(), "Nothing playing")
        self.assertEqual(chunes.extension_state_text(), "Chune ID: off")

        chunes.presence.set_status(extension_enabled=None)
        self.assertEqual(chunes.extension_state_text(), "Chune ID: not connected")

    def test_status_change_refreshes_the_native_menu(self):
        old = {"track": None, "extension_enabled": None}
        new = {"track": "Song", "extension_enabled": True}
        stop = mock.Mock()
        stop.wait.side_effect = [False, True]
        icon = mock.Mock()
        with mock.patch.object(
            chunes.presence, "status_snapshot", side_effect=[old, new]
        ):
            chunes.refresh_dynamic_menu(icon, stop, interval=0)
        icon.update_menu.assert_called_once_with()


class SingleInstanceTests(unittest.TestCase):
    def tearDown(self):
        chunes._instance_mutex = None

    def test_existing_mutex_rejects_a_second_instance(self):
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = 123
        with (
            mock.patch.object(chunes.ctypes, "WinDLL", return_value=kernel32),
            mock.patch.object(chunes.ctypes, "set_last_error"),
            mock.patch.object(chunes.ctypes, "get_last_error", return_value=183),
        ):
            self.assertFalse(chunes.acquire_single_instance())
        kernel32.CloseHandle.assert_called_once_with(123)

    def test_new_mutex_is_kept_for_the_process_lifetime(self):
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = 456
        with (
            mock.patch.object(chunes.ctypes, "WinDLL", return_value=kernel32),
            mock.patch.object(chunes.ctypes, "set_last_error"),
            mock.patch.object(chunes.ctypes, "get_last_error", return_value=0),
        ):
            self.assertTrue(chunes.acquire_single_instance())
        self.assertEqual(chunes._instance_mutex, 456)
        kernel32.CloseHandle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
