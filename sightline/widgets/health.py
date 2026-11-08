"""Mac health: crashes today, kernel panics this week, how much is written to the SSD, uptime, waiting updates.

All of it is readable without admin rights: crash reports in DiagnosticReports (the first line of an .ips file is a
JSON header naming the app and the kind of report), the disk driver's write counter in the I/O Registry, and the boot
time from sysctl. Lifetime SSD wear needs smartctl (Homebrew's smartmontools); without it the card says so, once.
"""

import datetime
import json
import os
import plistlib
import re
import time

from .. import core
from . import Widget

CRASH_TYPES = ("309", "109")   # app crashes (new and old report formats)
PANIC_TYPES = ("210",)          # kernel panics
REPORT_DIRS = (os.path.expanduser("~/Library/Logs/DiagnosticReports"), "/Library/Logs/DiagnosticReports")


def ips_header(path):
    """The JSON header on the first line of an .ips report, or {}."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            line = f.readline(65536)
        d = json.loads(line)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def app_from_filename(name):
    """'node-2026-11-06-163013.000.ips' -> 'node'; 'Finder_2026-11-06-1630_Mac.crash' -> 'Finder'."""
    m = re.match(r"(.+?)[-_]\d{4}-\d{2}-\d{2}", name)
    return m.group(1) if m else name.rsplit(".", 1)[0]


def scan_reports(dirs, today_start, week_start, header=ips_header):
    """-> {"crashes": {app: count today}, "panics": [timestamps in the last 7 days], "readable": [dirs read]}."""
    crashes, panics, readable = {}, [], []
    for folder in dirs:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        readable.append(folder)
        for e in entries:
            name = e.name
            if not name.endswith((".ips", ".crash", ".panic")):
                continue
            try:
                mtime = e.stat().st_mtime
            except OSError:
                continue
            if mtime < week_start:
                continue
            if name.endswith(".panic"):
                panics.append(mtime)
                continue
            if name.endswith(".crash"):
                if mtime >= today_start:
                    app = app_from_filename(name)
                    crashes[app] = crashes.get(app, 0) + 1
                continue
            h = header(e.path)
            kind = str(h.get("bug_type", ""))
            if kind in PANIC_TYPES:
                panics.append(mtime)
            elif kind in CRASH_TYPES and not h.get("is_simulated") and mtime >= today_start:
                app = h.get("app_name") or h.get("name") or app_from_filename(name)
                crashes[app] = crashes.get(app, 0) + 1
    return {"crashes": crashes, "panics": sorted(panics), "readable": readable}


def internal_disk(raw):
    """`ioreg -a -l -r -c IOBlockStorageDevice -d 3` -> {"name", "bsd", "written", "read"} for the internal SSD, or None."""
    try:
        items = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return None
    best = None
    for dev in items or []:
        proto = dev.get("Protocol Characteristics") or {}
        if proto.get("Physical Interconnect Location") != "Internal":
            continue
        if proto.get("Physical Interconnect") in ("Secure Digital", "USB", "Virtual Interface"):
            continue
        for drv in dev.get("IORegistryEntryChildren") or []:
            stats = drv.get("Statistics")
            if not isinstance(stats, dict):
                continue
            bsd = next((m.get("BSD Name") for m in drv.get("IORegistryEntryChildren") or [] if m.get("BSD Name")), None)
            disk = {"name": ((dev.get("Device Characteristics") or {}).get("Product Name") or "Internal SSD").strip(),
                    "bsd": bsd, "written": stats.get("Bytes (Write)", 0), "read": stats.get("Bytes (Read)", 0)}
            if best is None or disk["written"] > best["written"]:
                best = disk
    return best


def parse_smart(j):
    """`smartctl -a -j <disk>` JSON -> {"wear_pct", "written", "spare", "media_errors", "passed"} (NVMe fields when present)."""
    if not isinstance(j, dict):
        return None
    log = j.get("nvme_smart_health_information_log") or {}
    out = {"passed": (j.get("smart_status") or {}).get("passed")}
    if log:
        out.update(wear_pct=log.get("percentage_used"), spare=log.get("available_spare"),
                   media_errors=log.get("media_errors"),
                   written=log["data_units_written"] * 512000 if isinstance(log.get("data_units_written"), int) else None)
    if out["passed"] is None and not log:
        return None
    return out


def boot_time(out):
    m = re.search(r"sec = (\d+)", out or "")
    return int(m.group(1)) if m else None


def uptime_text(secs):
    days, rest = divmod(int(secs), 86400)
    hours, rest = divmod(rest, 3600)
    if days:
        return f"{days} day{'s' if days != 1 else ''} {hours} h"
    return f"{hours} h {rest // 60} min" if hours else f"{rest // 60} min"


def build(reports, disk, smart, has_smartctl, boot, updates, now=None):
    """Card data from what was read (pure, for tests)."""
    now = now or time.time()
    rows, alerts, notes = [], [], []

    crashes = reports.get("crashes") or {}
    total = sum(crashes.values())
    if total:
        top = sorted(crashes.items(), key=lambda kv: (-kv[1], kv[0]))
        rows.append({"label": "Crashes today", "value": f"{total} in {len(crashes)} app{'s' if len(crashes) != 1 else ''}",
                     "sub": " · ".join(f"{a} {n}" if n > 1 else a for a, n in top[:4]) + (" …" if len(top) > 4 else ""),
                     "state": "warn" if len(crashes) >= 3 else None})
        if len(crashes) >= 3:
            alerts.append({"key": "crashes", "level": "info",
                           "text": f"{len(crashes)} apps crashed today: {', '.join(a for a, _ in top[:5])}.",
                           "short": f"{len(crashes)} apps crashed today"})
    else:
        rows.append({"label": "Crashes today", "value": "None", "state": "ok"})

    if reports.get("readable"):
        panics = reports.get("panics") or []
        if panics:
            rows.append({"label": "Kernel panics this week", "value": str(len(panics)), "state": "bad",
                         "sub": f"The Mac restarted after a serious error, last {core.ago(panics[-1], now)}"})
        else:
            rows.append({"label": "Kernel panics this week", "value": "None", "state": "ok"})

    up = now - boot if boot else None
    if disk:
        written = disk["written"]
        sub = ""
        if up and up >= 3600:
            per_day = written / (up / 86400)
            sub = f"About {core.human_bytes(per_day)} a day"
        rows.append({"label": "Written to the SSD since restart", "value": core.human_bytes(written), "sub": sub})
    if smart and smart.get("wear_pct") is not None:
        bits = []
        if smart.get("written"):
            bits.append(f"{core.human_bytes(smart['written'])} written in its life")
        if smart.get("spare") is not None:
            bits.append(f"spare {smart['spare']}%")
        rows.append({"label": "SSD wear", "value": f"{smart['wear_pct']}% used", "sub": " · ".join(bits),
                     "state": "bad" if smart["wear_pct"] >= 90 else "warn" if smart["wear_pct"] >= 70 else "ok"})
    elif smart and smart.get("passed") is not None:
        rows.append({"label": "SSD self-check", "value": "Passed" if smart["passed"] else "Failed",
                     "state": "ok" if smart["passed"] else "bad"})
    if disk:
        notes.append("Downloading or converting a model writes its full size to the SSD, and running a model too big "
                     "for memory pushes data into swap, which writes too.")
    if not has_smartctl:
        notes.append("Install smartmontools to see lifetime wear.")

    if boot:
        last = datetime.datetime.fromtimestamp(boot).strftime("%a %d %b, %H:%M")
        rows.append({"label": "Up for", "value": uptime_text(up), "sub": f"Last restart {last}"})

    if updates:
        waiting = updates.get("waiting") or []
        if updates.get("restart"):
            rows.append({"label": "Restart to finish an update", "value": "Waiting", "state": "warn",
                         "sub": ", ".join(waiting[:3])})
        elif waiting:
            rows.append({"label": "Updates ready to install", "value": str(len(waiting)), "sub": ", ".join(waiting[:3])})

    summary = f"up {uptime_text(up)}" if up else ""
    if total:
        summary += f"{' · ' if summary else ''}{len(crashes)} app{'s' if len(crashes) != 1 else ''} crashed today"
    state = "bad" if any(r.get("state") == "bad" for r in rows) else "warn" if any(r.get("state") == "warn" for r in rows) else "ok"
    rows = [{k: v for k, v in r.items() if v not in (None, "")} for r in rows]
    return {"summary": summary, "state": state, "rows": rows, "note": " ".join(notes),
            "crashes": crashes, "panics": len(reports.get("panics") or []),
            "ssd": dict(disk or {}, smart=smart), "boot": boot, "alerts": alerts}


class Health(Widget):
    id = "health"
    title = "Mac health"
    category = "health"
    description = "Crashes today, kernel panics this week, how much is written to the SSD, uptime and waiting updates."
    interval = 300
    size = "m"
    alert_rules = [{"key": "crashes", "label": "Three or more apps crashed today"}]

    def collect(self):
        now = time.time()
        midnight = datetime.datetime.combine(datetime.date.today(), datetime.time()).timestamp()
        reports = scan_reports(REPORT_DIRS, midnight, now - 7 * 86400)
        disk = internal_disk(core.run(["ioreg", "-a", "-l", "-r", "-c", "IOBlockStorageDevice", "-d", "3"], stderr=False))
        smartctl = core.which("smartctl")
        smart = None
        if smartctl:
            smart = parse_smart(core.run_json([smartctl, "-a", "-j", (disk or {}).get("bsd") or "disk0"], timeout=20))
        boot = boot_time(core.run(["sysctl", "-n", "kern.boottime"]))
        updates = self.data_of("security").get("updates") if "security" in self.app.widgets else None
        data = build(reports, disk, smart, bool(smartctl), boot, updates, now)
        if disk and boot and now - boot >= 3600:
            self.record("ssd_gb_day", round(disk["written"] / 2**30 / ((now - boot) / 86400), 1))
        return data
