"""GPU memory: how much of the Mac's memory the graphics chip may use for models (sysctl iogpu.wired_limit_mb).

Reading it needs no rights. macOS's own default (0) lets the GPU use about 75% of memory; big models on big Macs
need more. Changing it needs root, so it is opt-in: "Install helper" runs scripts/install-gpu-helper.sh through
macOS's normal password prompt. That installs
  - a root helper that accepts only this Mac's five steps (computed from its memory at install time),
  - a sudoers rule that lets you run that helper with those five values and nothing else, without a password,
  - a LaunchDaemon that re-applies your chosen step at startup (macOS resets the limit on restart).
Apple Silicon only.
"""

import os
import pwd
import re
import subprocess
import threading
import time

from .. import core
from . import Widget

KEY = "iogpu.wired_limit_mb"
HELPER = "/Library/PrivilegedHelperTools/com.sightline.gpu-limit"
SUDOERS = "/private/etc/sudoers.d/sightline-gpu-limit"
DAEMON = "/Library/LaunchDaemons/com.sightline.gpu-limit.plist"
SAVED = "/Library/Application Support/Sightline/gpu-limit-mb"
INSTALLER = os.path.join(core.ROOT, "scripts", "install-gpu-helper.sh")
MIN_LEFT_MB = 4096     # never offer a step that leaves macOS and apps less than this
MAX_LEFT_MB = 40960    # the Max step leaves this much on big Macs (8% of memory on smaller ones, at least 4 GB)


# Steps and decisions (pure; tested). install-gpu-helper.sh computes the same numbers in shell. ------------------

