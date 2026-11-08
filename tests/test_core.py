"""Core: config merge, alerts, history, profiles, and the HTTP server's rules."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline import server  # noqa: E402
from sightline.alerts import Alerts  # noqa: E402
from sightline.app import App  # noqa: E402
from sightline.config import Config, deep_merge  # noqa: E402
from sightline.core import State, ago, human_bytes  # noqa: E402
from sightline.history import History  # noqa: E402


def temp(name):
    return os.path.join(tempfile.mkdtemp(prefix="sightline-test-"), name)


class ConfigTest(unittest.TestCase):
    def test_defaults_without_file(self):
        c = Config(temp("config.json"))
        self.assertEqual(c.get("port"), 47800)
        self.assertEqual(c.get("layout"), "window")

    def test_merge_keeps_nested_and_replaces_lists(self):
        merged = deep_merge({"a": {"x": 1, "y": 2}, "order": [1, 2]}, {"a": {"y": 3}, "order": [9]})
        self.assertEqual(merged, {"a": {"x": 1, "y": 3}, "order": [9]})

    def test_update_saves(self):
        path = temp("config.json")
        c = Config(path)
        c.set_widget("memory", enabled=False)
        self.assertFalse(Config(path).widget("memory")["enabled"])


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.alerts = Alerts(Config(temp("config.json")))
        self.alerts._deliver = lambda title, text, level: self.sent.append((text, level))

    def test_new_alert_notifies_once(self):
        a = [{"key": "disk-full:Internal", "level": "warn", "text": "Full"}]
        self.alerts.update("storage", a, now=1000)
        self.alerts.update("storage", a, now=1005)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.alerts.list()), 1)

    def test_cleared_then_back_within_cooldown_is_quiet(self):
        a = [{"key": "k:1", "level": "warn", "text": "x"}]
        self.alerts.update("w", a, now=1000)
        self.alerts.update("w", [], now=1010)
        self.assertEqual(self.alerts.list(), [])
        self.alerts.update("w", a, now=1020)
        self.assertEqual(len(self.sent), 1)
        self.alerts.update("w", [], now=1030)
        self.alerts.update("w", a, now=1000 + 31 * 60)
        self.assertEqual(len(self.sent), 2)

    def test_worse_level_notifies_again(self):
        self.alerts.update("w", [{"key": "k", "level": "warn", "text": "x"}], now=1000)
        self.alerts.update("w", [{"key": "k", "level": "bad", "text": "x"}], now=1001)
        self.assertEqual([lvl for _, lvl in self.sent], ["warn", "bad"])

    def test_rule_switched_off(self):
        self.alerts.config.update({"alerts": {"rules": {"disk-full": False}}})
        self.alerts.update("storage", [{"key": "disk-full:X", "level": "bad", "text": "x"}], now=1)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.alerts.list()), 1)  # still shown on the panel

    def test_app_outbox(self):
        alerts = Alerts(Config(temp("config.json")))
        alerts.take()  # the app is listening
        alerts.update("w", [{"key": "k", "level": "warn", "text": "hello"}])
        self.assertEqual(alerts.take()[0]["text"], "hello")


class HistoryTest(unittest.TestCase):
    def test_minute_averages_and_series(self):
        h = History(temp("h.db"))
        base = 1_700_000_000 - (1_700_000_000 % 60)
        for i, v in enumerate([10, 20, 30]):
            h.add("cpu", v, now=base + i)
        h.add("cpu", 50, now=base + 61)  # next minute flushes the first
        rows = h.db.execute("SELECT ts, value FROM samples WHERE key='cpu'").fetchall()
        self.assertEqual(rows, [(base, 20.0)])
        self.assertEqual(h.recent_values("cpu"), [10, 20, 30, 50])

    def test_runs(self):
        h = History(temp("h.db"))
        h.add_run("speed", "m1", {"gen_tps": 42})
        self.assertEqual(h.runs("speed")[0]["gen_tps"], 42)


class SmallThingsTest(unittest.TestCase):
    def test_ago(self):
        self.assertEqual(ago(100, now=130), "just now")
        self.assertEqual(ago(0), "never")
        self.assertEqual(ago(100, now=100 + 3 * 3600), "3 h ago")

    def test_human_bytes(self):
        self.assertEqual(human_bytes(1536), "2 KB")
        self.assertEqual(human_bytes(5 * 2**30), "5.0 GB")

    def test_state_roundtrip(self):
        path = temp("state.json")
        State(path).set("x", {"a": 1})
        self.assertEqual(State(path).get("x"), {"a": 1})


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        home = tempfile.mkdtemp(prefix="sightline-test-")
        cls.app = App(config=Config(os.path.join(home, "config.json")), state=State(os.path.join(home, "state.json")),
                      history=History(os.path.join(home, "h.db")), start=False)
        cls.app.config.data["port"] = 0
        cls.httpd = server.serve(cls.app, port=0)
        cls.port = cls.httpd.server_address[1]
        cls.app.config.data["port"] = cls.port
        cls.httpd.RequestHandlerClass = server.make_handler(cls.app)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return r.status, r.read()

    def post(self, path, body, origin=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_page_and_status(self):
        code, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"Sightline", body)
        status = json.loads(self.get("/api/status")[1])
        self.assertIn("system", status["widgets"])
        self.assertIn("memory", status["order"])

    def test_static_cannot_escape(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.get("/static/../sightline/core.py")
        self.assertEqual(e.exception.code, 404)

    def test_config_post_and_port_is_protected(self):
        res = self.post("/api/config", {"changes": {"theme": "light", "port": 1}})
        self.assertEqual(res["config"]["theme"], "light")
        self.assertEqual(self.app.config.get("port"), self.port)

    def test_foreign_origin_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post("/api/config", {"changes": {"theme": "dark"}}, origin="https://evil.example")
        self.assertEqual(e.exception.code, 403)

    def test_unknown_action(self):
        res = self.post("/api/action", {"widget": "nope", "action": "x"})
        self.assertFalse(res["ok"])

    def test_profile_roundtrip_turns_tiles_off(self):
        self.app.config.update({"custom": [{"title": "t", "command": "echo hi", "enabled": True}]})
        prof = json.loads(self.get("/api/profile")[1])
        self.assertEqual(prof["sightline_profile"], 1)
        self.assertNotIn("lan", prof["settings"])
        res = self.post("/api/profile", {"profile": prof})
        self.assertTrue(res["ok"])
        self.assertFalse(self.app.config.get("custom")[0]["enabled"])

    def test_bad_profile(self):
        self.assertFalse(self.post("/api/profile", {"profile": {"hello": 1}})["ok"])


class LanTokenTest(unittest.TestCase):
    def test_token_required_off_loopback(self):
        app = App(config=Config(temp("config.json")), state=State(temp("state.json")), history=History(temp("h.db")), start=False)
        app.config.update({"lan": {"enabled": True, "token": "s3cret"}})
        handler = server.make_handler(app)

        class Fake(handler):
            def __init__(self, ip):  # no socket: just the bits allowed() needs
                self.client_address = (ip, 1)
        self.assertTrue(Fake("127.0.0.1").allowed({}))
        self.assertFalse(Fake("192.168.1.20").allowed({}))
        self.assertFalse(Fake("192.168.1.20").allowed({"token": ["nope"]}))
        self.assertTrue(Fake("192.168.1.20").allowed({"token": ["s3cret"]}))
        app.config.update({"lan": {"enabled": False}})
        self.assertFalse(Fake("192.168.1.20").allowed({"token": ["s3cret"]}))


if __name__ == "__main__":
    unittest.main()
