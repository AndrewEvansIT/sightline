import os
import struct
import tempfile
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import devices

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    return slurp(os.path.join(FIX, name))


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FakeConfig:
    def widget(self, wid):
        return {}


class FakeApp:
    def __init__(self, tmp):
        self.config = FakeConfig()
        self.state = core.State(os.path.join(tmp, "state.json"))

    def poke(self, wid):
        pass


class FakeResolver:
    def __init__(self, names):
        self.names = names

    def wait(self, ips, timeout=1.0):
        return {ip: self.names.get(ip, "") for ip in ips}


class Parsers(unittest.TestCase):
    def test_arp(self):
        rows = devices.parse_arp(load("devices_arp_an.txt"))
        ips = [r[0] for r in rows]
        self.assertNotIn("192.168.1.2", ips)          # incomplete
        self.assertNotIn("192.168.1.255", ips)        # broadcast
        self.assertNotIn("224.0.0.251", ips)          # multicast
        self.assertIn(("192.168.1.11", "00:11:22:33:44:05", "en14", False), rows)  # short MAC padded
        self.assertIn(("192.168.1.23", "00:11:22:33:44:03", "en14", True), rows)   # this Mac: permanent
        only = devices.parse_arp(load("devices_arp_an.txt"), "en14")
        self.assertTrue(all(r[2] == "en14" for r in only))
        self.assertNotIn("192.168.1.250", [r[0] for r in only])

    def test_ethers(self):
        self.assertEqual(devices.parse_ethers(load("devices_ifconfig_all.txt")),
                         {"00:11:22:33:44:10", "00:11:22:33:44:03", "02:00:11:22:33:0c"})

    def test_sweep_targets(self):
        t = devices.sweep_targets("192.168.1.23", 24)
        self.assertEqual(len(t), 253)
        self.assertNotIn("192.168.1.23", t)
        self.assertEqual((t[0], t[-1]), ("192.168.1.1", "192.168.1.254"))
        self.assertEqual(devices.sweep_targets("172.16.4.20", 20), [])   # too big: ARP table only
        self.assertEqual(len(devices.sweep_targets("10.9.8.7", 28)), 13)
        self.assertEqual(devices.sweep_targets(None, None), [])

    def test_private_mac_and_names(self):
        self.assertTrue(devices.private_mac("5a:11:22:33:44:72"))
        self.assertTrue(devices.private_mac("02:00:11:22:33:0c"))
        self.assertFalse(devices.private_mac("00:11:22:33:44:01"))
        self.assertEqual(devices.short_host("Example-iPad.local"), "Example-iPad")
        self.assertEqual(devices.short_host("printer.lan"), "printer")
        self.assertEqual(devices.short_host("living-room.hsd1.ca.comcast.net"), "living-room")
        self.assertEqual(devices.short_host(""), "")

    def test_mdns_ptr(self):
        q = devices.mdns_query("192.168.1.104", 7)
        self.assertEqual(q[:2], b"\x00\x07")
        self.assertIn(b"\x03104\x011\x03168\x03192\x07in-addr\x04arpa\x00", q)
        target = b"\x0cExample-iPad\x05local\x00"
        answer = b"\xc0\x0c" + struct.pack(">HHIH", 12, 0x8001, 10, len(target)) + target
        packet = struct.pack(">HHHHHH", 7, 0x8400, 1, 1, 0, 0) + q[12:] + answer
        self.assertEqual(devices.parse_mdns_ptr(packet), {"192.168.1.104": "Example-iPad.local"})
        self.assertEqual(devices.parse_mdns_ptr(packet[:20]), {})
        self.assertEqual(devices.parse_mdns_ptr(b""), {})
        self.assertEqual(devices.mdns_names([]), {})

    def test_merge_known(self):
        seen = {"aa:aa:aa:aa:aa:01": {"ip": "192.168.1.1", "name": "router"}, "aa:aa:aa:aa:aa:02": {"ip": "192.168.1.9", "name": ""}}
        known, new = devices.merge_known({}, seen, 100, first_sweep=True)
        self.assertEqual(new, [])
        self.assertEqual(set(known), set(seen))
        self.assertFalse(known["aa:aa:aa:aa:aa:02"]["new"])
        seen["aa:aa:aa:aa:aa:03"] = {"ip": "192.168.1.50", "name": "Example-Phone"}
        known, new = devices.merge_known(known, seen, 200, first_sweep=False)
        self.assertEqual(new, ["aa:aa:aa:aa:aa:03"])
        self.assertTrue(known["aa:aa:aa:aa:aa:03"]["new"])
        self.assertEqual(known["aa:aa:aa:aa:aa:03"]["auto_name"], "Example-Phone")
        self.assertEqual(known["aa:aa:aa:aa:aa:01"]["last_seen"], 200)
        # still new on the next sweep until marked known
        _, new = devices.merge_known(known, seen, 300, first_sweep=False)
        self.assertEqual(new, ["aa:aa:aa:aa:aa:03"])

    def test_display_name(self):
        self.assertEqual(devices.display_name({}, "192.168.1.1", "192.168.1.1"), "Router")
        self.assertEqual(devices.display_name({"name": "Kitchen"}, "192.168.1.1", "192.168.1.1"), "Kitchen")
        self.assertEqual(devices.display_name({"auto_name": "nas"}, "192.168.1.5"), "nas")
        self.assertEqual(devices.display_name({}, "192.168.1.5"), "Unknown device")
        self.assertEqual(devices.display_name({}, me=True), "This Mac")


