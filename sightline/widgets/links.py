"""Cables and ports: every USB and Thunderbolt device with the speed its link is running at.

The point is to catch a drive that silently fell back to USB 2 (480 Mb/s): a bad cable, a tired hub or the wrong
port, and model loads that took seconds take minutes. Storage is recognised from the USB mass-storage interface
class in the I/O Registry (no setup), or from the "Media" list on older macOS.
"""

import plistlib
import re

from .. import core
from . import Widget

SLOW_MBPS = 480
# Old-format (SPUSBDataType) speed codes, in Mb/s.
SPEED_CODES = {"low_speed": 1.5, "full_speed": 12, "high_speed": 480, "super_speed": 5000,
               "super_speed_plus": 10000, "super_speed_plus_by2": 20000}
# Things every USB-C hub or Mac reports that mean nothing to a person.
NOISE = ("billboard", "tps dmc", "apple internal", "apple t2", "touch bar", "ibridge")
TB_MODES = {"thunderbolt_three": "Thunderbolt 3", "thunderbolt_four": "Thunderbolt 4", "usb_four": "USB4",
            "usb_four_v2": "Thunderbolt 5 / USB4 v2"}


def speed_mbps(text):
    """'480 Mb/s', 'Up to 480 Mb/sec', '5 Gb/s', '80 Gb/s', 'high_speed' -> Mb/s (float), or None."""
    if not text:
        return None
    text = str(text)
    if text in SPEED_CODES:
        return SPEED_CODES[text]
    m = re.search(r"([\d.]+)\s*([GM])b", text)
    if not m:
        return None
    v = float(m.group(1))
    return v * 1000 if m.group(2) == "G" else v


def speed_text(mbps):
    if mbps is None:
        return ""
    if mbps >= 1000:
        g = mbps / 1000
        return f"{g:g} Gb/s"
    return f"{mbps:g} Mb/s"


def usb_generation(mbps):
    """Plain name of the USB speed class."""
    if mbps is None:
        return ""
    if mbps <= 12:
        return "USB 1 speed"
    if mbps <= 480:
        return "USB 2 speed"
    if mbps <= 5000:
        return "USB 3 speed"
    return "USB 3 fast speed" if mbps <= 20000 else "USB4 speed"


def location_int(value):
    """'0x03143000' or '0x14100000 / 5' or 51654656 -> int, or None."""
    if isinstance(value, int):
        return value
    m = re.match(r"\s*(0x[0-9a-fA-F]+)", str(value or ""))
    return int(m.group(1), 16) if m else None


def storage_locations(raw):
    """`ioreg -a -r -d 1 -c IOUSBHostInterface` plist -> set of USB locationIDs that have a mass-storage interface (class 8)."""
    try:
        items = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return set()
    if isinstance(items, dict):
        items = [items]
    return {i["locationID"] for i in items or []
            if isinstance(i, dict) and i.get("bInterfaceClass") == 8 and isinstance(i.get("locationID"), int)}


def is_noise(name):
    n = (name or "").lower()
    return any(k in n for k in NOISE)


def usb_tree(sp, storage_locs=frozenset()):
    """system_profiler SPUSBHostDataType or SPUSBDataType JSON -> buses: [{"name", "kind", "devices": [node]}].

    node: {"name", "vendor", "mbps", "location", "storage", "hub", "children": [node]}
    """
    buses = []
    entries = (sp or {}).get("SPUSBHostDataType") or (sp or {}).get("SPUSBDataType") or []

    def node(d):
        name = d.get("_name") or "Unnamed device"
        mbps = speed_mbps(d.get("USBDeviceKeyLinkSpeed") or d.get("device_speed") or d.get("speed"))
        loc = location_int(d.get("USBKeyLocationID") or d.get("location_id"))
        kids = [node(c) for c in d.get("_items") or [] if not is_noise(c.get("_name"))]
        vendor = (d.get("USBDeviceKeyVendorName") or d.get("manufacturer") or "").strip()
        storage = bool(d.get("Media")) or (loc is not None and loc in storage_locs)
        return {"name": "Unnamed device" if name == "Unnamed Device" else name, "vendor": vendor, "mbps": mbps,
                "location": loc, "storage": storage, "hub": "hub" in name.lower() or bool(kids), "children": kids}

    for bus in entries:
        devices = [node(d) for d in bus.get("_items") or [] if not is_noise(d.get("_name"))]
        if not devices:
            continue
        over_tb = (bus.get("USBKeyHardwareType") == "Thunderbolt") or "Thunderbolt" in (bus.get("_name") or "")
        buses.append({"name": bus.get("_name") or "USB bus", "kind": "thunderbolt" if over_tb else "built-in",
                      "location": location_int(bus.get("USBKeyLocationID") or bus.get("location_id")), "devices": devices})
    return buses


def has_storage(n):
    return n["storage"] or any(has_storage(c) for c in n["children"])


