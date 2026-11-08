import datetime
import os
import plistlib
import tempfile
import time
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import storage

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def info(name):
    return plistlib.loads(load(name))


class Mounts(unittest.TestCase):
    def test_candidates(self):
        mounts = storage.parse_mount(load("storage_mount.txt").decode())
        got = [(c["mount"], c["kind"]) for c in storage.candidates(mounts)]
        self.assertEqual(got, [("/System/Volumes/Data", "internal"), ("/Volumes/Models", "local"),
                               ("/Volumes/Backup Drive", "local"), ("/Volumes/Installer", "local"),
                               ("/Volumes/share", "network")])

    def test_classify(self):
        self.assertEqual(storage.classify(info("storage_diskutil_dmg.plist"), "local")[0], False)
        self.assertEqual(storage.classify(info("storage_diskutil_simulator.plist"), "local")[0], False)
        self.assertEqual(storage.classify(info("storage_diskutil_external.plist"), "local"), (True, "external", "PCI-Express"))
        self.assertEqual(storage.classify(info("storage_diskutil_usb.plist"), "local"), (True, "external", "USB"))
        self.assertEqual(storage.classify(info("storage_diskutil_internal.plist"), "internal")[1], "internal")

    def test_names(self):
        self.assertEqual(storage.drive_name("/System/Volumes/Data", info("storage_diskutil_internal.plist")), "Macintosh HD")
        self.assertEqual(storage.drive_name("/Volumes/Backup Drive"), "Backup Drive")

    def test_pct_state(self):
        self.assertEqual(storage.pct_state(89.9), "ok")
        self.assertEqual(storage.pct_state(90), "warn")
        self.assertEqual(storage.pct_state(97), "bad")
        self.assertEqual(storage.pct_state(None), "off")


class Dropouts(unittest.TestCase):
    def test_gone_drive_is_a_dropout(self):
        out = storage.update_dropouts({"Models": "/Volumes/Models"}, {}, [], {}, now=1000)
        self.assertEqual(out, [{"name": "Models", "mount": "/Volumes/Models", "at": 1000}])

    def test_ejected_here_is_not(self):
        out = storage.update_dropouts({"Models": "/Volumes/Models"}, {}, [], {"Models": 950}, now=1000)
        self.assertEqual(out, [])

    def test_old_dropouts_pruned(self):
        old = [{"name": "X", "at": 0}, {"name": "Y", "at": 8 * 86400 - 10}]
        out = storage.update_dropouts({}, {}, old, {}, now=8 * 86400)
        self.assertEqual([d["name"] for d in out], ["Y"])


class TimeMachine(unittest.TestCase):
    def test_prefs(self):
        tm = storage.parse_time_machine(load("storage_timemachine.plist"))
        self.assertTrue(tm["setup"])
        self.assertEqual(tm["dest"], "Backup Drive")
        want = datetime.datetime(2026, 11, 8, 11, 43, 51, tzinfo=datetime.timezone.utc).timestamp()
        self.assertEqual(tm["last"], want)

    def test_not_set_up(self):
        self.assertFalse(storage.parse_time_machine("Domain /Library/Preferences/com.apple.TimeMachine does not exist")["setup"])
        self.assertFalse(storage.parse_time_machine(plistlib.dumps({"AutoBackup": False}))["setup"])
        self.assertEqual(storage.backup_view({"setup": False}), (None, None))

    def test_latestbackup(self):
        ts = storage.parse_latestbackup("/Volumes/.timemachine/ABC/2026-11-08-104037.backup/2026-11-08-104037.backup\n")
        self.assertEqual(ts, time.mktime(datetime.datetime(2026, 11, 8, 10, 40, 37).timetuple()))
        self.assertIsNone(storage.parse_latestbackup("tmutil: latestbackup requires Full Disk Access privileges."))

    def test_status(self):
        raw = plistlib.dumps({"Running": 1, "BackupPhase": "Copying", "Progress": {"Percent": 0.42}})
        self.assertEqual(storage.parse_tm_status(raw), (True, 42))
        self.assertEqual(storage.parse_tm_status(plistlib.dumps({"Running": 0, "Percent": "-1"})), (False, None))

    def test_old_backup_alerts(self):
        now = 10 * 86400
        row, alert = storage.backup_view({"setup": True, "last": now - 4 * 86400, "auto": True, "dest": "B"}, now=now)
        self.assertEqual(row["state"], "warn")
        self.assertEqual(alert["key"], "backup-old")
        row, alert = storage.backup_view({"setup": True, "last": now - 3600, "auto": True, "dest": "B"}, now=now)
        self.assertEqual((row["state"], alert), ("ok", None))
        row, alert = storage.backup_view({"setup": True, "last": None, "auto": True}, now=now)
        self.assertEqual(alert["key"], "backup-old")
        row, alert = storage.backup_view({"setup": True, "last": now - 5 * 86400, "auto": True}, running=True, percent=10, now=now)
        self.assertEqual(row["value"], "Backing up now (10%)")
        self.assertIsNone(alert)


