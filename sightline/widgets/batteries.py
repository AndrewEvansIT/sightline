"""Batteries: wireless mice, keyboards, trackpads and headphones, with no setup.

Two sources: the I/O Registry (every accessory that reports BatteryPercent, read every minute) and
system_profiler's Bluetooth report (AirPods and Beats left, right and case; read every 5 minutes).
Phones, watches and other computers are left out.
"""

import plistlib
import re
import time

from .. import core
from . import Widget

LOW = 15
SKIP_TYPES = {"phone", "smartphone", "cellular", "watch", "computer", "desktop", "laptop", "tablet", "ipad"}


def norm_addr(a):
    """'10-94-bb-a7-5e-43' or '10:94:BB:A7:5E:43' -> '1094bba75e43'."""
    return re.sub(r"[^0-9a-f]", "", (a or "").lower())


def pct_of(v):
    """'85%' or 85 -> 85, else None."""
    if isinstance(v, int):
        return v
    m = re.match(r"\s*(\d+)\s*%?", str(v or ""))
    return int(m.group(1)) if m else None


def parse_ioreg(raw):
    """`ioreg -a -r -d 1 -k BatteryPercent` -> [{"name", "pct", "addr", "kind"}] (external accessories only)."""
    try:
        items = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return []
    if isinstance(items, dict):
        items = [items]
    out, seen = [], set()
    for d in items or []:
        if not isinstance(d, dict) or d.get("Built-In") or d.get("BatteryPercent") is None:
            continue
        addr = norm_addr(d.get("DeviceAddress") or "")
        key = addr or (d.get("Product"), d.get("SerialNumber"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": (d.get("Product") or "").strip(), "pct": int(d["BatteryPercent"]), "addr": addr,
                    "kind": d.get("Accessory Category") or ""})
    return out


def parse_bluetooth(sp):
    """system_profiler SPBluetoothDataType -json -> {"names": {addr: name}, "devices": [battery devices]}.

    Only connected devices. A device with left/right/case levels (AirPods, Beats) gets "parts".
    """
    names, devices = {}, []
    for ctrl in (sp or {}).get("SPBluetoothDataType") or []:
        # macOS 13+: "device_connected"; older: everything under "device_title" with device_isconnected.
        for entry in ctrl.get("device_connected") or ctrl.get("device_title") or []:
            for name, d in (entry or {}).items():
                if not isinstance(d, dict) or d.get("device_isconnected", "attrib_Yes") != "attrib_Yes":
                    continue
                addr = norm_addr(d.get("device_address"))
                if addr:
                    names[addr] = name
                kind = (d.get("device_minorType") or "").lower()
                if kind in SKIP_TYPES:  # exact: "Headphones" contains "phone"
                    continue
                parts = [(label, pct_of(d.get(key))) for label, key in
                         (("Left", "device_batteryLevelLeft"), ("Right", "device_batteryLevelRight"),
                          ("Case", "device_batteryLevelCase"))]
                parts = [{"label": l, "pct": p} for l, p in parts if p is not None]
                main = pct_of(d.get("device_batteryLevelMain"))
                if parts:
                    buds = [p["pct"] for p in parts if p["label"] != "Case"]
                    devices.append({"name": name, "addr": addr, "pct": min(buds) if buds else None, "parts": parts,
                                    "kind": d.get("device_minorType") or ""})
                elif main is not None:
                    devices.append({"name": name, "addr": addr, "pct": main, "kind": d.get("device_minorType") or ""})
    return {"names": names, "devices": devices}


def parse_accps(out):
    """`pmset -g accps` -> {name: (pct, charging)} for accessories (not the Mac's own battery)."""
    found = {}
    for line in out.splitlines():
        m = re.match(r"\s*-(.+?) \(id=\d+\)\s+(\d+)%;\s*([^;]*)", line)
        if m and "InternalBattery" not in m.group(1):
            name = m.group(1).replace("\ufffd", "\u2019")  # pmset prints curly apostrophes as bad bytes
            how = m.group(3).split(" present")[0].strip()
            found[name] = (int(m.group(2)), how == "charging")
    return found


def ascii_key(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def merge(io_devs, bt, accps):
    """One list: I/O Registry readings first (freshest), Bluetooth for what it alone reports. Names filled in."""
    names = bt.get("names", {})
    charging = {ascii_key(n): c for n, (_, c) in accps.items()}
    out, addrs = [], set()
    for d in io_devs:
        name = d["name"] or names.get(d["addr"])
        if not name:
            # pmset names accessories too; use one if exactly one reading has this level.
            same = [n for n, (p, _) in accps.items() if p == d["pct"]]
            name = same[0] if len(same) == 1 else f"{d['kind'] or 'Wireless'} accessory".strip()
        out.append({"name": name, "pct": d["pct"], "kind": d["kind"], "charging": charging.get(ascii_key(name), False)})
        if d["addr"]:
            addrs.add(d["addr"])
    for d in bt.get("devices", []):
        if d["addr"] and d["addr"] in addrs:
            continue
        item = {"name": d["name"], "pct": d["pct"], "kind": d.get("kind", ""), "charging": False}
        if d.get("parts"):
            item["parts"] = d["parts"]
        out.append(item)
    return out


def view(devices):
    """Card data and alerts."""
    alerts = []
    for d in devices:
        p = d.get("pct")
        d["state"] = "off" if p is None else "bad" if p <= LOW else "warn" if p <= 30 else "ok"
        if p is not None and p <= LOW and not d.get("charging"):
            alerts.append({"key": f"battery-low:{d['name']}", "level": "bad" if p <= 5 else "warn",
                           "text": f"{d['name']} battery is at {p}%. Charge it soon.", "short": f"{d['name']} {p}%"})
    lowest = min((d for d in devices if d.get("pct") is not None), key=lambda d: d["pct"], default=None)
    summary = f"lowest {lowest['pct']}%" if lowest and len(devices) > 1 else (f"{lowest['pct']}%" if lowest else "")
    rows = []
    for d in devices:
        if d.get("parts"):
            value = " · ".join(f"{p['label'][0]} {p['pct']}%" for p in d["parts"])
        else:
            value = f"{d['pct']}%" if d.get("pct") is not None else "–"
        rows.append({"label": d["name"], "value": value, "state": d["state"], "sub": "Charging" if d.get("charging") else ""})
    return {"summary": summary, "devices": devices, "rows": rows, "alerts": alerts}


class Batteries(Widget):
    id = "batteries"
    title = "Batteries"
    category = "system"
    description = "Battery levels of wireless mice, keyboards, trackpads and AirPods. No setup needed."
    interval = 60
    size = "s"
    alert_rules = [{"key": "battery-low", "label": "An accessory's battery is low"}]

    def __init__(self, app):
        super().__init__(app)
        self.bt = {"names": {}, "devices": []}
        self.bt_at = 0

    def bluetooth(self):
        if time.time() - self.bt_at > 300:
            self.bt_at = time.time()
            sp = core.run_json(["system_profiler", "SPBluetoothDataType", "-json"], timeout=40)
            if sp is not None:
                self.bt = parse_bluetooth(sp)
        return self.bt

    def read(self):
        io_devs = parse_ioreg(core.run(["ioreg", "-a", "-r", "-d", "1", "-k", "BatteryPercent"], stderr=False))
        accps = parse_accps(core.run(["pmset", "-g", "accps"], stderr=False))
        return merge(io_devs, self.bluetooth(), accps)

    def available(self):
        return bool(self.read())

    def collect(self):
        return view(self.read())