def usb_rows(buses):
    """-> (rows, alerts, counts). Storage at 480 Mb/s or less is bad; so is a hub that slow with storage behind it."""
    rows, alerts = [], []
    counts = {"devices": 0, "slow": 0}

    def walk(n, depth):
        counts["devices"] += 1
        slow = n["mbps"] is not None and n["mbps"] <= SLOW_MBPS
        state, sub = None, []
        if n["storage"]:
            sub.append("Storage")
            if slow:
                state = "bad"
                counts["slow"] += 1
                sub.append(f"{usb_generation(n['mbps'])}: too slow for a drive")
                alerts.append({"key": f"slow-link:{n['name']}", "level": "bad",
                               "text": f"{n['name']} is running at USB 2 speed ({speed_text(n['mbps'])}). "
                                       "Try another port or cable.",
                               "short": f"{n['name']} at USB 2 speed"})
            else:
                state = "ok"
                sub.append(usb_generation(n["mbps"]))
        elif n["hub"] and n["children"]:
            sub.append("Hub")
            if slow and any(has_storage(c) for c in n["children"]):
                state = "bad"
                sub.append("USB 2 only: slows the drive behind it")
        else:
            if n["vendor"]:
                sub.append(n["vendor"])
            if n["mbps"] is not None:
                sub.append(usb_generation(n["mbps"]))
        row = {"label": n["name"], "value": speed_text(n["mbps"]), "sub": " · ".join(s for s in sub if s), "depth": depth}
        if state:
            row["state"] = state
        rows.append(row)
        for c in n["children"]:
            walk(c, depth + 1)

    for bus in sorted(buses, key=lambda b: b["kind"] == "thunderbolt"):
        label = "USB through a Thunderbolt display or dock" if bus["kind"] == "thunderbolt" else "USB ports"
        if not any(r.get("sep") == label for r in rows):
            rows.append({"sep": label})
        for d in bus["devices"]:
            walk(d, 0)
    return rows, alerts, counts


def tb_tree(sp):
    """system_profiler SPThunderboltDataType JSON -> ports with something plugged in:
    [{"port", "speed", "devices": [{"name", "vendor", "mode", "mbps", "children"}]}]."""
    ports = []

    def link_speed(d):
        for k, v in d.items():
            if k.startswith("receptacle_upstream") and isinstance(v, dict):
                return speed_mbps(v.get("current_speed_key"))
        return None

    def node(d):
        return {"name": d.get("device_name_key") or d.get("_name") or "Thunderbolt device",
                "vendor": (d.get("vendor_name_key") or "").strip(), "mode": TB_MODES.get(d.get("mode_key"), ""),
                "mbps": link_speed(d), "children": [node(c) for c in d.get("_items") or []]}

    for bus in (sp or {}).get("SPThunderboltDataType") or []:
        devices = [node(d) for d in bus.get("_items") or []]
        if not devices:
            continue
        rec = next((v for k, v in bus.items() if k.startswith("receptacle_") and isinstance(v, dict)), {})
        ports.append({"port": rec.get("receptacle_id_key"), "speed": speed_mbps(rec.get("current_speed_key")),
                      "devices": devices})
    return ports


def tb_rows(ports):
    rows, count = [], 0

    def walk(n, depth):
        nonlocal count
        count += 1
        rows.append({"label": n["name"], "value": speed_text(n["mbps"]),
                     "sub": " · ".join(s for s in (n["vendor"], n["mode"]) if s), "depth": depth})
        for c in n["children"]:
            walk(c, depth + 1)

    for p in ports:
        rows.append({"sep": f"Thunderbolt port {p['port']}" if p.get("port") else "Thunderbolt"})
        for d in p["devices"]:
            walk(d, 0)
    return rows, count


class Links(Widget):
    id = "links"
    title = "Cables and ports"
    category = "health"
    description = "Every USB and Thunderbolt device and the speed it's connected at. Warns when a drive drops to USB 2 speed."
    interval = 300
    size = "m"
    alert_rules = [{"key": "slow-link", "label": "A drive is connected at USB 2 speed"}]

    def available(self):
        return bool(self.collect().get("count"))

    def collect(self):
        usb = core.run_json(["system_profiler", "SPUSBHostDataType", "-json"])
        if not (usb or {}).get("SPUSBHostDataType"):
            usb = core.run_json(["system_profiler", "SPUSBDataType", "-json"])
        tb = core.run_json(["system_profiler", "SPThunderboltDataType", "-json"])
        locs = storage_locations(core.run(["ioreg", "-a", "-r", "-d", "1", "-c", "IOUSBHostInterface"], stderr=False))
        return build(usb, tb, locs)


def build(usb, tb, locs):
    """Everything the card shows, from the three command outputs (pure, for tests)."""
    rows, alerts, counts = usb_rows(usb_tree(usb, locs))
    trows, tcount = tb_rows(tb_tree(tb))
    total = counts["devices"] + tcount
    if counts["slow"]:
        summary = f"{counts['slow']} drive{'s' if counts['slow'] > 1 else ''} at USB 2 speed"
    else:
        summary = f"{total} device{'s' if total != 1 else ''}"
    # Slow drives first, so the card shows the problem without scrolling; the full tree follows.
    first = []
    for r in rows:
        if r.get("state") == "bad" and r.get("sub", "").startswith("Storage"):
            first.append({"label": r["label"], "value": r["value"], "state": "bad", "depth": 0,
                          "sub": "Running at USB 2 speed. Try another port or cable."})
    if first:
        first.insert(0, {"sep": "Needs attention"})
    return {"summary": summary, "state": "bad" if counts["slow"] else "ok", "count": total,
            "rows": first + trows + rows,
            "note": "A drive should connect at 5 Gb/s or faster. 480 Mb/s means a USB 2 fallback: try another cable or port.",
            "alerts": alerts}
