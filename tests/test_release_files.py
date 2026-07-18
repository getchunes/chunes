import hashlib
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET

from version import __version__


ROOT = Path(__file__).resolve().parents[1]
WIX_NS = {"w": "http://schemas.microsoft.com/wix/2006/wi"}


class CanonicalAssetTests(unittest.TestCase):
    def test_assets_match_canonical_release_hashes(self):
        expected = {
            "assets/logo.svg": (
                "a64b28c856723e8f7619a26244a736c64e16f8af558329bd25379fe198bfd0f5"
            ),
            "assets/logo-512.png": (
                "d93fb81ef2b43438d63d83536910923a1be4c7529c55de3591200bac62de679b"
            ),
            "assets/chunes-tray-64.png": (
                "133e33cecd70d07dbda8ea1e5608bfd902b517546eda79773f3d867abe59f8fc"
            ),
            "assets/chunes-tray.ico": (
                "9118d9a90d162898a875e13ae71d03bc1504b8306f606f796bd804b81cf3a21d"
            ),
        }
        for relative, digest in expected.items():
            with self.subTest(relative=relative):
                contents = (ROOT / relative).read_bytes()
                if relative.endswith(".svg"):
                    contents = contents.replace(b"\r\n", b"\n")
                actual = hashlib.sha256(contents).hexdigest()
                self.assertEqual(actual, digest)


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ET.parse(ROOT / "installer" / "Chunes.wxs")
        cls.product = cls.tree.getroot().find("w:Product", WIX_NS)

    def test_all_release_metadata_is_version_1_0_0(self):
        self.assertEqual(__version__, "1.0.0")
        version_info = (ROOT / "installer" / "version_info.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("ProductVersion', '1.0.0.0'", version_info)
        self.assertIn("FileVersion', '1.0.0.0'", version_info)

    def test_installer_is_per_user_x64_with_stable_upgrade_code(self):
        self.assertEqual(
            self.product.attrib["UpgradeCode"],
            "{2DDF67BD-FBDE-4BDF-A090-F1552C2C1330}",
        )
        package = self.product.find("w:Package", WIX_NS)
        self.assertEqual(package.attrib["InstallScope"], "perUser")
        self.assertEqual(package.attrib["InstallPrivileges"], "limited")
        self.assertEqual(package.attrib["Platform"], "x64")
        self.assertIsNotNone(self.product.find("w:MajorUpgrade", WIX_NS))

    def test_installer_wires_shortcut_icon_and_uninstall_cleanup(self):
        self.assertIsNotNone(self.product.find(".//w:Shortcut", WIX_NS))
        icon = self.product.find("w:Icon", WIX_NS)
        self.assertEqual(icon.attrib["Id"], "ChunesIcon")
        remove_folders = self.product.findall(".//w:RemoveFolder", WIX_NS)
        self.assertGreaterEqual(len(remove_folders), 2)
        self.assertTrue(all(item.attrib["On"] == "uninstall" for item in remove_folders))

    def test_first_install_privacy_checkboxes_and_opt_outs_are_authored(self):
        settings = (
            (
                "AutoUpdate",
                "AUTO_UPDATE",
                "EXISTING_AUTO_UPDATE",
                "AutomaticallyCheckForUpdates",
                "PreserveAutoUpdateOptOut",
            ),
            (
                "Artwork",
                "ARTWORK",
                "EXISTING_ARTWORK",
                "LookUpOnlineCoverArt",
                "PreserveArtworkOptOut",
            ),
        )
        for control_id, property_id, search_id, value_name, action_id in settings:
            with self.subTest(property_id=property_id):
                checkbox = self.product.find(
                    f".//w:Control[@Id='{control_id}']", WIX_NS
                )
                self.assertEqual(checkbox.attrib["Property"], property_id)
                self.assertEqual(checkbox.attrib["CheckBoxValue"], "1")

                setting_property = self.product.find(
                    f"w:Property[@Id='{property_id}']", WIX_NS
                )
                self.assertEqual(setting_property.attrib["Value"], "1")
                self.assertEqual(setting_property.attrib["Secure"], "yes")

                search = self.product.find(
                    f"w:Property[@Id='{search_id}']/w:RegistrySearch", WIX_NS
                )
                self.assertEqual(search.attrib["Name"], value_name)
                self.assertEqual(search.attrib["Win64"], "yes")

                custom = self.product.find(
                    f"w:CustomAction[@Id='{action_id}']", WIX_NS
                )
                self.assertEqual(custom.attrib["Value"], "0")

    def test_maintenance_restores_install_path_and_autostart_helpers_are_safe(self):
        search = self.product.find(
            "w:Property[@Id='EXISTING_INSTALL_PATH']/w:RegistrySearch", WIX_NS
        )
        self.assertEqual(search.attrib["Name"], "InstallPath")
        self.assertEqual(search.attrib["Win64"], "yes")
        restore = self.product.find(
            "w:CustomAction[@Id='RestoreInstallFolder']", WIX_NS
        )
        self.assertEqual(restore.attrib["Property"], "INSTALLFOLDER")
        self.assertEqual(restore.attrib["Value"], "[EXISTING_INSTALL_PATH]")

        migrate = self.product.find(
            "w:CustomAction[@Id='MigrateOwnedAutostart']", WIX_NS
        )
        remove = self.product.find(
            "w:CustomAction[@Id='RemoveOwnedAutostart']", WIX_NS
        )
        self.assertEqual(migrate.attrib["FileKey"], "ChunesExe")
        self.assertEqual(migrate.attrib["ExeCommand"], "--migrate-autostart")
        self.assertEqual(remove.attrib["FileKey"], "ChunesExe")
        self.assertEqual(
            remove.attrib["ExeCommand"], "--remove-autostart-if-owned"
        )
        execute_actions = {
            item.attrib["Action"]: (item.text or "")
            for item in self.product.findall(
                "w:InstallExecuteSequence/w:Custom", WIX_NS
            )
        }
        ui_actions = {
            item.attrib["Action"]: (item.text or "")
            for item in self.product.findall("w:InstallUISequence/w:Custom", WIX_NS)
        }
        self.assertIn(
            "Installed OR WIX_UPGRADE_DETECTED", ui_actions["RestoreInstallFolder"]
        )
        self.assertIn(
            "Installed OR WIX_UPGRADE_DETECTED",
            execute_actions["RestoreInstallFolder"],
        )
        self.assertIn("EXISTING_INSTALL_PATH", execute_actions["RestoreInstallFolder"])
        self.assertIn("NOT REMOVE", execute_actions["MigrateOwnedAutostart"])
        self.assertIn("NOT UPGRADINGPRODUCTCODE", execute_actions["RemoveOwnedAutostart"])

    def test_requirements_are_pinned(self):
        packages = {}
        for filename in ("requirements.txt", "requirements-build.txt"):
            for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-r "):
                    continue
                with self.subTest(filename=filename, requirement=line):
                    self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^=]+$")
                    name, version = line.split("==", 1)
                    packages[name.lower()] = version

        lock_packages = {}
        lock = ROOT / "requirements-windows-py313.lock"
        for line in lock.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            with self.subTest(lock_requirement=line):
                self.assertRegex(
                    line,
                    r"^[A-Za-z0-9_.-]+==\S+ --hash=sha256:[0-9a-f]{64}$",
                )
                requirement = line.split(" --hash=", 1)[0]
                name, version = requirement.split("==", 1)
                lock_packages[name.lower()] = version
        self.assertEqual(lock_packages, packages)

    def test_wix_download_is_hash_locked(self):
        script = (ROOT / "scripts" / "get-wix.ps1").read_text(encoding="utf-8")
        self.assertIn("wix3141rtm/wix314-binaries.zip", script)
        self.assertIn(
            "6AC824E1642D6F7277D0ED7EA09411A508F6116BA6FAE0AA5F2C7DAA2FF43D31",
            script,
        )
        self.assertIn("Get-FileHash -Algorithm SHA256", script)

    def test_explicit_wix_directory_does_not_fall_back_to_path(self):
        script = (ROOT / "scripts" / "build.ps1").read_text(encoding="utf-8")
        self.assertIn(
            '$wixBinProvided = $PSBoundParameters.ContainsKey("WixBin")', script
        )
        self.assertRegex(
            script,
            re.compile(
                r"if \(-not \$wixBinProvided\) \{\s+"
                r"\$candle = \(Get-Command candle\.exe",
                re.MULTILINE,
            ),
        )

    def test_release_workflow_is_manual_and_publishes_after_signing(self):
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotRegex(workflow, re.compile(r"^\s+push:\s*$", re.MULTILINE))
        signing = workflow.index(
            "signpath/github-action-submit-signing-request@"
            "b9d91eadd323de506c0c81cf0c7fe7438f3360fd"
        )
        tag_check = workflow.index("Require unused release tag")
        verify = workflow.index("Verify signed release artifact")
        publish = workflow.index("gh release create")
        self.assertLess(tag_check, signing)
        self.assertLess(signing, verify)
        self.assertLess(verify, publish)
        self.assertIn("SignPath Foundation", workflow)
        self.assertIn("verify_msi_identity", workflow)
        self.assertIn("confirm_v1_recreation:", workflow)
        self.assertIn("environment: code-signing", workflow)
        self.assertIn("environment: stable-release", workflow)
        self.assertIn("permissions: {}", workflow)
        self.assertGreaterEqual(workflow.count("persist-credentials: false"), 3)
        actions = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(actions)
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")

    def test_all_workflow_actions_are_full_sha_pinned(self):
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            actions = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
            for action in actions:
                with self.subTest(workflow=path.name, action=action):
                    self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
            checkout_count = sum(
                action.startswith("actions/checkout@") for action in actions
            )
            self.assertGreaterEqual(
                workflow.count("persist-credentials: false"), checkout_count
            )

    def test_signpath_configuration_deep_signs_the_executable_and_msi(self):
        config = (
            ROOT / ".signpath" / "artifact-configuration.xml"
        ).read_text(encoding="utf-8")
        self.assertIn('name="version" required="true"', config)
        self.assertIn('path="Chunes-${version}-x64.msi"', config)
        self.assertIn('path="Chunes.exe"', config)
        self.assertIn('product-name="Chunes"', config)
        self.assertIn('product-version="${version}.0"', config)
        self.assertEqual(config.count("<authenticode-sign/>"), 2)


if __name__ == "__main__":
    unittest.main()
