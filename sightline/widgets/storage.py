"""Storage: free space on every drive, drives that dropped out, and the last Time Machine backup.

No setup: drives are found from `mount` and labelled with `diskutil info -plist`. Disk images, simulator runtimes,
hidden system volumes (Recovery, Preboot) and read-only system snapshots are left out. Network shares are shown
and labelled as such.
"""

import datetime
import os
import plistlib
import re
import time

from .. import core
from . import Widget

WEEK = 7 * 86400
NETWORK_FS = ("smbfs", "afpfs", "nfs", "webdav", "cifs", "ftp")


def pct_state(pct):
    """Used % -> ok | warn (90%+) | bad (97%+)."""
    if pct is None:
        return "off"
    return "bad" if pct >= 97 else "warn" if pct >= 90 else "ok"


def parse_mount(out):
    """`mount` output -> [{"device", "mount", "fs", "flags"}]."""
    found = []
    for line in out.splitlines():
        m = re.match(r"^(.+?) on (.+) \(([^,)]+)(?:, ([^)]*))?\)$", line.strip())
        if m:
            flags = [f.strip() for f in (m.group(4) or "").split(",") if f.strip()]
            found.append({"device": m.group(1), "mount": m.group(2), "fs": m.group(3), "flags": flags})
    return found


def candidates(mounts):
    """The mounts worth showing: the internal Data volume and visible volumes under /Volumes."""
    out = []
    for m in mounts:
        if m["mount"] == "/System/Volumes/Data":
            out.append(dict(m, kind="internal"))
        elif m["mount"].startswith("/Volumes/"):
            if "nobrowse" in m["flags"] or "sealed" in m["flags"]:
                continue  # hidden system volumes (Recovery) and read-only system snapshots
            out.append(dict(m, kind="network" if m["fs"] in NETWORK_FS else "local"))
    return out


def classify(info, kind):
    """diskutil info -> (show it?, kind, bus) for a local mount."""
    if kind in ("internal", "network"):
        return True, kind, info.get("BusProtocol") or ""
    bus = info.get("BusProtocol") or ""
    if bus in ("Disk Image", "Virtual Interface") or "Simulator" in (info.get("VolumeName") or ""):
        return False, kind, bus
    return True, "internal" if info.get("Internal") else "external", bus


def drive_name(mount, info=None):
    """The name people see in Finder: the mount folder's name (macOS keeps it unique); the internal disk by volume name."""
    if mount == "/System/Volumes/Data":
        name = (info or {}).get("VolumeName") or "Macintosh HD"
        return re.sub(r" - Data$", "", name)
    return os.path.basename(mount.rstrip("/")) or mount


def space(mount, network=False):
    """(total, free) bytes. Network shares go through df with a timeout so a dead server can't hang the widget."""
    if network:
        out = core.run(["df", "-k", mount], timeout=5, stderr=False).splitlines()
        parts = out[-1].split() if len(out) > 1 else []
        try:
            return int(parts[1]) * 1024, int(parts[3]) * 1024
        except (IndexError, ValueError):
            return None, None
    try:
        st = os.statvfs(mount)
        return st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    except OSError:
        return None, None


def bus_words(bus):
    return {"USB": "USB", "PCI-Express": "Thunderbolt or PCIe", "Thunderbolt": "Thunderbolt", "Apple Fabric": "Built in",
            "SATA": "SATA", "Secure Digital": "SD card"}.get(bus, bus)


def update_dropouts(prev, now_names, dropouts, ejected, now=None):
    """Drives mounted at the last check and gone now become dropouts (unless Sightline ejected them).

    prev / now_names: {name: mount} of external drives. dropouts: [{"name", "at", "mount"}]. ejected: {name: time}.
    Returns the new dropout list, pruned to the last 7 days.
    """
    now = now or time.time()
    out = [d for d in dropouts if now - d.get("at", 0) < WEEK]
    for name, mount in prev.items():
        if name in now_names:
            continue
        if now - ejected.get(name, 0) < 600:
            continue  # ejected from here: a clean eject, not a dropout
        out.append({"name": name, "mount": mount, "at": now})
    return out


def parse_time_machine(raw):
    """`defaults export /Library/Preferences/com.apple.TimeMachine -` -> {"setup", "last", "auto", "dest", "used", "free"}.

    SnapshotDates are the finished backups on the destination; plist dates are UTC.
    """
    try:
        prefs = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return {"setup": False}
    if not isinstance(prefs, dict):
        return {"setup": False}
    dests = prefs.get("Destinations") or []
    if not dests:
        return {"setup": False}
    last, best = None, dests[0]
    for d in dests:
        snaps = [s for s in d.get("SnapshotDates") or [] if isinstance(s, datetime.datetime)]
        if snaps:
            ts = max(snaps).replace(tzinfo=datetime.timezone.utc).timestamp()
            if last is None or ts > last:
                last, best = ts, d
    return {"setup": True, "last": last, "auto": bool(prefs.get("AutoBackup", True)),
            "dest": best.get("LastKnownVolumeName"), "used": best.get("BytesUsed"), "free": best.get("BytesAvailable")}


