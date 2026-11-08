import os
import plistlib
import tempfile
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import security

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class FakeConfig:
    def widget(self, wid):
        return {}


class FakeApp:
    def __init__(self, tmp):
        self.config = FakeConfig()
        self.state = core.State(os.path.join(tmp, "state.json"))
        self.poked = []

    def poke(self, wid):
        self.poked.append(wid)


class Parsers(unittest.TestCase):
    def test_firewall(self):
        self.assertEqual(security.parse_firewall(load("security_fw_global.txt"), load("security_fw_stealth.txt")),
                         {"on": True, "stealth": False})
        self.assertEqual(security.parse_firewall(load("security_fw_global_off.txt"), load("security_fw_stealth_on.txt")),
                         {"on": False, "stealth": True})
        self.assertEqual(security.parse_firewall("Firewall is blocking all non-essential incoming connections. (State = 2)"),
                         {"on": True, "stealth": None})
        self.assertEqual(security.parse_firewall(""), {"on": None, "stealth": None})

    def test_protections(self):
        self.assertTrue(security.parse_filevault(load("security_fdesetup.txt")))
        self.assertFalse(security.parse_filevault(load("security_fdesetup_off.txt")))
        self.assertIsNone(security.parse_filevault(""))
        self.assertTrue(security.parse_gatekeeper(load("security_spctl.txt")))
        self.assertFalse(security.parse_gatekeeper(load("security_spctl_off.txt")))
        self.assertTrue(security.parse_sip(load("security_csrutil.txt")))
        self.assertFalse(security.parse_sip(load("security_csrutil_off.txt")))

    def test_softwareupdate(self):
        self.assertEqual(security.parse_softwareupdate(load("security_softwareupdate_none.txt")), {"waiting": [], "restart": False})
        some = security.parse_softwareupdate(load("security_softwareupdate_some.txt"))
        self.assertEqual(some["waiting"], ["macOS Example 27.3", "Command Line Tools for Xcode 27.1", "Safari 27.2"])
        self.assertTrue(some["restart"])
        self.assertIsNone(security.parse_softwareupdate(""))  # timed out or failed: keep the last answer

    def test_auto_updates(self):
        a = security.auto_updates({"AutomaticDownload": 1, "AutomaticallyInstallMacOSUpdates": 1, "CriticalUpdateInstall": 1,
                                   "ConfigDataInstall": 1}, {"AutoUpdate": 0})
        self.assertEqual(a, {"check": True, "download": True, "install_macos": True, "security": True, "app_store": False})
        self.assertEqual(security.auto_updates_text(a)[1], "ok")
        self.assertEqual(security.auto_updates_text(security.auto_updates({"AutomaticCheckEnabled": 0}))[1], "warn")
        self.assertEqual(security.auto_updates_text(security.auto_updates({"CriticalUpdateInstall": 0}))[1], "warn")
        self.assertIn("wait for you", security.auto_updates_text(security.auto_updates({}))[0])
        self.assertIsNone(security.auto_updates(None))

    def test_recommended_updates(self):
        prefs = {"RecommendedUpdates": [{"Display Name": "macOS Example 27.3", "Display Version": "27.3", "Identifier": "MSU"},
                                        {"Identifier": "Safari"}]}
        self.assertEqual(security.recommended_updates(prefs), ["macOS Example 27.3", "Safari"])
        self.assertEqual(security.recommended_updates({}), [])


class LoginItems(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "LaunchAgents")
        os.makedirs(self.dir)
        self.write("com.example.helper", {"Label": "com.example.helper", "ProgramArguments": ["/usr/bin/true", "--quiet"]})
        self.write("com.example.gone", {"Label": "com.example.gone", "Program": "/nonexistent/example/tool"})
        with open(os.path.join(self.dir, "broken.plist"), "w") as f:
            f.write("not a plist")
        self.dirs = [(self.dir, "Starts when you log in")]

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data, binary=False):
        with open(os.path.join(self.dir, name + ".plist"), "wb") as f:
            plistlib.dump(data, f, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML)

    def test_scan(self):
        items = {i["label"]: i for i in security.scan_launch_items(self.dirs)}
        self.assertEqual(set(items), {"com.example.helper", "com.example.gone", "broken"})
        self.assertEqual(items["com.example.helper"]["program"], "/usr/bin/true")
        self.assertEqual(items["com.example.helper"]["command"], "/usr/bin/true --quiet")
        self.assertFalse(items["com.example.helper"]["missing"])
        self.assertTrue(items["com.example.gone"]["missing"])
        self.assertEqual(items["broken"]["program"], "")

    def test_baseline_then_new_then_accept(self):
        app = FakeApp(self.tmp.name)
        w = security.Security(app)
        w.maybe_check_updates = lambda: None
        fake = {"fdesetup status": load("security_fdesetup.txt"), "spctl --status": load("security_spctl.txt"),
                "csrutil status": load("security_csrutil.txt")}
        run = lambda cmd, **k: fake.get(" ".join(cmd), load("security_fw_global.txt") if "--getglobalstate" in cmd else
                                        load("security_fw_stealth.txt") if "--getstealthmode" in cmd else "")
        real_read = security.read_plist

        def read(path):
            if path == security.UPDATE_PREFS:
                return {"RecommendedUpdates": [{"Display Name": "Safari", "Display Version": "27.2"}]}
            if path == security.APPSTORE_PREFS:
                return {}
            return real_read(path)

        with mock.patch.object(security, "LAUNCH_DIRS", self.dirs), mock.patch.object(core, "run", run), \
                mock.patch.object(security, "read_plist", read):
            d = w.collect()
            self.assertEqual(d["alerts"], [])
            self.assertEqual(d["new_login_items"], 0)
            self.assertEqual(d["summary"], "1 update")
            self.assertEqual(d["updates"], {"waiting": ["Safari 27.2"], "restart": False, "checked": None, "pending": True})
            self.assertEqual((d["firewall"], d["filevault"], d["gatekeeper"], d["sip"]), ({"on": True, "stealth": False}, True, True, True))

            self.write("com.example.sneaky", {"Label": "com.example.sneaky", "ProgramArguments": ["/tmp/x"]}, binary=True)
            d = w.collect()
            self.assertEqual([a["key"] for a in d["alerts"]], ["new-login-item:com.example.sneaky"])
            self.assertEqual(d["alerts"][0]["text"], "com.example.sneaky was added to start automatically")
            self.assertEqual(d["state"], "warn")
            path = os.path.join(self.dir, "com.example.sneaky.plist")
            self.assertTrue(next(i for i in d["login_items"] if i["path"] == path)["new"])

            self.assertTrue(w.accept({"path": path})["ok"])
            self.assertFalse(w.accept({"path": "/nope.plist"})["ok"])
            d = w.collect()
            self.assertEqual(d["alerts"], [])

            # Removed and re-added: flagged again.
            os.remove(path)
            w.collect()
            self.write("com.example.sneaky", {"Label": "com.example.sneaky"})
            self.assertEqual(len(w.collect()["alerts"]), 1)

    def test_updates_check_saves_shape(self):
        app = FakeApp(self.tmp.name)
        w = security.Security(app)
        with mock.patch.object(core, "run", lambda *a, **k: load("security_softwareupdate_some.txt")):
            w.updates_busy = True
            w._check_updates()
        saved = app.state.get("security.updates")
        self.assertEqual(set(saved), {"waiting", "restart", "checked"})
        self.assertTrue(saved["restart"])
        self.assertEqual(app.poked, ["security"])
        self.assertFalse(w.updates_busy)


if __name__ == "__main__":
    unittest.main()
