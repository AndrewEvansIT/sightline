"""Mac battery: charge, health, cycles and the charger, for laptops. Hidden on desktop Macs.

Everything comes from the I/O Registry's AppleSmartBattery entry (`ioreg -r -c AppleSmartBattery -a`), no admin rights.
Health is the battery's present full capacity against its design capacity, the way System Settings counts it.
"""

import plistlib

from .. import core
from . import Widget

NO_TIME = 65535  # what the battery reports while it's still estimating


def signed(v):
    """ioreg writes negative currents as unsigned 64-bit numbers."""
    if isinstance(v, int) and v >= 2**63:
        return v - 2**64
    return v


def read_battery(raw):
    """`ioreg -r -c AppleSmartBattery -a` -> the battery's properties, or None when there's no battery."""
    try:
        items = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return None
    if isinstance(items, dict):
        items = [items]
    for d in items or []:
        if isinstance(d, dict) and d.get("BatteryInstalled", True) and (d.get("DesignCapacity") or d.get("CurrentCapacity") is not None):
            return d
    return None


def parse_battery(d):
    """AppleSmartBattery properties -> plain numbers (pure, for tests)."""
    maxcap = d.get("MaxCapacity") or 100
    if maxcap <= 100:
        pct = d.get("CurrentCapacity")  # Apple silicon reports percent here
    else:
        pct = round(100 * d.get("CurrentCapacity", 0) / maxcap)
    design = d.get("DesignCapacity") or (d.get("BatteryData") or {}).get("DesignCapacity")
    full = d.get("AppleRawMaxCapacity") or d.get("NominalChargeCapacity")
    health = round(100 * full / design) if design and full else None
    charging, plugged, full_charge = bool(d.get("IsCharging")), bool(d.get("ExternalConnected")), bool(d.get("FullyCharged"))
    to_full = d.get("AvgTimeToFull") if charging else None
    to_empty = d.get("AvgTimeToEmpty") if not plugged else None
    if to_full is not None and to_full >= NO_TIME:
        to_full = None
    if to_empty is not None and to_empty >= NO_TIME:
        to_empty = None
    temp = d.get("Temperature")
    temp_c = round(temp / 100, 1) if isinstance(temp, int) and temp > 0 else None  # hundredths of a degree C
    volts, amps = d.get("Voltage"), signed(d.get("Amperage"))
    battery_w = round(volts * amps / 1e6, 1) if isinstance(volts, int) and isinstance(amps, int) else None
    adapter = d.get("AdapterDetails") or {}
    telemetry = d.get("PowerTelemetryData") or {}
    power_in = telemetry.get("SystemPowerIn")
    failed = bool(d.get("PermanentFailureStatus"))
    if failed or (health is not None and health < 80):
        condition = "Service recommended"
    else:
        condition = "Normal"
    return {
        "pct": pct, "charging": charging, "plugged": plugged, "full": full_charge,
        "to_full_min": to_full, "to_empty_min": to_empty,
        "cycles": d.get("CycleCount"), "design_cycles": d.get("DesignCycleCount9C"),
        "health": health, "condition": condition, "temp_c": temp_c,
        "battery_w": battery_w,
        "power_in_w": round(power_in / 1000, 1) if plugged and isinstance(power_in, int) and power_in > 0 else None,
        "charger": {"watts": adapter.get("Watts"), "name": adapter.get("Name") or adapter.get("Description") or "",
                    "wireless": bool(adapter.get("IsWireless"))} if plugged and adapter else None,
    }


def minutes(m):
    if m is None:
        return ""
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def view(b):
    """Generic card data and alerts from parse_battery()."""
    if b["charging"]:
        status = f"Charging{', full in ' + minutes(b['to_full_min']) if b['to_full_min'] else ''}"
    elif b["plugged"] and b["full"]:
        status = "Charged"
    elif b["plugged"]:
        status = "Plugged in, not charging (macOS may be holding the charge to protect the battery)"
    else:
        status = f"On battery{', ' + minutes(b['to_empty_min']) + ' left' if b['to_empty_min'] else ''}"
    pct = b["pct"]
    state = "bad" if pct is not None and pct <= 10 and not b["plugged"] else "warn" if pct is not None and pct <= 20 and not b["plugged"] else "ok"
    hstate = None if b["health"] is None else "ok" if b["health"] >= 80 else "warn"
    big = [{"label": "Charge", "value": pct, "unit": "%", "state": state}]
    if b["health"] is not None:
        big.append({"label": "Health", "value": b["health"], "unit": "%", "state": hstate})
    rows = [{"label": status, "value": ""}]
    if b["charger"]:
        name = b["charger"]["name"] or "Charger"
        watts = f"{b['charger']['watts']} W" if b["charger"].get("watts") else ""
        rows.append({"label": "Charger", "value": watts, "sub": name})
    if b["power_in_w"] is not None:
        rows.append({"label": "Drawing from the charger now", "value": f"{b['power_in_w']:g} W"})
    if b["battery_w"] and b["charging"] and b["battery_w"] > 0:
        rows.append({"label": "Going into the battery", "value": f"{b['battery_w']:g} W"})
    elif b["battery_w"] and b["battery_w"] < 0:
        rows.append({"label": "Using from the battery", "value": f"{-b['battery_w']:g} W"})
    if b["cycles"] is not None:
        rows.append({"label": "Charge cycles", "value": b["cycles"],
                     "sub": f"Rated for about {b['design_cycles']}" if b.get("design_cycles") else ""})
    rows.append({"label": "Condition", "value": b["condition"], "state": "ok" if b["condition"] == "Normal" else "warn"})
    if b["temp_c"] is not None:
        rows.append({"label": "Battery temperature", "value": f"{b['temp_c']:.0f}°C",
                     "state": "warn" if b["temp_c"] >= 40 else None})
    rows = [{k: v for k, v in r.items() if v is not None} for r in rows]
    alerts = []
    if b["health"] is not None and b["health"] < 80:
        alerts.append({"key": "battery-health", "level": "warn",
                       "text": f"The battery holds {b['health']}% of its original charge. Apple suggests a service below 80%.",
                       "short": f"Battery health {b['health']}%"})
    summary = f"{pct}%" + (" · charging" if b["charging"] else " · on battery" if not b["plugged"] else "")
    return {"summary": summary, "state": state, "big": big, "rows": rows, "battery": b, "alerts": alerts}


class MacBattery(Widget):
    id = "mac_battery"
    title = "Mac battery"
    category = "health"
    description = "This Mac's own battery: charge, health, charge cycles and the charger. Laptops only."
    interval = 60
    size = "s"
    alert_rules = [{"key": "battery-health", "label": "The battery has worn below 80%"}]

    def read(self):
        return read_battery(core.run(["ioreg", "-r", "-c", "AppleSmartBattery", "-a"], stderr=False))

    def available(self):
        return self.read() is not None

    def collect(self):
        d = self.read()
        if d is None:
            return {"summary": "No battery", "state": "off"}
        b = parse_battery(d)
        self.record("pct", b["pct"])
        return view(b)
