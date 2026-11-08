import json
import os
import unittest

from sightline.widgets import links

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def usbhost():
    return json.loads(load("links_usbhost.json"))


class Speeds(unittest.TestCase):
    def test_speed_mbps(self):
        self.assertEqual(links.speed_mbps("480 Mb/s"), 480)
        self.assertEqual(links.speed_mbps("Up to 480 Mb/sec"), 480)
        self.assertEqual(links.speed_mbps("5 Gb/s"), 5000)
        self.assertEqual(links.speed_mbps("1.5 Mb/s"), 1.5)
        self.assertEqual(links.speed_mbps("Up to 120 Gb/s"), 120000)
        self.assertEqual(links.speed_mbps("high_speed"), 480)
        self.assertEqual(links.speed_mbps("super_speed_plus"), 10000)
        self.assertIsNone(links.speed_mbps(""))
        self.assertIsNone(links.speed_mbps(None))

    def test_speed_text(self):
        self.assertEqual(links.speed_text(480), "480 Mb/s")
        self.assertEqual(links.speed_text(10000), "10 Gb/s")
        self.assertEqual(links.speed_text(1.5), "1.5 Mb/s")

    def test_location(self):
        self.assertEqual(links.location_int("0x03143000"), 0x03143000)
        self.assertEqual(links.location_int("0x14100000 / 5"), 0x14100000)
        self.assertEqual(links.location_int(12), 12)
        self.assertIsNone(links.location_int(None))


class UsbHost(unittest.TestCase):
    """Real output from a Mac Studio on macOS 27 (serials removed): a storage enclosure stuck behind a hub's USB 2 side."""

    def setUp(self):
        self.locs = links.storage_locations(load("links_usb_interfaces.plist"))

    def test_storage_from_interface_class(self):
        self.assertIn(0x03143000, self.locs)
        self.assertEqual(len(self.locs), 1)

    def test_slow_storage_is_flagged(self):
        data = links.build(usbhost(), None, self.locs)
        self.assertEqual(data["state"], "bad")
        self.assertEqual(data["summary"], "1 drive at USB 2 speed")
        self.assertEqual(len(data["alerts"]), 1)
        a = data["alerts"][0]
        self.assertEqual(a["key"], "slow-link:UGREEN Storage Device")
        self.assertEqual(a["text"], "UGREEN Storage Device is running at USB 2 speed (480 Mb/s). Try another port or cable.")
        self.assertEqual(data["rows"][0], {"sep": "Needs attention"})
        self.assertEqual(data["rows"][1]["label"], "UGREEN Storage Device")  # shown first, without scrolling
        row = [r for r in data["rows"] if r.get("label") == "UGREEN Storage Device"][-1]  # and in the tree
        self.assertEqual(row["state"], "bad")
        self.assertEqual(row["value"], "480 Mb/s")
        self.assertEqual(row["depth"], 2)

    def test_hub_with_slow_storage_behind_it(self):
        rows = links.build(usbhost(), None, self.locs)["rows"]
        hub = [r for r in rows if r.get("label") == "USB2.0 Hub" and r.get("depth") == 1][0]
        self.assertEqual(hub["state"], "bad")
        top = [r for r in rows if r.get("label") == "USB2.0 Hub" and r.get("depth") == 0][0]
        self.assertEqual(top["state"], "bad")  # the drive is behind it too
        lg = [r for r in rows if r.get("label") == "USB2.1 Hub"]
        self.assertTrue(lg and all("state" not in r for r in lg))  # no storage behind: just shown

    def test_noise_is_skipped(self):
        labels = [r.get("label") for r in links.build(usbhost(), None, self.locs)["rows"]]
        self.assertNotIn("Element 5 Hub Billboard", labels)
        self.assertNotIn("TPS DMC Family", labels)
        self.assertIn("LG UltraFine Display Camera", labels)
        self.assertIn("Unnamed device", labels)

    def test_fast_storage_is_fine(self):
        sp = usbhost()
        ugreen = sp["SPUSBHostDataType"][4]["_items"][0]["_items"][2]["_items"][0]
        self.assertEqual(ugreen["_name"], "UGREEN Storage Device")
        ugreen["USBDeviceKeyLinkSpeed"] = "10 Gb/s"
        data = links.build(sp, None, self.locs)
        self.assertEqual(data["alerts"], [])
        row = next(r for r in data["rows"] if r.get("label") == "UGREEN Storage Device")
        self.assertEqual(row["state"], "ok")

    def test_bus_separators(self):
        seps = [r["sep"] for r in links.build(usbhost(), None, self.locs)["rows"] if "sep" in r]
        self.assertEqual(seps, ["Needs attention", "USB ports", "USB through a Thunderbolt display or dock"])


class Legacy(unittest.TestCase):
    """SPUSBDataType (macOS 14 and earlier): speed codes and a Media list for drives."""

    def test_media_marks_storage(self):
        sp = json.loads(load("links_usb_legacy.json"))
        data = links.build(sp, None, set())
        keys = [a["key"] for a in data["alerts"]]
        self.assertEqual(keys, ["slow-link:Portable SSD T7"])
        fast = next(r for r in data["rows"] if r.get("label") == "Extreme Pro SSD")
        self.assertEqual((fast["state"], fast["value"]), ("ok", "10 Gb/s"))
        hub = next(r for r in data["rows"] if r.get("label") == "USB2.0 Hub")
        self.assertEqual(hub["state"], "bad")
        rx = next(r for r in data["rows"] if r.get("label") == "USB Receiver")
        self.assertNotIn("state", rx)

    def test_host_empty_falls_back(self):
        buses = links.usb_tree({"SPUSBHostDataType": [], "SPUSBDataType": []})
        self.assertEqual(buses, [])


class Thunderbolt(unittest.TestCase):
    def test_ports_and_speeds(self):
        tb = json.loads(load("links_thunderbolt.json"))
        rows, count = links.tb_rows(links.tb_tree(tb))
        self.assertEqual(count, 3)
        by = {r["label"]: r for r in rows if "label" in r}
        self.assertEqual(by["Element 5 Hub"]["value"], "80 Gb/s")
        self.assertEqual(by["TB501Pro"]["depth"], 1)
        self.assertEqual(by["UltraFine 5K"]["value"], "40 Gb/s")
        self.assertIn("Thunderbolt 3", by["UltraFine 5K"]["sub"])
        self.assertEqual([r["sep"] for r in rows if "sep" in r], ["Thunderbolt port 4", "Thunderbolt port 3"])

    def test_nothing_plugged_in(self):
        data = links.build(None, None, set())
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["rows"], [])


if __name__ == "__main__":
    unittest.main()
