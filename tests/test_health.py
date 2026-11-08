import json
import os
import plistlib
import tempfile
import time
import unittest

from sightline.widgets import health, mac_battery

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


class MacBattery(unittest.TestCase):
    def test_laptop_fixture(self):
        d = mac_battery.read_battery(load("health_smartbattery.plist"))
        b = mac_battery.parse_battery(d)
        self.assertEqual(b["pct"], 73)
        self.assertTrue(b["charging"])
        self.assertEqual(b["health"], 94)  # 5893 / 6249
        self.assertEqual(b["cycles"], 214)
        self.assertEqual(b["temp_c"], 30.6)
        self.assertEqual(b["to_full_min"], 47)
        self.assertIsNone(b["to_empty_min"])
        self.assertEqual(b["charger"], {"watts": 94, "name": "96W USB-C Power Adapter", "wireless": False})
        self.assertEqual(b["power_in_w"], 38.8)
        self.assertEqual(b["battery_w"], 26.8)
        self.assertEqual(b["condition"], "Normal")
        data = mac_battery.view(b)
        self.assertEqual(data["summary"], "73% · charging")
        self.assertEqual(data["alerts"], [])
        self.assertEqual(data["rows"][0]["label"], "Charging, full in 47 min")

    def test_desktop_has_no_battery(self):
        self.assertIsNone(mac_battery.read_battery(load("health_smartbattery_desktop.plist")))
        self.assertIsNone(mac_battery.read_battery(""))

    def test_worn_battery_on_battery_power(self):
        d = mac_battery.read_battery(load("health_smartbattery.plist"))
        d.update(AppleRawMaxCapacity=4700, IsCharging=False, ExternalConnected=False, AvgTimeToEmpty=312,
                 Amperage=2**64 - 1100, AdapterDetails={"FamilyCode": 0})
        b = mac_battery.parse_battery(d)
        self.assertEqual(b["health"], 75)
        self.assertEqual(b["condition"], "Service recommended")
        self.assertEqual(b["to_empty_min"], 312)
        self.assertEqual(b["battery_w"], -13.8)
        self.assertIsNone(b["charger"])
        data = mac_battery.view(b)
        self.assertEqual([a["key"] for a in data["alerts"]], ["battery-health"])
        self.assertEqual(data["rows"][0]["label"], "On battery, 5 h 12 min left")
        self.assertIn({"label": "Using from the battery", "value": "13.8 W"}, data["rows"])

    def test_plugged_not_charging(self):
        d = mac_battery.read_battery(load("health_smartbattery.plist"))
        d.update(IsCharging=False, FullyCharged=False, CurrentCapacity=80)
        data = mac_battery.view(mac_battery.parse_battery(d))
        self.assertTrue(data["rows"][0]["label"].startswith("Plugged in, not charging"))


def write_ips(folder, name, header, mtime):
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write(json.dumps(header) + "\n{\n  \"uptime\" : 1000\n}\n")
    os.utime(path, (mtime, mtime))
    return path


class Reports(unittest.TestCase):
    def test_scan(self):
        now = time.time()
        today, week = now - 3600 * 2, now - 7 * 86400
        a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
        write_ips(a, "node-2026-11-08-025942.ips", {"app_name": "node", "bug_type": "309", "name": "node"}, now - 60)
        write_ips(a, "node-2026-11-08-025942.000.ips", {"app_name": "node", "bug_type": "309"}, now - 50)
        write_ips(a, "echo-2026-11-08-025942.ips", {"app_name": "echo", "bug_type": "309"}, now - 40)
        write_ips(a, "Old-2026-11-06-010101.ips", {"app_name": "Old", "bug_type": "309"}, now - 2 * 86400)
        write_ips(b, "ExcUserFault_Agent-2026-11-08-1.ips", {"app_name": "Agent", "bug_type": "309", "is_simulated": 1}, now - 30)
        write_ips(b, "JetsamEvent-2026-11-08.ips", {"bug_type": "298"}, now - 30)
        write_ips(b, "panic-full-2026-11-05-101010.0002.ips", {"bug_type": "210", "name": ""}, now - 3 * 86400)
        write_ips(b, "panic-full-2026-10-01-101010.ips", {"bug_type": "210"}, now - 30 * 86400)
        with open(os.path.join(a, "Finder_2026-11-08-101010_Mac.crash"), "w") as f:
            f.write("Process: Finder\n")
        with open(os.path.join(a, "broken.ips"), "w") as f:
            f.write("not json\n")
        r = health.scan_reports((a, b, "/nonexistent-folder"), today, week)
        self.assertEqual(r["crashes"], {"node": 2, "echo": 1, "Finder": 1})
        self.assertEqual(len(r["panics"]), 1)
        self.assertEqual(r["readable"], [a, b])

    def test_app_from_filename(self):
        self.assertEqual(health.app_from_filename("node-2026-11-06-163013.000.ips"), "node")
        self.assertEqual(health.app_from_filename("Google Chrome Helper-2026-11-06-163013.ips"), "Google Chrome Helper")
        self.assertEqual(health.app_from_filename("Finder_2026-11-06-1630_Mac.crash"), "Finder")


