"""Security: the Mac's built-in protections, software updates waiting, and a watch on what starts automatically.

Everything here is read without admin rights. Login items: the folders where apps (and malware) put
programs that start on their own. The first look records what's there; after that, anything new is flagged.
"""

import os
import plistlib
import re
import threading
import time

from .. import core
from . import Widget

FIREWALL = "/usr/libexec/ApplicationFirewall/socketfilterfw"
UPDATE_PREFS = "/Library/Preferences/com.apple.SoftwareUpdate.plist"
APPSTORE_PREFS = "/Library/Preferences/com.apple.commerce.plist"
LAUNCH_DIRS = [("~/Library/LaunchAgents", "Starts when you log in"),
               ("/Library/LaunchAgents", "Starts when anyone logs in"),
               ("/Library/LaunchDaemons", "Runs in the background for the whole Mac")]
UPDATES_EVERY = 6 * 3600


# Parsers (pure) ---------------------------------------------------------------------

def parse_firewall(state_out, stealth_out=""):
    """{"on": bool or None, "stealth": bool or None}."""
    on = None
    if re.search(r"enabled|State = [12]", state_out or ""):
        on = True
    elif re.search(r"disabled|State = 0", state_out or ""):
        on = False
    stealth = None
    if "stealth mode is on" in (stealth_out or "") or "stealth mode enabled" in (stealth_out or ""):
        stealth = True
    elif "stealth mode is off" in (stealth_out or "") or "stealth mode disabled" in (stealth_out or ""):
        stealth = False
    return {"on": on, "stealth": stealth}


def parse_on_off(out, on_words, off_words):
    out = out or ""
    if any(w in out for w in on_words):
        return True
    if any(w in out for w in off_words):
        return False
    return None


def parse_filevault(out):
    if "FileVault is On" in (out or ""):
        return True
    if "FileVault is Off" in (out or ""):
        return False
    return None


def parse_gatekeeper(out):
    return parse_on_off(out, ["assessments enabled"], ["assessments disabled"])


def parse_sip(out):
    return parse_on_off(out, ["status: enabled"], ["status: disabled"])


def parse_softwareupdate(out):
    """{"waiting": [titles], "restart": bool} from `softwareupdate -l`, or None if it didn't finish."""
    out = out or ""
    if "No new software available" in out:
        return {"waiting": [], "restart": False}
    titles, restart = [], False
    for label, detail in re.findall(r"^\* Label: (.+)\n\s*(.*)$", out, re.M):
        title = re.search(r"Title: (.+?), Version: ([^,]+)", detail)
        name = f"{title.group(1)}" if title else label.strip()
        if title and title.group(2).strip() not in name:
            name += f" {title.group(2).strip()}"
        titles.append(name)
        if re.search(r"Action: restart|\[restart\]", detail):
            restart = True
    if titles:
        return {"waiting": titles, "restart": restart}
    return None


def auto_updates(prefs, store=None):
    """What automatic updates do, from SoftwareUpdate prefs (a dict). Missing keys mean macOS's default (on)."""
    if prefs is None:
        return None
    get = lambda k: bool(prefs.get(k, True))
    out = {"check": get("AutomaticCheckEnabled"), "download": get("AutomaticDownload"),
           "install_macos": bool(prefs.get("AutomaticallyInstallMacOSUpdates", False)),
           "security": get("CriticalUpdateInstall") and get("ConfigDataInstall")}
    if store is not None:
        out["app_store"] = bool(store.get("AutoUpdate", False))
    return out


def auto_updates_text(a):
    if not a:
        return "", "off"
    if not a["check"]:
        return "Off: macOS doesn't check for updates", "warn"
    if a["install_macos"] and a["security"]:
        return "On: installs macOS updates and security fixes", "ok"
    if a["security"]:
        return "Security fixes install by themselves; macOS updates wait for you", "ok"
    return "Checks only: security fixes don't install by themselves", "warn"


def recommended_updates(prefs):
    """Updates macOS already knows are waiting (from its last background check), as display names."""
    out = []
    for u in (prefs or {}).get("RecommendedUpdates") or []:
        if isinstance(u, dict):
            name = u.get("Display Name") or u.get("Identifier") or ""
            ver = u.get("Display Version") or ""
            out.append(f"{name} {ver}".strip() if ver and ver not in name else name)
    return [n for n in out if n]


def read_plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return None


def launch_item(path, where):
    """{"path", "label", "program", "where", "missing"} for one launchd plist (unreadable ones still listed)."""
    p = read_plist(path) or {}
    label = p.get("Label") if isinstance(p.get("Label"), str) else os.path.basename(path)[:-6]
    program = p.get("Program") or ""
    args = p.get("ProgramArguments") or []
    if not program and isinstance(args, list) and args:
        program = str(args[0])
    cmd = " ".join(str(a) for a in args) if isinstance(args, list) and args else program
    missing = bool(program and program.startswith("/") and not os.path.exists(program))
    return {"path": path, "label": label, "program": program, "command": cmd[:300], "where": where, "missing": missing,
            "disabled": bool(p.get("Disabled"))}


