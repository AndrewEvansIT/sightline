import json
import os
import tempfile
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import network

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def widget(self, wid):
        return dict(self.values.get(wid, {}))


class FakeHistory:
    def __init__(self):
        self.added, self.runs = [], []

    def add(self, key, value):
        self.added.append((key, value))

    def add_run(self, *args):
        self.runs.append(args)


class FakeApp:
    def __init__(self, tmp, values=None):
        self.config = FakeConfig(values)
        self.state = core.State(os.path.join(tmp, "state.json"))
        self.history = FakeHistory()
        self.poked = []

    def poke(self, wid):
        self.poked.append(wid)

    def data(self, wid):
        return {}


class Parsers(unittest.TestCase):
    def test_ping(self):
        self.assertEqual(network.parse_ping(load("network_ping_ok.txt")), (14.5, 0.0))
        self.assertEqual(network.parse_ping(load("network_ping_fail.txt")), (None, 100.0))
        self.assertEqual(network.parse_ping(""), (None, 100.0))

    def test_route(self):
        self.assertEqual(network.parse_route(load("network_route_default.txt")), {"gateway": "192.168.1.1", "interface": "en14"})
        self.assertEqual(network.parse_route(load("network_route_vpn.txt")), {"gateway": "", "interface": "utun4"})

    def test_hardware_ports_and_type(self):
        ports = network.parse_hardware_ports(load("network_hardware_ports.txt"))
        self.assertEqual(ports["en1"], "Wi-Fi")
        self.assertEqual(ports["en14"], "USB 10/100/1G/2.5G LAN")
        self.assertEqual(network.connection_type("en1", ports), "Wi-Fi")
        self.assertEqual(network.connection_type("en0", ports), "Ethernet")
        self.assertEqual(network.connection_type("en14", ports), "Ethernet")
        self.assertEqual(network.connection_type("en8", ports), "Ethernet")
        self.assertEqual(network.connection_type("en2", ports), "Thunderbolt")
        self.assertEqual(network.connection_type("bridge0", ports), "Thunderbolt")
        self.assertEqual(network.connection_type("en5", ports), "iPhone")
        self.assertEqual(network.connection_type("utun3", ports), "VPN")
        self.assertEqual(network.connection_type("en99", ports), "Unknown")

    def test_dns(self):
        servers = network.parse_dns(load("network_scutil_dns.txt"))
        self.assertEqual(servers, ["2001:db8:53::1", "192.168.1.1", "1.1.1.1", "203.0.113.53"])  # not the scoped 8.8.8.8
        self.assertEqual(network.dns_label("1.1.1.1"), "Cloudflare")
        self.assertEqual(network.dns_label("8.8.4.4"), "Google")
        self.assertEqual(network.dns_label("9.9.9.9"), "Quad9")
        self.assertEqual(network.dns_label("192.168.1.1", "192.168.1.1"), "your router")
        self.assertEqual(network.dns_label("10.1.2.3", "192.168.1.1"), "local network")
        self.assertEqual(network.dns_label("203.0.113.53"), "")

    def test_vpn_services(self):
        self.assertEqual(network.parse_vpn_services(load("network_scutil_nc_none.txt")), [])
        self.assertEqual(network.parse_vpn_services(load("network_scutil_nc_connected.txt")), ["Example VPN"])

    def test_trace(self):
        t = network.parse_trace(load("network_trace.txt"))
        self.assertEqual((t["ip"], t["loc"], t["colo"]), ("203.0.113.7", "US", "SEA"))

    def test_speed(self):
        s = network.parse_speed(load("network_quality.json"))
        self.assertEqual(s, {"down": 280, "up": 42, "rpm": 590, "rtt_ms": 25})
        self.assertIsNone(network.parse_speed("not json"))
        self.assertIsNone(network.parse_speed("{}"))

    def test_wifi(self):
        w = network.parse_wifi(json.loads(load("network_airport.json")))
        self.assertEqual(w["device"], "en1")
        self.assertNotIn("ssid", w)  # redacted without Location permission
        self.assertEqual((w["channel"], w["band"], w["width_mhz"]), (69, "6 GHz", 160))
        self.assertEqual((w["signal"], w["noise"], w["quality"]), (-47, -92, "good"))
        self.assertEqual((w["generation"], w["rate_mbps"]), ("Wi-Fi 6E", 2041))
        w2 = network.parse_wifi(json.loads(load("network_airport_ssid.json")), "en1")
        self.assertEqual((w2["ssid"], w2["quality"], w2["generation"], w2["band"]), ("ExampleNet", "weak", "Wi-Fi 5", "5 GHz"))
        self.assertIsNone(network.parse_wifi(json.loads(load("network_airport.json")), "awdl0"))  # no channel: not connected
        self.assertIsNone(network.parse_wifi(None))

    def test_netmask_and_lan(self):
        self.assertEqual(network.parse_netmask(load("devices_ifconfig_en.txt")), ("192.168.1.23", 24))
        self.assertEqual(network.parse_netmask(load("devices_ifconfig_big.txt")), ("172.16.4.20", 20))
        self.assertEqual(network.parse_netmask(""), (None, None))
        for ip in ("10.0.0.5", "172.20.1.1", "192.168.0.9", "100.64.1.1", "fd12::1", "::ffff:192.168.1.2"):
            self.assertTrue(network.is_lan(ip), ip)
        for ip in ("1.1.1.1", "203.0.113.7", "2001:db8::1", "fe80::1", "nonsense"):
            self.assertFalse(network.is_lan(ip), ip)


