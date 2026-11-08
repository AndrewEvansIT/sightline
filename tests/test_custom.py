"""Script tiles: output parsing, the time limit, per-tile intervals, and never running a tile twice at once."""

import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline.widgets import custom  # noqa: E402


class FakeConfig:
    def __init__(self, tiles):
        self.tiles = tiles

    def get(self, key, default=None):
        return self.tiles if key == "custom" else default


class FakeApp:
    def __init__(self, tiles):
        self.config = FakeConfig(tiles)
        self.pokes = 0

    def poke(self, wid):
        self.pokes += 1


class ParseTest(unittest.TestCase):
    def test_json_becomes_generic_data(self):
        kind, d = custom.parse_output('{"summary": "ok", "big": [{"label": "Jobs", "value": 3}], "alerts": [1], "x": 2}')
        self.assertEqual(kind, "data")
        self.assertEqual(d, {"summary": "ok", "big": [{"label": "Jobs", "value": 3}]})

    def test_buttons_are_dropped(self):
        _, d = custom.parse_output('{"rows": [{"label": "a", "actions": [{"id": "stop", "widget": "memory"}]}]}')
        self.assertEqual(d["rows"], [{"label": "a"}])

    def test_text_is_cut(self):
        kind, t = custom.parse_output("x" * 5000)
        self.assertEqual((kind, len(t)), ("text", 2000))
        self.assertEqual(custom.parse_output("[1, 2]"), ("text", "[1, 2]"))
        self.assertEqual(custom.parse_output("{not json"), ("text", "{not json"))


class RunTest(unittest.TestCase):
    def test_text_and_json(self):
        self.assertEqual(custom.run_command("echo hello"), {"ok": True, "text": "hello"})
        r = custom.run_command("""echo '{"summary": "fine"}'""")
        self.assertEqual(r, {"ok": True, "data": {"summary": "fine"}})

    def test_failure_shows_last_error_line(self):
        r = custom.run_command("echo partial; echo first >&2; echo 'it broke' >&2; exit 3")
        self.assertFalse(r["ok"])
        self.assertEqual((r["text"], r["error"]), ("partial", "it broke"))
        self.assertEqual(custom.run_command("exit 4")["error"], "Exited with code 4.")

    def test_timeout_stops_the_whole_group(self):
        marker = tempfile.mktemp(prefix="sightline-tile-")
        started = time.time()
        r = custom.run_command(f"(sleep 3; touch {marker}) & sleep 5", timeout=1)
        self.assertFalse(r["ok"])
        self.assertIn("longer than 1 second", r["error"])
        self.assertLess(time.time() - started, 4)
        time.sleep(3)
        self.assertFalse(os.path.exists(marker))  # the background child was stopped too


class WidgetTest(unittest.TestCase):
    def test_available_needs_an_enabled_tile(self):
        self.assertFalse(custom.Custom(FakeApp([])).available())
        self.assertFalse(custom.Custom(FakeApp([{"title": "a", "command": "echo", "enabled": False}])).available())
        self.assertFalse(custom.Custom(FakeApp([{"title": "a", "command": "  "}])).available())
        self.assertTrue(custom.Custom(FakeApp([{"title": "a", "command": "echo hi"}])).available())

    def test_collect_runs_due_tiles_once_per_interval(self):
        app = FakeApp([{"title": "Hello", "command": "echo hi", "interval": 60},
                       {"title": "Off", "command": "echo no", "enabled": False}])
        w = custom.Custom(app)
        d = w.collect()
        self.assertEqual(len(d["tiles"]), 1)
        t = d["tiles"][0]
        self.assertEqual((t["title"], t["ok"], t["text"]), ("Hello", True, "hi"))
        first = t["ran"]
        d = w.collect()  # not due again for 60 s
        self.assertEqual(d["tiles"][0]["ran"], first)
        w.last[custom.tile_key(app.config.tiles[0])] -= 61
        d = w.collect()
        self.assertGreater(d["tiles"][0]["ran"], first)

    def test_never_parallel_with_itself(self):
        app = FakeApp([{"title": "Slow", "command": "sleep 2; echo done", "interval": 5}])
        w = custom.Custom(app)
        calls = []
        real = custom.run_command

        def counting(cmd, *a, **k):
            calls.append(cmd)
            return real(cmd, *a, **k)

        custom.run_command = counting
        try:
            d = w.collect()
            self.assertTrue(d["tiles"][0]["running"])
            w.last[custom.tile_key(app.config.tiles[0])] -= 100  # due again, but still running
            w.collect()
            self.assertEqual(len(calls), 1)
            deadline = time.time() + 5
            while w.busy and time.time() < deadline:
                time.sleep(0.1)
            d = w.collect()
            self.assertEqual(d["tiles"][0]["text"], "done")
            self.assertGreaterEqual(app.pokes, 1)
        finally:
            custom.run_command = real

    def test_removed_tiles_are_forgotten(self):
        app = FakeApp([{"title": "A", "command": "echo a"}])
        w = custom.Custom(app)
        w.collect()
        app.config.tiles = [{"title": "B", "command": "echo b"}]
        d = w.collect()
        self.assertEqual([t["title"] for t in d["tiles"]], ["B"])
        self.assertEqual(len(w.results), 1)


if __name__ == "__main__":
    unittest.main()