def parse_latestbackup(out):
    """`tmutil latestbackup` -> timestamp (local time in the folder name), or None (it needs Full Disk Access)."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})", out or "")
    if not m:
        return None
    return time.mktime(datetime.datetime(*[int(x) for x in m.groups()]).timetuple())


def parse_tm_status(raw):
    """`tmutil status -X` -> (running, percent or None)."""
    try:
        st = plistlib.loads(raw if isinstance(raw, bytes) else raw.encode())
    except Exception:
        return False, None
    running = bool(int(st.get("Running", 0) or 0))
    pct = (st.get("Progress") or {}).get("Percent", st.get("Percent"))
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        pct = None
    return running, (round(pct * 100) if pct is not None and 0 <= pct <= 1 else None)


def backup_view(tm, running=False, percent=None, now=None):
    """-> (row or None, alert or None)."""
    if not tm.get("setup"):
        return None, None
    now = now or time.time()
    last = tm.get("last")
    hours = (now - last) / 3600 if last else None
    if running:
        value = f"Backing up now{f' ({percent}%)' if percent is not None else ''}"
    else:
        value = core.ago(last, now) if last else "No finished backup yet"
    state = "ok" if hours is not None and hours <= 24 else "warn"
    sub = f"To {tm['dest']}" if tm.get("dest") else ""
    if not tm.get("auto"):
        sub = (sub + " · " if sub else "") + "automatic backups are off"
    row = {"label": "Last Time Machine backup", "value": value, "state": state, "sub": sub}
    alert = None
    if not running and (hours is None or hours > 72):
        text = (f"The last Time Machine backup was {core.ago(last, now)}." if last
                else "Time Machine is set up but has no finished backup yet.")
        alert = {"key": "backup-old", "level": "warn", "text": text, "short": "Backup is old"}
    return row, alert


class Storage(Widget):
    id = "storage"
    title = "Storage"
    category = "system"
    description = "Free space on every drive, drives that dropped out, and when Time Machine last backed up."
    interval = 60
    size = "m"
    alert_rules = [{"key": "disk-full", "label": "A drive is almost full"},
                   {"key": "drive-gone", "label": "A drive disconnected without being ejected"},
                   {"key": "backup-old", "label": "No Time Machine backup for 3 days"}]

    def __init__(self, app):
        super().__init__(app)
        self.info_cache = {}     # (mount, st_dev) -> diskutil info
        self.externals = {}      # name -> mount, from the latest scan (what Eject may touch)
        self.ejected = {}        # name -> time ejected from here
        self.tm_latest = None    # tmutil latestbackup result
        self.tm_checked = 0

    def disk_info(self, mount):
        try:
            dev = os.stat(mount).st_dev
        except OSError:
            return {}
        key = (mount, dev)
        if key not in self.info_cache:
            raw = core.run(["diskutil", "info", "-plist", mount], timeout=15, stderr=False)
            try:
                self.info_cache[key] = plistlib.loads(raw.encode())
            except Exception:
                self.info_cache[key] = {}
        return self.info_cache[key]

    def scan(self):
        drives = []
        for m in candidates(parse_mount(core.run(["mount"], stderr=False))):
            info = {} if m["kind"] == "network" else self.disk_info(m["mount"])
            show, kind, bus = classify(info, m["kind"])
            if not show:
                continue
            total, free = space(m["mount"], network=kind == "network")
            drives.append({"name": drive_name(m["mount"], info), "mount": m["mount"], "kind": kind, "bus": bus,
                           "total": total, "free": free, "ejectable": kind == "external" and bool(info.get("Ejectable", True)),
                           "writable": info.get("WritableVolume", "read-only" not in m["flags"])})
        return drives

    def time_machine(self):
        tm = parse_time_machine(core.run(["defaults", "export", "/Library/Preferences/com.apple.TimeMachine", "-"],
                                         stderr=False))
        if not tm.get("setup"):
            return tm, False, None
        if time.time() - self.tm_checked > 600:
            self.tm_checked = time.time()
            self.tm_latest = parse_latestbackup(core.run(["tmutil", "latestbackup"], timeout=10, stderr=False))
        if self.tm_latest and (not tm.get("last") or self.tm_latest > tm["last"]):
            tm["last"] = self.tm_latest
        running, pct = parse_tm_status(core.run(["tmutil", "status", "-X"], timeout=10, stderr=False))
        return tm, running, pct

    def collect(self):
        now = time.time()
        drives = self.scan()
        self.externals = {d["name"]: d["mount"] for d in drives if d["kind"] == "external"}
        self.info_cache = {k: v for k, v in self.info_cache.items() if k[0] in {d["mount"] for d in drives}}

        prev = self.state.get("storage.externals", None)
        dropouts = self.state.get("storage.dropouts", []) or []
        if prev is not None:
            dropouts = update_dropouts(prev, self.externals, dropouts, self.ejected, now)
        else:
            dropouts = [d for d in dropouts if now - d.get("at", 0) < WEEK]
        self.state.set("storage.externals", self.externals)
        self.state.set("storage.dropouts", dropouts)

        tm, running, pct = self.time_machine()
        alerts, meters = [], []
        for d in drives:
            used = None
            if d["total"]:
                used = round(100 * (d["total"] - (d["free"] or 0)) / d["total"], 1)
            d["used_pct"] = used
            d["state"] = pct_state(used)
            d["text"] = f"{core.human_bytes(d['free'])} free of {core.human_bytes(d['total'])}" if d["total"] else "size unknown"
            where = {"internal": "Internal", "network": "Network share"}.get(d["kind"]) or bus_words(d["bus"]) or "External"
            extra = []
            if tm.get("setup") and tm.get("dest") and tm["dest"] == d["name"]:
                extra.append("Time Machine backups")
            if d.get("writable") is False:
                extra.append("read-only")
            d["sub"] = " · ".join([where] + extra)
            meters.append({"label": d["name"], "pct": used, "text": d["text"], "state": d["state"]})
            if used is not None and used >= 90:
                alerts.append({"key": f"disk-full:{d['name']}", "level": "bad" if used >= 97 else "warn",
                               "text": f"{d['name']} is {used:.0f}% full ({core.human_bytes(d['free'])} free).",
                               "short": f"{d['name']} {used:.0f}% full"})

        rows = []
        mounted = {d["name"] for d in drives}
        recent = sorted(dropouts, key=lambda x: -x["at"])
        shown_gone = set()
        for drop in recent:
            back = drop["name"] in mounted
            rows.append({"label": f"{drop['name']} disconnected", "value": core.ago(drop["at"], now),
                         "state": "ok" if back else "warn",
                         "sub": "Back now" if back else "Not ejected first. Check its cable and power."})
            if not back and drop["name"] not in shown_gone:
                shown_gone.add(drop["name"])
                alerts.append({"key": f"drive-gone:{drop['name']}", "level": "warn",
                               "text": f"{drop['name']} disconnected without being ejected. Check its cable and power.",
                               "short": f"{drop['name']} disconnected"})
        tm_row, tm_alert = backup_view(tm, running, pct, now)
        if tm_row:
            rows.insert(0, tm_row)
        if tm_alert:
            alerts.append(tm_alert)

        full = [d for d in drives if d["state"] in ("warn", "bad")]
        summary = f"{len(drives)} drive{'s' if len(drives) != 1 else ''}"
        if full:
            summary += f" · {len(full)} almost full"
        elif tm_row:
            summary += " · backing up now" if running else f" · backup {tm_row['value']}" if tm.get("last") else " · no backup yet"
        return {
            "summary": summary,
            "state": "bad" if any(d["state"] == "bad" for d in drives) else "warn" if full or shown_gone else "ok",
            "drives": [{k: d[k] for k in ("name", "mount", "kind", "used_pct", "free", "total", "text", "state", "sub", "ejectable")}
                       for d in drives],
            "dropouts": recent,
            "backup": dict(tm_row or {}, setup=bool(tm.get("setup")), last=tm.get("last"), running=running),
            "meters": meters,
            "rows": rows,
            "alerts": alerts,
        }

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"eject": self.eject}

    def eject(self, params):
        """Eject an external drive from the latest scan (diskutil eject; macOS refuses if files are open)."""
        mount = params.get("mount", "")
        name = next((n for n, m in self.externals.items() if m == mount), None)
        if not name:
            return {"ok": False, "message": "That drive isn't connected any more."}
        self.ejected[name] = time.time()
        out = core.run(["diskutil", "eject", mount], timeout=60)
        if "ejected" in out.lower() and "fail" not in out.lower():
            return {"ok": True, "message": f"{name} is ejected. You can unplug it now."}
        self.ejected.pop(name, None)
        reason = out.strip().splitlines()[-1] if out.strip() else "macOS didn't say why."
        return {"ok": False, "message": f"Couldn't eject {name}: {reason[:160]} Close any files open on it and try again."}
