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
        self.assertEqual(chunes.version_text(), "Chunes v1.0.12")

        chunes.presence.set_status(track=None, extension_enabled=False)
        self.assertEqual(chunes.current_track_text(), "Nothing playing")
        self.assertEqual(chunes.extension_state_text(), "Chune ID: off")

        chunes.presence.set_status(extension_enabled=None)
        self.assertEqual(chunes.extension_state_text(), "Chune ID: not connected")
        self.assertEqual(chunes.version_text(), "Chunes v1.0.12")

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


class GracefulCloseTests(unittest.TestCase):
    def tearDown(self):
        chunes._tray_stop.clear()

    def test_registration_requires_message_handlers_dict(self):
        icon = mock.Mock(spec=[])
        chunes._register_close_messages(icon)
        self.assertFalse(hasattr(icon, "_message_handlers"))

    @mock.patch.object(chunes.ctypes.windll.user32, "DestroyWindow")
    def test_registered_handlers_satisfy_pystray_contract(self, destroy_window):
        icon = mock.Mock()
        icon._message_handlers = {}
        chunes._register_close_messages(icon)

        # WM_CLOSE
        self.assertEqual(icon._message_handlers[0x0010](123, None, None, None), 0)
        self.assertTrue(chunes._tray_stop.is_set())
        icon.stop.assert_called_once_with()
        destroy_window.assert_called_once_with(123)

        chunes._tray_stop.clear()
        icon.stop.reset_mock()
        destroy_window.reset_mock()

        # WM_QUERYENDSESSION
        self.assertEqual(icon._message_handlers[0x0011](123, None, None, None), 1)
        self.assertFalse(chunes._tray_stop.is_set())
        icon.stop.assert_not_called()
        destroy_window.assert_not_called()

        # WM_ENDSESSION (wparam=False)
        self.assertEqual(icon._message_handlers[0x0016](123, None, 0, None), 0)
        self.assertFalse(chunes._tray_stop.is_set())
        icon.stop.assert_not_called()
        destroy_window.assert_not_called()

        # WM_ENDSESSION (wparam=True)
        self.assertEqual(icon._message_handlers[0x0016](123, None, 1, None), 0)
        self.assertTrue(chunes._tray_stop.is_set())
        icon.stop.assert_called_once_with()
        destroy_window.assert_called_once_with(123)


class PackagedTrayTests(unittest.TestCase):
    def packaged(self, enabled=True):
        return mock.patch.object(chunes.packaged, "is_packaged", return_value=enabled)

    def test_the_run_key_is_never_touched_by_the_packaged_build(self):
        with (
            self.packaged(),
            mock.patch.object(chunes, "_read_autostart_command") as read,
            mock.patch.object(chunes, "_set_autostart_command") as write,
            mock.patch.object(chunes, "_delete_autostart_command") as delete,
            mock.patch.object(chunes.startup_task, "is_enabled", return_value=True),
            mock.patch.object(chunes.startup_task, "disable") as disable,
        ):
            self.assertTrue(chunes.autostart_enabled())
            self.assertFalse(chunes.migrate_legacy_autostart())
            self.assertFalse(chunes.remove_owned_autostart())
            chunes.toggle_autostart(mock.Mock(), None)
        disable.assert_called_once_with()
        read.assert_not_called()
        write.assert_not_called()
        delete.assert_not_called()

    def test_a_refused_startup_request_sends_the_user_to_windows_settings(self):
        cases = (
            (chunes.startup_task.DISABLED_BY_USER, True),
            (chunes.startup_task.ENABLED, False),
        )
        for result, expect_settings in cases:
            with self.subTest(result=result):
                with (
                    self.packaged(),
                    mock.patch.object(
                        chunes.startup_task, "is_enabled", return_value=False
                    ),
                    mock.patch.object(
                        chunes.startup_task, "enable", return_value=result
                    ),
                    mock.patch.object(chunes, "_open_startup_settings") as settings,
                    mock.patch("builtins.print"),
                ):
                    chunes.toggle_autostart(mock.Mock(), None)
                self.assertEqual(settings.called, expect_settings)

    def test_an_unavailable_startup_task_opens_windows_settings(self):
        with (
            self.packaged(),
            mock.patch.object(chunes.startup_task, "is_enabled", return_value=False),
            mock.patch.object(
                chunes.startup_task,
                "enable",
                side_effect=chunes.startup_task.StartupTaskUnavailable("no package"),
            ),
            mock.patch.object(chunes, "_open_startup_settings") as settings,
            mock.patch("builtins.print"),
        ):
            chunes.toggle_autostart(mock.Mock(), None)
        settings.assert_called_once_with()

    def test_the_packaged_menu_drops_the_github_update_items(self):
        def labels(store_updates):
            return [
                item.text
                for item in chunes.build_menu_items(store_updates=store_updates)
                if isinstance(item.text, str)
            ]

        unpackaged = labels(False)
        store = labels(True)
        self.assertIn("Check for updates now", unpackaged)
        self.assertIn("Automatically check for updates", unpackaged)
        self.assertNotIn("Check for updates now", store)
        self.assertNotIn("Automatically check for updates", store)
        # Everything the Store build still owns has to survive.
        for kept in ("Start with Windows", "Look up online album art", "Open log", "Quit"):
            with self.subTest(kept=kept):
                self.assertIn(kept, store)


if __name__ == "__main__":
    unittest.main()
