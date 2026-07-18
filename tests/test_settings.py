import unittest
from unittest import mock

import settings


class SettingsTests(unittest.TestCase):
    def test_reads_only_boolean_dword_values(self):
        for stored, expected in ((1, True), (0, False), (2, True)):
            key = mock.MagicMock()
            with (
                mock.patch.object(settings.winreg, "OpenKey", return_value=key),
                mock.patch.object(
                    settings.winreg,
                    "QueryValueEx",
                    return_value=(stored, settings.winreg.REG_DWORD),
                ),
            ):
                self.assertEqual(
                    settings.get_bool("Setting", default=True), expected
                )

    def test_missing_or_wrong_type_uses_default(self):
        with mock.patch.object(
            settings.winreg, "OpenKey", side_effect=OSError("missing")
        ):
            self.assertFalse(settings.get_bool("Setting", default=False))

        key = mock.MagicMock()
        with (
            mock.patch.object(settings.winreg, "OpenKey", return_value=key),
            mock.patch.object(
                settings.winreg,
                "QueryValueEx",
                return_value=("1", settings.winreg.REG_SZ),
            ),
        ):
            self.assertFalse(settings.get_bool("Setting", default=False))

    def test_writes_boolean_dword_values(self):
        key = mock.MagicMock()
        with (
            mock.patch.object(settings.winreg, "CreateKeyEx", return_value=key),
            mock.patch.object(settings.winreg, "SetValueEx") as set_value,
        ):
            settings.set_bool("Setting", True)
            settings.set_bool("Setting", False)

        self.assertEqual(set_value.call_args_list[0].args[-1], 1)
        self.assertEqual(set_value.call_args_list[1].args[-1], 0)

    def test_public_settings_default_on(self):
        with mock.patch.object(
            settings.winreg, "OpenKey", side_effect=OSError("missing")
        ):
            self.assertTrue(settings.automatic_updates_enabled())
            self.assertTrue(settings.artwork_enabled())


if __name__ == "__main__":
    unittest.main()