class FakeApp:
    def __init__(self, folder):
        self.state = core.State(os.path.join(folder, "state.json"))
        self.widgets = {}


class Collect(unittest.TestCase):
    """The whole card from fixtures. Nothing touches real drives: every command and disk call is replaced."""

    INFO = {"/System/Volumes/Data": "storage_diskutil_internal.plist", "/Volumes/Models": "storage_diskutil_external.plist",
            "/Volumes/Backup Drive": "storage_diskutil_usb.plist", "/Volumes/Installer": "storage_diskutil_dmg.plist"}
    SPACE = {"/System/Volumes/Data": (1000, 400), "/Volumes/Models": (1000, 40), "/Volumes/Backup Drive": (1000, 900),
             "/Volumes/share": (1000, 500)}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mount_text = load("storage_mount.txt").decode()
        self.calls = []
        self.w = storage.Storage(FakeApp(self.tmp))
        self.w.disk_info = lambda m: info(self.INFO[m]) if m in self.INFO else {}
        self.patches = [mock.patch.object(storage.core, "run", self.fake_run),
                        mock.patch.object(storage, "space", lambda m, network=False: self.SPACE.get(m, (None, None)))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def fake_run(self, cmd, timeout=20, env=None, stderr=True):
        self.calls.append(cmd)
        if cmd == ["mount"]:
            return self.mount_text
        if cmd[:2] == ["defaults", "export"]:
            return load("storage_timemachine.plist").decode()
        if cmd[:2] == ["tmutil", "latestbackup"]:
            return "tmutil: latestbackup requires Full Disk Access privileges."
        if cmd[:2] == ["tmutil", "status"]:
            return plistlib.dumps({"Running": 0}).decode()
        if cmd[:2] == ["diskutil", "eject"]:
            return f"Disk {cmd[2]} ejected"
        return ""

    def test_drives_and_alerts(self):
        data = self.w.collect()
        names = [d["name"] for d in data["drives"]]
        self.assertEqual(names, ["Macintosh HD", "Models", "Backup Drive", "share"])
        by = {d["name"]: d for d in data["drives"]}
        self.assertEqual(by["Models"]["state"], "warn")
        self.assertTrue(by["Models"]["ejectable"])
        self.assertFalse(by["Macintosh HD"]["ejectable"])
        self.assertFalse(by["share"]["ejectable"])
        self.assertIn("Network share", by["share"]["sub"])
        self.assertIn("Time Machine backups", by["Backup Drive"]["sub"])
        keys = [a["key"] for a in data["alerts"]]
        self.assertIn("disk-full:Models", keys)
        self.assertNotIn("drive-gone:Models", keys)

    def test_dropout_then_back(self):
        self.w.collect()
        self.mount_text = "\n".join(l for l in self.mount_text.splitlines() if "/Volumes/Models" not in l)
        data = self.w.collect()
        self.assertIn("drive-gone:Models", [a["key"] for a in data["alerts"]])
        self.assertEqual(data["dropouts"][0]["name"], "Models")
        self.mount_text = load("storage_mount.txt").decode()
        data = self.w.collect()
        self.assertNotIn("drive-gone:Models", [a["key"] for a in data["alerts"]])
        self.assertEqual(data["rows"][-1]["sub"], "Back now")

    def test_eject_only_known_external(self):
        self.w.collect()
        res = self.w.eject({"mount": "/System/Volumes/Data"})
        self.assertFalse(res["ok"])
        res = self.w.eject({"mount": "/Volumes/Nope"})
        self.assertFalse(res["ok"])
        self.assertFalse(any(c[:2] == ["diskutil", "eject"] for c in self.calls))
        res = self.w.eject({"mount": "/Volumes/Models"})  # the fake run answers; nothing real is ejected
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls[-1], ["diskutil", "eject", "/Volumes/Models"])
        # A clean eject isn't a dropout.
        self.mount_text = "\n".join(l for l in self.mount_text.splitlines() if "/Volumes/Models" not in l)
        data = self.w.collect()
        self.assertEqual(data["dropouts"], [])


if __name__ == "__main__":
    unittest.main()
