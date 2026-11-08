import json
import os
import unittest

from sightline.widgets import batteries

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def merged():
    io = batteries.parse_ioreg(load("batteries_ioreg.plist"))
    bt = batteries.parse_bluetooth(json.loads(load("batteries_bluetooth.json")))
    acc = batteries.parse_accps(load("batteries_accps.txt").decode())
    return batteries.merge(io, bt, acc)


class Parsers(unittest.TestCase):
    def test_ioreg_dedupes_and_keeps_unnamed(self):
        io = batteries.parse_ioreg(load("batteries_ioreg.plist"))
        self.assertEqual([d["name"] for d in io], ["Magic Mouse", "Magic Keyboard", ""])
        self.assertEqual(io[0]["pct"], 12)
        self.assertEqual(io[2]["addr"], "aabbcc000003")

    def test_bluetooth_connected_only_no_phones(self):
        bt = batteries.parse_bluetooth(json.loads(load("batteries_bluetooth.json")))
        names = [d["name"] for d in bt["devices"]]
        self.assertIn("AirPods Pro", names)
        self.assertIn("Magic Trackpad", names)
        self.assertNotIn("iPhone", names)        # phones are left out
        self.assertNotIn("Beats Studio", names)  # not connected
        pods = next(d for d in bt["devices"] if d["name"] == "AirPods Pro")
        self.assertEqual(pods["parts"], [{"label": "Left", "pct": 85}, {"label": "Right", "pct": 80}, {"label": "Case", "pct": 40}])
        self.assertEqual(pods["pct"], 80)
        self.assertEqual(bt["names"]["aabbcc000003"], "Gaming Mouse")

    def test_accps(self):
        acc = batteries.parse_accps(load("batteries_accps.txt").decode())
        self.assertEqual(acc["Magic Mouse"], (12, False))
        self.assertEqual(acc["Magic Keyboard"], (100, True))
        live = batteries.parse_accps(" -Alex�s Magic Keyboard (id=95944705)\t100%; discharging present: true\n"
                                     " -InternalBattery-0 (id=1)\t80%; charging; 1:00 remaining present: true\n")
        self.assertEqual(list(live), ["Alex’s Magic Keyboard"])


class Merge(unittest.TestCase):
    def test_one_row_per_device_with_names(self):
        devs = merged()
        names = [d["name"] for d in devs]
        self.assertEqual(names, ["Magic Mouse", "Magic Keyboard", "Gaming Mouse", "AirPods Pro", "Magic Trackpad"])
        kb = devs[1]
        self.assertTrue(kb["charging"])

    def test_alert_when_low(self):
        data = batteries.view(merged())
        self.assertEqual([a["key"] for a in data["alerts"]], ["battery-low:Magic Mouse"])
        self.assertEqual(data["alerts"][0]["level"], "warn")
        self.assertEqual(data["summary"], "lowest 12%")
        pods = next(r for r in data["rows"] if r["label"] == "AirPods Pro")
        self.assertEqual(pods["value"], "L 85% · R 80% · C 40%")

    def test_charging_device_does_not_alert(self):
        data = batteries.view([{"name": "Pencil", "pct": 5, "charging": True}])
        self.assertEqual(data["alerts"], [])

    def test_nothing(self):
        self.assertEqual(batteries.parse_ioreg(""), [])
        self.assertEqual(batteries.merge([], {"names": {}, "devices": []}, {}), [])

    def test_unnamed_without_bluetooth_falls_back(self):
        out = batteries.merge([{"name": "", "pct": 40, "addr": "", "kind": "Mouse"}], {"names": {}, "devices": []},
                              {"Some Mouse": (40, False)})
        self.assertEqual(out[0]["name"], "Some Mouse")
        out = batteries.merge([{"name": "", "pct": 40, "addr": "", "kind": "Mouse"}], {"names": {}, "devices": []}, {})
        self.assertEqual(out[0]["name"], "Mouse accessory")


if __name__ == "__main__":
    unittest.main()
