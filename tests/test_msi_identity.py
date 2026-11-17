import unittest
from unittest import mock

import msi_identity


class MsiIdentityTests(unittest.TestCase):
    def test_msi_identity_requires_all_exact_properties(self):
        valid = {
            "ProductName": "Chunes",
            "ProductVersion": "1.1.0",
            "Manufacturer": "Chunes",
            "UpgradeCode": msi_identity.EXPECTED_UPGRADE_CODE,
        }
        with mock.patch.object(
            msi_identity, "_read_msi_properties", return_value=valid
        ):
            self.assertEqual(
                msi_identity.verify_msi_identity("installer.msi", "1.1.0"), valid
            )

        for name in valid:
            changed = dict(valid)
            changed[name] = "unexpected"
            with self.subTest(name=name), mock.patch.object(
                msi_identity, "_read_msi_properties", return_value=changed
            ):
                with self.assertRaises(msi_identity.MsiIdentityError):
                    msi_identity.verify_msi_identity("installer.msi", "1.1.0")

    def test_expected_version_must_be_a_stable_triplet(self):
        for value in ("1.2", "1.2.3.4", "01.2.3", "1.2.3-rc.1", None):
            with self.subTest(value=value):
                with self.assertRaises(msi_identity.MsiIdentityError):
                    msi_identity.verify_msi_identity("installer.msi", value)


if __name__ == "__main__":
    unittest.main()