def fake_run(ping="network_ping_ok.txt", vpn="network_scutil_nc_none.txt", inet="network_route_default.txt"):
    def run(cmd, timeout=20, env=None, stderr=True):
        c = " ".join(cmd)
        if cmd[0] == "ping":
            return load(ping)
        if c == "route -n get default":
            return load("network_route_default.txt")
        if c == "route -n get 1.1.1.1":
            return load(inet)
        if cmd[0] == "networksetup":
            return load("network_hardware_ports.txt")
        if c == "ipconfig getifaddr en14":
            return "192.168.1.23\n"
        if c == "ipconfig getifaddr en1":
            return "192.168.1.239\n"
        if c == "scutil --nc list":
            return load(vpn)
        if c == "scutil --dns":
            return load("network_scutil_dns.txt")
        return ""
    return run


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = FakeApp(self.tmp.name, {"network": {"public_ip": False, "speed_test_hours": 0}})
        self.w = network.Network(self.app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_online_connection_and_wifi(self):
        with mock.patch.object(core, "run", fake_run()), \
                mock.patch.object(core, "run_json", lambda *a, **k: json.loads(load("network_airport.json"))):
            self.w._wifi_scan("en1")
            self.w.wifi_at = 9e12  # don't start the background scan again
            d = self.w.collect()
        self.assertEqual(d["summary"], "Online · 14 ms")
        self.assertEqual(d["connection"], {"type": "Ethernet", "device": "en14", "port": "USB 10/100/1G/2.5G LAN",
                                           "ip": "192.168.1.23", "router": "192.168.1.1"})
        self.assertEqual(d["vpn"], {"on": False, "name": ""})
        self.assertEqual([x["label"] for x in d["dns"]], ["", "your router", "Cloudflare"])
        self.assertFalse(d["wifi"]["primary"])
        self.assertIsNone(d["public"])
        self.assertEqual(d["alerts"], [])
        self.assertIn(("network.ping", 14.5), self.app.history.added)

    def test_vpn(self):
        with mock.patch.object(core, "run", fake_run(vpn="network_scutil_nc_connected.txt")):
            self.w.wifi_at = 9e12
            self.assertEqual(self.w.collect()["vpn"], {"on": True, "name": "Example VPN"})
        with mock.patch.object(core, "run", fake_run(inet="network_route_vpn.txt")):
            self.assertEqual(self.w.collect()["vpn"], {"on": True, "name": "tunnel utun4"})

    def test_offline_after_three_fails(self):
        self.w.wifi_at = 9e12
        with mock.patch.object(core, "run", fake_run(ping="network_ping_fail.txt")):
            first = self.w.collect()
            self.assertEqual(first["alerts"], [])
            self.assertEqual(first["state"], "warn")
            self.w.collect()
            third = self.w.collect()
        self.assertEqual(third["summary"], "Offline")
        self.assertEqual([a["key"] for a in third["alerts"]], ["offline"])
        self.assertEqual(third["state"], "bad")
        with mock.patch.object(core, "run", fake_run()):
            self.assertEqual(self.w.collect()["alerts"], [])

    def test_speed_test_stores_result(self):
        with mock.patch.object(core, "run", lambda *a, **k: load("network_quality.json")):
            self.w.speed_busy = True
            self.w._speed_test()
        saved = self.app.state.get("network.speed")
        self.assertEqual((saved["down"], saved["up"]), (280, 42))
        self.assertEqual(self.app.history.runs[0][:2], ("speed", "internet"))
        self.assertFalse(self.w.speed_busy)
        self.assertEqual(self.app.poked, ["network"])


class ResolverTest(unittest.TestCase):
    def test_cache_and_no_blocking(self):
        r = network.Resolver(workers=2)
        with mock.patch.object(network.socket, "gethostbyaddr", lambda ip: ("host.example.com", [], [ip])):
            self.assertEqual(r.wait(["203.0.113.9"], timeout=2), {"203.0.113.9": "host.example.com"})
            self.assertEqual(r.name("203.0.113.9"), "host.example.com")

        def boom(ip):
            raise OSError("no name")
        with mock.patch.object(network.socket, "gethostbyaddr", boom):
            self.assertEqual(r.wait(["203.0.113.10"], timeout=2), {"203.0.113.10": ""})


if __name__ == "__main__":
    unittest.main()
