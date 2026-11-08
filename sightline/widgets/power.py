"""Power: how many watts the Mac draws right now, for the whole computer and for the graphics chip.

Read by the small helper helpers/power (no admin rights), which uses two unofficial Apple interfaces: IOReport's
energy counters (the ones Activity Monitor and powermetrics read) and the SMC's power sensor. A macOS update can
change them. Without the helper, the whole Mac's draw from the wall is read from macOS's own power telemetry
(ioreg AppleSmartBattery, which desktop Macs report too) when it is updating. When nothing reads, the card hides.
A part whose counter isn't updating shows "–" rather than a made-up number.
"""

import json
import plistlib

from .. import core
from . import Widget


def parse_wall(xml):
    """(watts, counter) from `ioreg -r -c AppleSmartBattery -a`: the power coming in from the wall (SystemPowerIn,
    mW), which desktop Macs report too. watts is None when it reads 0. counter changes while the value is live."""
    try:
        items = plistlib.loads(xml if isinstance(xml, bytes) else (xml or "").encode())
    except Exception:
        return None, None
    for item in items if isinstance(items, list) else [items]:
        t = (item or {}).get("PowerTelemetryData") or {}
        mw = t.get("SystemPowerIn")
        if mw is None:
            continue
        counter = (t.get("AccumulatedSystemPowerIn"), t.get("SystemPowerInAccumulatorCount"))
        return (round(mw / 1000, 1) if mw and mw > 0 else None), counter
    return None, None


def parse_power(out):
    """The helper's JSON, or None when it printed nothing useful (no readings, an error, not JSON)."""
    try:
        d = json.loads(out or "")
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("error"):
        return None
    if d.get("system_w") is None and d.get("gpu_w") is None and d.get("total_w") is None:
        return None
    return d


def headline(d):
    """(watts, label) for the big number: the whole Mac when the SMC sensor reads, else the chip parts."""
    if d.get("system_w") is not None:
        return d["system_w"], "Whole Mac (from the wall)" if d.get("source") == "wall" else "Whole Mac"
    if d.get("total_w") is not None:
        return d["total_w"], "Chip"
    return d.get("gpu_w"), "Graphics"


def fmt_w(w):
    if w is None:
        return None
    return round(w) if w >= 10 else round(w, 1)


class Power(Widget):
    id = "power"
    title = "Power"
    category = "system"
    description = ("Watts the Mac is drawing now, for the whole computer and its graphics, processor and memory. "
                   "Uses an unofficial Apple interface (no admin rights), so a macOS update may hide it.")
    interval = 5
    size = "s"

    def __init__(self, app):
        super().__init__(app)
        self.wall_counter = None

    def wall(self):
        """The whole Mac's draw from the wall, from the official power telemetry; None when it isn't updating."""
        out = core.run(["ioreg", "-r", "-c", "AppleSmartBattery", "-a"], timeout=5, stderr=False)
        watts, counter = parse_wall(out)
        stale = counter is not None and counter == self.wall_counter
        self.wall_counter = counter
        return None if stale else watts

    def read(self):
        """The helper's reading, or (without the helper) the wall reading alone."""
        helper = core.helper("power")
        d = parse_power(core.run([helper], timeout=5, stderr=False)) if helper else None
        if d is None:
            w = self.wall()
            if w is not None:
                d = {"system_w": w, "gpu_w": None, "cpu_w": None, "ane_w": None, "dram_w": None, "total_w": None,
                     "source": "wall"}
        return d

    def available(self):
        return self.read() is not None

    def collect(self):
        d = self.read()
        if d is None:
            return {"summary": "no reading", "state": "off", "note": "The power helper couldn't read this Mac's sensors just now."}
        watts, label = headline(d)
        for key in ("system_w", "gpu_w", "cpu_w", "ane_w", "dram_w"):
            self.record(key.replace("_w", ""), d.get(key))
        big = [{"label": label, "value": fmt_w(watts), "unit": "W"}]
        if d.get("source") == "wall":
            return {"summary": f"{fmt_w(watts)} W", "watts": watts, "label": label, "system_w": watts, "big": big,
                    "note": "From macOS's power telemetry. Chip parts need Sightline's power helper."}
        if label != "Graphics":
            big.append({"label": "Graphics", "value": fmt_w(d.get("gpu_w")), "unit": "W"})
        if d.get("cpu_w") is not None:
            big.append({"label": "Processor", "value": fmt_w(d.get("cpu_w")), "unit": "W"})
        unknown = [n for n, k in (("processor", "cpu_w"), ("Neural Engine", "ane_w"), ("memory", "dram_w")) if d.get(k) is None]
        return {
            "summary": f"{fmt_w(watts)} W" if watts is not None else "–",
            "watts": watts, "label": label,
            "system_w": d.get("system_w"), "gpu_w": d.get("gpu_w"), "cpu_w": d.get("cpu_w"), "ane_w": d.get("ane_w"),
            "dram_w": d.get("dram_w"), "total_w": d.get("total_w"),
            "big": big,
            "note": (f"macOS isn't updating the {' and '.join([', '.join(unknown[:-1]), unknown[-1]] if len(unknown) > 1 else unknown)} "
                     f"energy counters on this Mac, so those show –."
                     if unknown else ""),
        }
