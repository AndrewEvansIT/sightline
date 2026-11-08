import os
import unittest
from unittest import mock

from sightline.widgets import power as pw

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name, mode="r"):
    with open(os.path.join(FIX, name), mode) as f:
        return f.read()


class Parsers(unittest.TestCase):
    def test_parse_power(self):
        d = pw.parse_power(fixture("ai_power.json"))
        self.assertEqual(d["system_w"], 187.4)
        self.assertEqual(d["cpu_w"], 6.42)
        frozen = pw.parse_power(fixture("ai_power_frozen.json"))
        self.assertIsNone(frozen["cpu_w"])
        self.assertAlmostEqual(frozen["gpu_w"], 0.46)
        self.assertIsNone(pw.parse_power(fixture("ai_power_error.json")))
        self.assertIsNone(pw.parse_power(""))
        self.assertIsNone(pw.parse_power("not json"))
        self.assertIsNone(pw.parse_power('{"cpu_w": null, "gpu_w": null, "system_w": null, "total_w": null}'))

    def test_headline(self):
        self.assertEqual(pw.headline({"system_w": 40, "total_w": 10}), (40, "Whole Mac"))
        self.assertEqual(pw.headline({"system_w": 40, "source": "wall"}), (40, "Whole Mac (from the wall)"))
        self.assertEqual(pw.headline({"system_w": None, "total_w": 10}), (10, "Chip"))
        self.assertEqual(pw.headline({"gpu_w": 3}), (3, "Graphics"))

    def test_fmt(self):
        self.assertEqual(pw.fmt_w(187.4), 187)
        self.assertEqual(pw.fmt_w(3.46), 3.5)
        self.assertIsNone(pw.fmt_w(None))

    def test_parse_wall(self):
        w, counter = pw.parse_wall(fixture("ai_power_ioreg_wall.plist", "rb"))
        self.assertEqual(w, 39.6)
        self.assertEqual(counter, (2675219610, 63065))
        w, counter = pw.parse_wall(fixture("ai_power_ioreg_zero.plist", "rb"))
        self.assertIsNone(w)
        self.assertEqual(pw.parse_wall(""), (None, None))
        self.assertEqual(pw.parse_wall("<plist>broken"), (None, None))


class FakeApp:
    class config:
        @staticmethod
        def widget(wid):
            return {}

    def __init__(self):
        self.recorded = []

    class history:
        @staticmethod
        def add(key, value):
            pass


class Widget(unittest.TestCase):
    def test_collect_frozen_counters(self):
        w = pw.Power(FakeApp())
        with mock.patch.object(pw.core, "helper", lambda n: "/fake/power"), \
                mock.patch.object(pw.core, "run", lambda cmd, **kw: fixture("ai_power_frozen.json")):
            d = w.collect()
        self.assertEqual(d["label"], "Whole Mac")
        self.assertEqual(d["big"][0]["value"], 37)
        self.assertIn("processor", d["note"])
        self.assertIsNone(d["cpu_w"])

    def test_wall_fallback_without_helper(self):
        w = pw.Power(FakeApp())
        with mock.patch.object(pw.core, "helper", lambda n: None), \
                mock.patch.object(pw.core, "run", lambda cmd, **kw: fixture("ai_power_ioreg_wall.plist")):
            d = w.collect()
            self.assertEqual(d["label"], "Whole Mac (from the wall)")
            self.assertEqual(d["system_w"], 39.6)
            # The same counters again: the telemetry isn't updating, so no number rather than an old one.
            self.assertFalse(w.available())

    def test_nothing_reads(self):
        w = pw.Power(FakeApp())
        with mock.patch.object(pw.core, "helper", lambda n: None), \
                mock.patch.object(pw.core, "run", lambda cmd, **kw: fixture("ai_power_ioreg_zero.plist")):
            self.assertFalse(w.available())


if __name__ == "__main__":
    unittest.main()