def max_headroom_mb(total_mb):
    return min(MAX_LEFT_MB, max(MIN_LEFT_MB, total_mb * 8 // 100))


def step_values(total_mb):
    """[(mb, name, pct)]: Normal (0, macOS's ~75%), 80%, 85%, 90%, Max (total minus headroom; never 100%).
    Steps that would leave less than 4 GB, or that aren't above the step before, are left out (small Macs)."""
    out = [(0, "Normal", 75)]
    floor = total_mb * 75 // 100
    for pct in (80, 85, 90):
        mb = total_mb * pct // 100
        if total_mb - mb >= MIN_LEFT_MB and mb > floor:
            out.append((mb, f"{pct}%", pct))
            floor = mb
    mx = total_mb - max_headroom_mb(total_mb)
    if mx > floor and total_mb - mx >= MIN_LEFT_MB:
        out.append((mx, "Max", round(mx * 100 / total_mb)))
    return out


def steps(total_mb, values=None):
    """Step rows for the page. values: the allowed values the installed helper accepts, when there is one."""
    rows = step_values(total_mb)
    if values:
        known = {mb: (name, pct) for mb, name, pct in rows}
        rows = []
        for i, mb in enumerate(sorted(values)):
            name, pct = known.get(mb) or (("Normal", 75) if mb == 0 else
                                          ("Max" if i == len(values) - 1 else f"{round(mb * 100 / total_mb)}%",
                                           round(mb * 100 / total_mb)))
            rows.append((mb, name, pct))
    return [{"mb": mb, "name": name, "pct": pct, "gb": round((mb or total_mb * 0.75) / 1024),
             "leaves_gb": round((total_mb - (mb or total_mb * 0.75)) / 1024)} for mb, name, pct in rows]


def warnings_for(mb, total_mb, models, used_gb):
    """Reasons to think twice before going above 90%: models loaded, or more memory in use than Max leaves free."""
    ninety = total_mb * 90 // 100
    if mb <= ninety:
        return []
    left_gb = round((total_mb - mb) / 1024)
    out = []
    if models:
        out.append(f"Model running: {', '.join(models)}.")
    if used_gb is not None and used_gb > left_gb:
        out.append(f"{round(used_gb)} GB is in use; this step leaves about {left_gb} GB for macOS and apps.")
    return out


def parse_allowed(text):
    """The allowed values from an installed helper (its ALLOWED="0 1 2" line), or []."""
    m = re.search(r'^ALLOWED="([\d ]+)"', text or "", re.M)
    return [int(v) for v in m.group(1).split()] if m else []


def applescript_install(installer, user):
    """The osascript line that runs the installer as root after macOS's password prompt."""
    def lit(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return (f'do shell script "/bin/sh " & quoted form of {lit(installer)} & " " & quoted form of {lit(user)} '
            f'with administrator privileges')


def read_mb(run=core.run):
    out = run(["sysctl", "-n", KEY]).strip()
    return int(out) if out.isdigit() else None


def total_mb_of(run=core.run):
    out = run(["sysctl", "-n", "hw.memsize"]).strip()
    return int(out) // 2**20 if out.isdigit() else 0


def apple_silicon(run=core.run):
    return run(["sysctl", "-n", "hw.optional.arm64"]).strip() == "1"


class GPULimit(Widget):
    id = "gpu_limit"
    title = "GPU memory"
    category = "ai"
    description = ("How much memory the graphics chip may use for models, in five steps. Changing it is optional and "
                   "needs a one-time helper install (macOS asks for your password).")
    interval = 15
    size = "s"
    depends_on = ["ai_runtimes", "memory"]

    def __init__(self, app):
        super().__init__(app)
        self.lock = threading.Lock()
        self.last = {}

    def available(self):
        return apple_silicon() and read_mb() is not None

    def allowed(self):
        try:
            with open(HELPER) as f:
                return parse_allowed(f.read(4096))
        except OSError:
            return []

    def used_gb(self):
        mem = self.data_of("memory")
        if mem.get("used_gb") is not None:
            return mem["used_gb"]
        try:
            from .memory import mem_counts
            return mem_counts()[1] / 2**30
        except Exception:
            return None

    def models(self):
        return [m.get("name") for m in self.data_of("ai_runtimes").get("models") or [] if m.get("state") == "loaded"]

    def collect(self):
        total = total_mb_of()
        mb = read_mb()
        allowed = self.allowed()
        rows = steps(total, allowed)
        step = next((s for s in rows if s["mb"] == mb), None)
        used, models = self.used_gb(), self.models()
        warn = {str(s["mb"]): w for s in rows for w in [warnings_for(s["mb"], total, models, used)] if w}
        saved = None
        try:
            with open(SAVED) as f:
                v = f.read(32).strip()
                saved = int(v) if v.isdigit() else None
        except OSError:
            pass
        gb = None if mb is None else step["gb"] if step else round(mb / 1024)
        installed = bool(allowed) and os.path.exists(SUDOERS)
        return {
            "summary": f"{gb} GB · {step['name'] if step else 'custom'}" if gb is not None else "",
            "mb": mb, "gb": gb, "step": step["name"] if step else None, "total_gb": round(total / 1024),
            "used_gb": round(used) if used is not None else None, "steps": rows, "warnings": warn,
            "installed": installed, "daemon": os.path.exists(DAEMON), "saved": saved, "last": dict(self.last),
            "note": ("" if installed else
                     "Changing it needs a small helper installed once (macOS asks for your password)."),
        }

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"install": self.install, "set": self.set}

    def install(self, params):
        if not os.path.exists(INSTALLER):
            return {"ok": False, "message": "The installer is missing from this copy of Sightline."}
        user = pwd.getpwuid(os.getuid()).pw_name
        try:
            p = subprocess.run(["osascript", "-e", applescript_install(INSTALLER, user)], capture_output=True, text=True,
                               timeout=600)
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": "The installer didn't finish in 10 minutes."}
        if p.returncode != 0:
            err = (p.stderr or p.stdout).strip()
            if "User canceled" in err or "(-128)" in err:
                return {"ok": False, "message": "Install cancelled."}
            return {"ok": False, "message": f"Install failed: {err[:200]}"}
        return {"ok": True, "message": (p.stdout.strip().splitlines() or ["GPU memory helper installed."])[-1][:200]}

    def set(self, params):
        try:
            mb = int(params.get("mb"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "That isn't one of the steps."}
        allowed = self.allowed()
        if not allowed:
            return {"ok": False, "message": "Install the helper first (Install helper button)."}
        if mb not in allowed:
            return {"ok": False, "message": "That isn't one of the steps the helper allows."}
        total = total_mb_of()
        warn = warnings_for(mb, total, self.models(), self.used_gb())
        if warn and not params.get("confirm"):
            return {"ok": False, "confirm": True, "warnings": warn, "message": " ".join(warn)}
        return self.apply(mb)

    def apply(self, mb, run=subprocess.run):
        with self.lock:
            try:
                p = run(["sudo", "-n", HELPER, str(mb)], capture_output=True, text=True, timeout=20)
                err = (p.stderr or p.stdout or "").strip()[:200]
            except subprocess.TimeoutExpired:
                p, err = None, "The helper didn't answer in 20 s."
            now = read_mb()
            ok = p is not None and p.returncode == 0 and now == mb
            gb = round((mb or total_mb_of() * 0.75) / 1024)
            if ok:
                msg = f"The GPU may now use {gb} GB. Kept after a restart."
            elif p is not None and "password" in err:
                msg = "sudo asked for a password: the helper's permission rule isn't installed. Install the helper again."
            else:
                msg = f"Didn't take: asked for {mb} MB, it reads {now} MB. {err}".strip()
            self.last = {"at": time.time(), "ok": ok, "asked": mb, "readback": now, "message": msg}
        return {"ok": ok, "message": msg}
