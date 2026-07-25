import threading
import time
import unittest
from unittest import mock

import packaged
import startup_task


class FakeState:
    def __init__(self, name):
        self.name = name


class PackageIdentityTests(unittest.TestCase):
    def setUp(self):
        packaged._family_name = False
        self.addCleanup(setattr, packaged, "_family_name", False)

    def test_the_test_runner_itself_is_not_packaged(self):
        self.assertIsNone(packaged.package_family_name())
        self.assertFalse(packaged.is_packaged())

    def test_the_family_name_is_queried_once_and_cached(self):
        with mock.patch.object(
            packaged, "_query_package_family_name", return_value="a_b"
        ) as query:
            self.assertEqual(packaged.package_family_name(), "a_b")
            self.assertEqual(packaged.package_family_name(), "a_b")
        query.assert_called_once_with()

    def test_any_hosting_package_switches_the_build_to_store_behavior(self):
        # Autostart and updates both break the same way inside any package,
        # not only inside the identity Chunes publishes.
        for family_name in (
            f"{packaged.PACKAGE_IDENTITY_NAME}_8wekyb3d8bbwe",
            "someoneelse.Chunes_8wekyb3d8bbwe",
        ):
            with self.subTest(family_name=family_name):
                packaged._family_name = family_name
                self.assertTrue(packaged.is_packaged())


class StartupTaskTests(unittest.TestCase):
    def setUp(self):
        startup_task._cached = None
        self.addCleanup(setattr, startup_task, "_cached", None)

    def test_projection_enumerators_map_to_winrt_state_names(self):
        cases = {
            "DISABLED": startup_task.DISABLED,
            "DISABLED_BY_USER": startup_task.DISABLED_BY_USER,
            "ENABLED": startup_task.ENABLED,
            "DISABLED_BY_POLICY": startup_task.DISABLED_BY_POLICY,
            "ENABLED_BY_POLICY": startup_task.ENABLED_BY_POLICY,
        }
        for enumerator, expected in cases.items():
            with self.subTest(enumerator=enumerator):
                self.assertEqual(
                    startup_task._state_name(FakeState(enumerator)), expected
                )
        self.assertIsNone(startup_task._state_name(FakeState("")))
        self.assertIsNone(startup_task._state_name(object()))

    def test_an_unreachable_task_reads_as_disabled_rather_than_raising(self):
        with mock.patch.object(
            startup_task,
            "_task",
            side_effect=startup_task.StartupTaskUnavailable("no package"),
        ):
            self.assertFalse(startup_task.is_enabled())
            with self.assertRaises(startup_task.StartupTaskUnavailable):
                startup_task.state()

    def test_projection_failures_never_escape_as_their_own_type(self):
        # pystray's tray thread is an STA and the projection raises a bare
        # RuntimeError there, which used to break menu rendering.
        apartment_error = RuntimeError(
            "Cannot call blocking method from single-threaded apartment."
        )
        with mock.patch.object(startup_task, "_task", side_effect=apartment_error):
            with self.assertRaises(startup_task.StartupTaskUnavailable):
                startup_task.state()
            self.assertFalse(startup_task.is_enabled())

    def test_winrt_calls_never_run_on_the_calling_thread(self):
        threads = []

        def record():
            threads.append(threading.current_thread())
            return FakeState("ENABLED")

        task = mock.Mock()
        type(task).state = property(lambda self: record())
        with mock.patch.object(startup_task, "_task", return_value=task):
            self.assertEqual(startup_task.state(), startup_task.ENABLED)
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.current_thread())

    def test_a_hung_projection_call_gives_up_instead_of_freezing_the_tray(self):
        release = threading.Event()
        self.addCleanup(release.set)
        with (
            mock.patch.object(startup_task, "CALL_TIMEOUT_SECONDS", 0.1),
            mock.patch.object(
                startup_task, "_task", side_effect=lambda: release.wait(30)
            ),
        ):
            with self.assertRaises(startup_task.StartupTaskUnavailable):
                startup_task.state()

    def test_the_menu_checkbox_reuses_a_fresh_answer(self):
        task = mock.Mock()
        task.state = FakeState("ENABLED")
        with mock.patch.object(startup_task, "_task", return_value=task) as lookup:
            self.assertTrue(startup_task.is_enabled())
            self.assertTrue(startup_task.is_enabled())
            lookup.assert_called_once_with()
            startup_task._cached = (
                time.monotonic() - startup_task.CACHE_SECONDS - 1,
                startup_task.ENABLED,
            )
            self.assertTrue(startup_task.is_enabled())
            self.assertEqual(lookup.call_count, 2)

    def test_enable_reports_the_state_windows_actually_settled_on(self):
        task = mock.Mock()
        task.request_enable_async.return_value.get.return_value = FakeState(
            "DISABLED_BY_USER"
        )
        with mock.patch.object(startup_task, "_task", return_value=task):
            self.assertEqual(startup_task.enable(), startup_task.DISABLED_BY_USER)
        self.assertNotIn(startup_task.DISABLED_BY_USER, startup_task.ENABLED_STATES)

    def test_disable_leaves_user_and_policy_decisions_alone(self):
        enumerators = {
            startup_task.DISABLED_BY_USER: "DISABLED_BY_USER",
            startup_task.DISABLED_BY_POLICY: "DISABLED_BY_POLICY",
            startup_task.ENABLED_BY_POLICY: "ENABLED_BY_POLICY",
        }
        self.assertEqual(set(enumerators), set(startup_task.LOCKED_STATES))
        for locked, enumerator in enumerators.items():
            with self.subTest(locked=locked):
                task = mock.Mock()
                task.state = FakeState(enumerator)
                with mock.patch.object(startup_task, "_task", return_value=task):
                    self.assertEqual(startup_task.disable(), locked)
                task.disable.assert_not_called()

    def test_disable_turns_off_a_task_the_app_owns(self):
        task = mock.Mock()
        task.state = FakeState("ENABLED")

        def turn_off():
            task.state = FakeState("DISABLED")

        task.disable.side_effect = turn_off
        with mock.patch.object(startup_task, "_task", return_value=task):
            self.assertEqual(startup_task.disable(), startup_task.DISABLED)
        task.disable.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
