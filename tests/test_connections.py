import os
import tempfile
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import connections

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    return slurp(os.path.join(FIX, name))


def slurp(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def procs():
    out = {}
    for line in load("connections_ps.txt").splitlines():
        p = line.split(None, 3)
        out[p[0]] = (p[1], p[2], p[3])
    return out


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def widget(self, wid):
        return dict(self.values.get(wid, {}))


class FakeApp:
    def __init__(self, tmp, values=None):
        self.config = FakeConfig(values)
        self.state = core.State(os.path.join(tmp, "state.json"))


class FakeResolver:
    def __init__(self, names):
        self.names = names

    def name(self, ip):
        return self.names.get(ip, "")


class Parsers(unittest.TestCase):
    def test_parse_lsof(self):
        rows = connections.parse_lsof(load("connections_lsof_established.txt"))
        self.assertTrue(rows)
        first = rows[0]
        self.assertEqual((first["pid"], first["command"], first["proto"], first["state"]), ("34775", "claude", "TCP", "ESTABLISHED"))
        self.assertIn("->", first["name"])
        udp = connections.parse_lsof(load("connections_lsof_udp.txt"))
        self.assertIn("fitnessintelligenced", {r["command"] for r in udp})  # full names, not lsof's 9-character column

    def test_split_addr(self):
        self.assertEqual(connections.split_addr("[2001:db8::1]:443"), ("2001:db8::1", "443"))
        self.assertEqual(connections.split_addr("192.168.1.23:62039"), ("192.168.1.23", "62039"))
        self.assertEqual(connections.split_addr("*:5000"), ("*", "5000"))
        self.assertEqual(connections.split_addr("*:*"), ("*", "*"))

    def test_addr_kind(self):
        self.assertEqual(connections.addr_kind("127.0.0.1"), "loopback")
        self.assertEqual(connections.addr_kind("::1"), "loopback")
        self.assertEqual(connections.addr_kind("fe80:22::a:1"), "link-local")   # lsof's embedded-scope form
        self.assertEqual(connections.addr_kind("169.254.3.4"), "link-local")
        self.assertEqual(connections.addr_kind("224.0.0.251"), "multicast")
        self.assertEqual(connections.addr_kind("192.168.1.50"), "lan")
        self.assertEqual(connections.addr_kind("203.0.113.1"), "internet")      # documentation range counts as internet here
        self.assertEqual(connections.addr_kind("2001:db8:ff::1"), "internet")
        self.assertEqual(connections.addr_kind("::ffff:10.0.0.2"), "lan")
        self.assertEqual(connections.addr_kind("*"), "unknown")

    def test_short_domain(self):
        self.assertEqual(connections.short_domain("lax17s55-in-f14.1e100.net"), ("1e100.net", "Google"))
        self.assertEqual(connections.short_domain("ec2-203-0-113-1.compute-1.amazonaws.com."), ("amazonaws.com", "Amazon Web Services"))
        self.assertEqual(connections.short_domain("www.example.co.uk"), ("example.co.uk", ""))
        self.assertEqual(connections.short_domain("localhost"), ("localhost", ""))

    def test_app_of(self):
        p = procs()
        self.assertEqual(connections.app_of("80057", p, "501"), ("Google Chrome", "app"))      # helper inside the bundle
        self.assertEqual(connections.app_of("80031", p, "501"), ("Claude", "app"))
        self.assertEqual(connections.app_of("34775", p, "501"), ("claude", "app"))
        self.assertEqual(connections.app_of("23546", p, "501"), ("Docker", "app"))
        self.assertEqual(connections.app_of("71448", p, "501"), ("Control Center (AirPlay Receiver)", "macos"))
        self.assertEqual(connections.app_of("74052", p, "501"), ("Handoff and Continuity", "macos"))
        self.assertEqual(connections.app_of("71463", p, "501"), ("iMessage and FaceTime", "macos"))
        self.assertEqual(connections.app_of("97448", p, "501"), ("iOS Simulator", "macos"))
        self.assertEqual(connections.app_of("99999", p, "501"), (None, "process"))
        p["5"] = ("1", "501", "/opt/homebrew/bin/ollama")
        self.assertEqual(connections.app_of("5", p, "501"), ("ollama", "process"))

    def test_talkers(self):
        rows = connections.parse_lsof(load("connections_lsof_established.txt")) + connections.parse_lsof(load("connections_lsof_udp.txt"))
        conns = connections.connections(rows)
        kinds = {c[3] for c in conns}
        self.assertEqual(kinds, {"internet"})  # loopback and link-local (rapportd, identityservicesd) skipped
        talkers = connections.group_talkers(conns, procs(), "501", {"203.0.113.1": "edge-1.example-ai.com"})
        names = [t["name"] for t in talkers]
        self.assertEqual(names[0], "claude")
        self.assertIn("Google Chrome", names)
        self.assertNotIn("rapportd", names)
        top = talkers[0]
        self.assertEqual(top["count"], sum(h["count"] for h in top["hosts"]) if top["host_count"] <= 6 else top["count"])
        self.assertIn("example-ai.com", [h["host"] for h in top["hosts"]])

    def test_lan_talker(self):
        rows = connections.parse_lsof("p1\ncnode\nu501\nf3\nPTCP\nn192.168.1.23:5000->192.168.1.40:8080\nTST=ESTABLISHED\n")
        t = connections.group_talkers(connections.connections(rows), {"1": ("1", "501", "/usr/local/bin/node")}, "501")
        self.assertEqual(t[0]["hosts"][0], {"host": "192.168.1.40", "owner": "local network", "count": 1, "local": True,
                                            "ips": ["192.168.1.40"], "names": []})

    def test_listeners(self):
        rows = connections.parse_lsof(load("connections_lsof_listen.txt")) + connections.parse_lsof(load("connections_lsof_udp.txt"))
        found = connections.listeners(rows)
        self.assertFalse(any(port == "*" for _, _, port, _ in found))   # UDP *:* isn't bound to a port
        apps = {a["name"]: a for a in connections.group_listeners(found, procs(), "501")}
        self.assertTrue(apps["Docker"]["exposed"])
        self.assertEqual(len(apps["Docker"]["ports"]), 19)
        cc = apps["Control Center (AirPlay Receiver)"]
        self.assertEqual([p["port"] for p in cc["ports"]], [5000, 7000])               # IPv4 and IPv6 merged
        self.assertFalse(apps["Example Panel"]["exposed"])
        self.assertEqual(apps["Example Panel"]["ports"][0]["where"], "this Mac only")
        self.assertIn("Python", apps)                                                   # not in ps: falls back to lsof's name
        ordered = list(apps)
        self.assertLess(ordered.index("Docker"), ordered.index("Example Panel"))       # exposed first

    def test_listener_on_lan_address(self):
        rows = connections.parse_lsof("p2\ncnc\nu501\nf3\nPTCP\nn192.168.1.23:9000\nTST=LISTEN\n")
        apps = connections.group_listeners(connections.listeners(rows), {}, "501")
        self.assertEqual(apps[0]["ports"][0], {"proto": "TCP", "port": 9000, "exposed": True, "where": "192.168.1.23"})


def fake_run(listen="connections_lsof_listen.txt"):
    def run(cmd, timeout=20, env=None, stderr=True):
        if cmd[0] == "lsof":
            if "-sTCP:ESTABLISHED" in cmd:
                return load("connections_lsof_established.txt")
            if "-sTCP:LISTEN" in cmd:
                return load(listen) if not os.path.isabs(listen) else slurp(listen)
            return load("connections_lsof_udp.txt")
        if cmd[0] == "ps":
            return load("connections_ps.txt")
        return ""
    return run


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self, w, listen="connections_lsof_listen.txt"):
        with mock.patch.object(core, "run", fake_run(listen)), \
                mock.patch.object(connections, "resolver", lambda: FakeResolver({"203.0.113.1": "edge.example.com"})):
            return w.collect()

    def test_collect_and_exposed_alerts(self):
        app = FakeApp(self.tmp.name, {"connections": {"alert_exposed": True}})
        w = connections.Connections(app)
        d = self.collect(w)
        self.assertEqual(d["alerts"], [])                     # first run is the baseline
        self.assertEqual(d["talkers"][0]["name"], "claude")
        self.assertGreater(d["exposed_count"], 0)
        self.assertIn("own apps only", d["note"])
        extra = os.path.join(self.tmp.name, "listen.txt")
        with open(extra, "w") as f:
            f.write(load("connections_lsof_listen.txt") + "p4242\ncnc\nu501\nf3\nPTCP\nn*:8888\nTST=LISTEN\n")
        d = self.collect(w, extra)
        self.assertEqual([a["key"] for a in d["alerts"]], ["exposed-port:nc:8888"])
        self.assertEqual(d["alerts"][0]["level"], "info")
        nc = next(a for a in d["listening"] if a["name"] == "nc")
        self.assertTrue(nc["ports"][0]["new"])

    def test_no_alerts_when_setting_off(self):
        app = FakeApp(self.tmp.name)
        w = connections.Connections(app)
        self.collect(w)
        extra = os.path.join(self.tmp.name, "listen.txt")
        with open(extra, "w") as f:
            f.write("p4242\ncnc\nu501\nf3\nPTCP\nn*:8888\nTST=LISTEN\n")
        d = self.collect(w, extra)
        self.assertEqual(d["alerts"], [])
        self.assertTrue(d["listening"][0]["ports"][0]["new"])


if __name__ == "__main__":
    unittest.main()