class Disk(unittest.TestCase):
    def test_internal_disk(self):
        d = health.internal_disk(load("health_block_storage.plist"))
        self.assertEqual(d["name"], "APPLE SSD AP16384Z")
        self.assertEqual(d["bsd"], "disk0")
        self.assertGreater(d["written"], 10**12)

    def test_smart(self):
        s = health.parse_smart(json.loads(load("health_smartctl.json")))
        self.assertEqual(s["wear_pct"], 0)
        self.assertEqual(s["spare"], 100)
        self.assertEqual(s["written"], 133621457 * 512000)
        self.assertTrue(s["passed"])
        self.assertIsNone(health.parse_smart(None))
        self.assertIsNone(health.parse_smart({}))

    def test_boot_time(self):
        self.assertEqual(health.boot_time("{ sec = 1793943851, usec = 461273 } Thu Nov  5 21:44:11 2026"), 1793943851)
        self.assertIsNone(health.boot_time(""))


class Build(unittest.TestCase):
    def test_card(self):
        now = 1794000000
        reports = {"crashes": {"node": 3, "echo": 1, "bash": 1}, "panics": [], "readable": ["x"]}
        disk = {"name": "SSD", "bsd": "disk0", "written": 300 * 2**30, "read": 0}
        data = health.build(reports, disk, None, False, now - 3 * 86400, {"waiting": ["macOS 27.2.1"], "restart": True}, now)
        labels = {r["label"]: r for r in data["rows"]}
        self.assertEqual(labels["Crashes today"]["value"], "5 in 3 apps")
        self.assertEqual(labels["Crashes today"]["sub"], "node 3 · bash · echo")
        self.assertEqual(labels["Kernel panics this week"]["value"], "None")
        self.assertEqual(labels["Written to the SSD since restart"]["sub"], "About 100 GB a day")
        self.assertEqual(labels["Up for"]["value"], "3 days 0 h")
        self.assertEqual(labels["Restart to finish an update"]["sub"], "macOS 27.2.1")
        self.assertIn("Install smartmontools to see lifetime wear.", data["note"])
        self.assertEqual(data["alerts"][0]["key"], "crashes")
        self.assertEqual(data["alerts"][0]["level"], "info")
        self.assertEqual(data["summary"], "up 3 days 0 h · 3 apps crashed today")

    def test_quiet_mac_with_smartctl(self):
        now = 1794000000
        smart = {"wear_pct": 2, "written": 68 * 10**12, "spare": 100, "passed": True}
        data = health.build({"crashes": {}, "panics": [], "readable": []}, None, smart, True, now - 600, None, now)
        labels = {r["label"]: r for r in data["rows"]}
        self.assertEqual(labels["Crashes today"]["value"], "None")
        self.assertNotIn("Kernel panics this week", labels)  # folder unreadable: say nothing
        self.assertEqual(labels["SSD wear"]["value"], "2% used")
        self.assertEqual(data["note"], "")
        self.assertEqual(data["alerts"], [])

    def test_panic_row(self):
        now = 1794000000
        data = health.build({"crashes": {}, "panics": [now - 7200], "readable": ["x"]}, None, None, True, None, None, now)
        row = next(r for r in data["rows"] if r["label"] == "Kernel panics this week")
        self.assertEqual((row["value"], row["state"]), ("1", "bad"))
        self.assertEqual(data["state"], "bad")


if __name__ == "__main__":
    unittest.main()