def scan_launch_items(dirs=None):
    items = []
    for folder, where in dirs or LAUNCH_DIRS:
        folder = os.path.expanduser(folder)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for n in names:
            if n.endswith(".plist") and not n.startswith("."):
                items.append(launch_item(os.path.join(folder, n), where))
    return items


def compare_baseline(items, baseline, now):
    """Returns (baseline, new_items). A missing baseline records everything present (no alerts on day one).
    Removed files leave the baseline, so if they come back they're flagged again."""
    if baseline is None:
        return {i["path"]: {"label": i["label"], "since": now} for i in items}, []
    present = {i["path"] for i in items}
    baseline = {p: v for p, v in baseline.items() if p in present}
    new = [i for i in items if i["path"] not in baseline]
    return baseline, new


# The widget -----------------------------------------------------------------------

class Security(Widget):
    id = "security"
    title = "Security"
    category = "security"
    description = "Firewall, FileVault, Gatekeeper, System Integrity Protection, updates waiting, and new programs set to start automatically."
    interval = 600
    delay = 3
    size = "m"
    alert_rules = [{"key": "new-login-item", "label": "Something new was set to start automatically"}]

    def __init__(self, app):
        super().__init__(app)
        self.updates = self.state.get("security.updates")
        self.updates_busy = False

    def _check_updates(self):
        try:
            res = parse_softwareupdate(core.run(["softwareupdate", "-l"], timeout=300))
            if res is not None:
                res["checked"] = time.time()
                self.updates = res
                self.state.set("security.updates", res)
        finally:
            self.updates_busy = False
            self.app.poke(self.id)

    def maybe_check_updates(self):
        if self.updates_busy or not core.which("softwareupdate"):
            return
        if self.updates and time.time() - self.updates.get("checked", 0) < UPDATES_EVERY:
            return
        self.updates_busy = True
        threading.Thread(target=self._check_updates, daemon=True, name="security:updates").start()

    def collect(self):
        now = time.time()
        fw = parse_firewall(core.run([FIREWALL, "--getglobalstate"], timeout=5),
                            core.run([FIREWALL, "--getstealthmode"], timeout=5)) if os.path.exists(FIREWALL) else {"on": None, "stealth": None}
        filevault = parse_filevault(core.run(["fdesetup", "status"], timeout=10))
        gatekeeper = parse_gatekeeper(core.run(["spctl", "--status"], timeout=5))
        sip = parse_sip(core.run(["csrutil", "status"], timeout=5))
        prefs = read_plist(UPDATE_PREFS)
        auto = auto_updates(prefs, read_plist(APPSTORE_PREFS))

        self.maybe_check_updates()
        updates = dict(self.updates) if self.updates else None
        if updates is None:
            known = recommended_updates(prefs)
            updates = {"waiting": known, "restart": False, "checked": None, "pending": True}

        items = scan_launch_items()
        baseline, new = compare_baseline(items, self.state.get("security.login_items"), now)
        self.state.set("security.login_items", baseline)
        new_paths = {i["path"] for i in new}
        for i in items:
            i["new"] = i["path"] in new_paths

        checks = [("Firewall", fw["on"]), ("FileVault", filevault), ("Gatekeeper", gatekeeper),
                  ("System Integrity Protection", sip)]
        alerts = []
        for i in new:
            alerts.append({"key": f"new-login-item:{i['label']}", "level": "warn",
                           "text": f"{i['label']} was added to start automatically", "short": f"New login item: {i['label']}"})
        off = [n for n, on in checks if on is False]
        waiting = len(updates.get("waiting") or [])
        bits = []
        if off:
            bits.append(f"{len(off)} off")
        if waiting:
            bits.append(f"{waiting} update{'s' if waiting != 1 else ''}")
        if new:
            bits.append(f"{len(new)} new login item{'s' if len(new) != 1 else ''}")
        auto_text, auto_state = auto_updates_text(auto)
        return {
            "summary": " · ".join(bits) or "All protections on",
            "state": "warn" if (off or new) else "ok",
            "firewall": fw, "filevault": filevault, "gatekeeper": gatekeeper, "sip": sip,
            "auto_updates": auto, "auto_updates_text": auto_text, "auto_updates_state": auto_state,
            "updates": updates, "updates_running": self.updates_busy,
            "login_items": items, "new_login_items": len(new),
            "alerts": alerts,
        }

    # Actions -----------------------------------------------------------------------

    def actions(self):
        return {"accept": self.accept, "check_updates": self.check_updates}

    def accept(self, params):
        path = str(params.get("path") or "")
        items = {i["path"]: i for i in scan_launch_items()}
        if path not in items:
            return {"ok": False, "message": "That file isn't there any more."}
        baseline = self.state.get("security.login_items") or {}
        baseline[path] = {"label": items[path]["label"], "since": time.time()}
        self.state.set("security.login_items", baseline)
        return {"ok": True, "message": f"{items[path]['label']} is now on your known list."}

    def check_updates(self, params):
        if self.updates_busy:
            return {"ok": True, "message": "Already checking for updates."}
        self.updates = None
        self.maybe_check_updates()
        return {"ok": True, "message": "Checking for updates. It can take a minute or two."}