def fake_run(arp="devices_arp_an.txt"):
    def run(cmd, timeout=20, env=None, stderr=True):
        c = " ".join(cmd)
        if c == "route -n get default":
            return load("network_route_default.txt")
        if c == "ifconfig en14":
            return load("devices_ifconfig_en.txt")
        if c == "ifconfig":
            return load("devices_ifconfig_all.txt")
        if c == "arp -an":
            return load(arp)
        return ""
    return run


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = FakeApp(self.tmp.name)
        self.w = devices.Devices(self.app)
        self.patches = [mock.patch.object(core, "run", fake_run()),
                        mock.patch.object(devices, "mdns_names", lambda ips, local_ip="": {"192.168.1.11": "Example-Printer.local"}),
                        mock.patch.object(devices, "resolver", lambda: FakeResolver({"192.168.1.104": "nas.local"})),
                        mock.patch.object(devices.Devices, "nudge_permission", lambda self, r: None)]
        for p in self.patches:
            p.start()
        self.swept = []
        self.w.sweep = lambda targets: self.swept.append(len(targets))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_first_sweep_then_new_device(self):
        d = self.w.collect()
        self.assertEqual(self.swept, [253])
        self.assertEqual(d["subnet"], "192.168.1.0/24")
        self.assertEqual(d["alerts"], [])
        names = {x["ip"]: x["name"] for x in d["devices"]}
        self.assertEqual(names["192.168.1.23"], "This Mac")
        self.assertEqual(names["192.168.1.1"], "Router")
        self.assertEqual(names["192.168.1.104"], "nas")
        self.assertEqual(names["192.168.1.72"], "Unknown device")
        self.assertEqual(names["192.168.1.11"], "Example-Printer")
        self.assertNotIn("192.168.1.239", names)   # this Mac's Wi-Fi address (its own MAC)
        self.assertNotIn("192.168.1.250", names)   # only in the Wi-Fi interface's table
        self.assertEqual(d["devices"][0]["name"], "This Mac")
        self.assertTrue(next(x for x in d["devices"] if x["ip"] == "192.168.1.72")["private"])
        self.assertEqual(d["summary"], "6 online")

        # A new device appears on a later sweep.
        with open(os.path.join(self.tmp.name, "arp2.txt"), "w") as f:
            f.write(load("devices_arp_an.txt") + "? (192.168.1.77) at 00:11:22:33:44:4d on en14 ifscope [ethernet]\n")
        self.w.last = None
        with mock.patch.object(core, "run", lambda cmd, **k: slurp(os.path.join(self.tmp.name, "arp2.txt"))
                                  if cmd == ["arp", "-an"] else fake_run()(cmd)):
            d2 = self.w.collect()
        self.assertEqual([a["key"] for a in d2["alerts"]], ["new-device:00:11:22:33:44:4d"])
        self.assertIn("192.168.1.77", d2["alerts"][0]["text"])
        self.assertEqual(d2["state"], "warn")

        # Mark it known: the alert goes away; reuses the recent sweep (no new ping sweep).
        res = self.w.mark_known({"mac": "00:11:22:33:44:4d"})
        self.assertTrue(res["ok"])
        with mock.patch.object(core, "run", lambda cmd, **k: slurp(os.path.join(self.tmp.name, "arp2.txt"))
                               if cmd == ["arp", "-an"] else fake_run()(cmd)):
            d3 = self.w.collect()
        self.assertEqual(d3["alerts"], [])
        self.assertEqual(self.swept, [253, 253])

    def test_rename_and_forget(self):
        self.w.collect()
        self.assertTrue(self.w.rename({"mac": "0:11:22:33:44:5", "name": "  Printer  "})["ok"])
        self.w.last = None
        d = self.w.collect()
        row = next(x for x in d["devices"] if x["mac"] == "00:11:22:33:44:05")
        self.assertEqual((row["name"], row["renamed"]), ("Printer", True))  # your name wins over the mDNS one
        self.assertTrue(self.w.rename({"mac": "00:11:22:33:44:05", "name": ""})["ok"])
        self.assertNotIn("name", self.app.state.get("devices.known")["00:11:22:33:44:05"])
        self.assertTrue(self.w.forget({"mac": "00:11:22:33:44:05"})["ok"])
        self.assertNotIn("00:11:22:33:44:05", self.app.state.get("devices.known"))
        self.assertFalse(self.w.forget({"mac": "00:11:22:33:44:05"})["ok"])
        self.assertFalse(self.w.rename({"mac": "de:ad:be:ef:00:00", "name": "x"})["ok"])

    def test_empty_first_sweep_keeps_no_baseline(self):
        with mock.patch.object(core, "run", fake_run(arp="network_ping_fail.txt")):
            d = self.w.collect()
        self.assertIn("Local Network", d["note"])
        self.assertIsNone(self.app.state.get("devices.known"))


if __name__ == "__main__":
    unittest.main()
