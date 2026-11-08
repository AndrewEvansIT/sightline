"""Memory: what's using it, grouped the way a person names things, with Quit or Stop.

Used memory follows Activity Monitor (app memory + wired + compressed) plus the firmware's startup reserve,
minus reclaimable memory (freed by apps, not yet taken back by macOS), which counts as available.
"""

import json
import os
import re
import signal
import subprocess
import threading
import time

from .. import core
from . import Widget

ABOUT = {
    "Local models": "Language models loaded by Ollama, LM Studio, llama.cpp or MLX. Stop frees their memory.",
    "Kernel and graphics": "Memory the kernel and graphics drivers keep locked in place (wired), plus what the firmware set aside "
                           "at startup. It can't be quit or paged out.",
    "Not tracked to an app": "Memory counted as used that no running program is charged for. Normally small; if it grows day "
                             "by day after a restart, something is leaking.",
    "Reclaimable": "Memory apps have finished with but macOS hasn't taken back yet. Like Free, it's available: macOS hands it "
                   "to whatever needs memory next.",
    "Free": "Memory nothing needs right now: empty pages, plus files and caches macOS keeps in case they're wanted again.",
    "macOS services": "Hundreds of small macOS helpers (Spotlight, Photos, iCloud, Siri and more). macOS starts and stops them itself.",
    "WindowServer": "Part of macOS: draws everything on your screens.",
    "Apple Intelligence": "Apple's on-device AI models. Part of macOS.",
    "Docker": "Runs containers inside a Linux virtual machine.",
    "Sightline": "This panel.",
}
CLI_NAMES = {"claude": "Claude Code", "codex": "Codex", "gemini": "Gemini CLI", "aider": "Aider", "ollama": "Ollama"}
DEV_TOOLS = {"expo": "Expo", "next": "Next.js", "vite": "Vite", "react-native": "Metro", "webpack": "Webpack",
             "nodemon": "nodemon", "astro": "Astro", "storybook": "Storybook", "nuxt": "Nuxt", "remix": "Remix"}
SYSTEM_DIRS = ("/System/", "/usr/", "/sbin/", "/bin/", "/Library/Apple/", "/private/", "/Library/Developer/PrivateFrameworks/")


def mem_counts(run=core.run):
    """(total, used, wired, reclaimable, free parts, swap used) in bytes."""
    total = int(run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    vm = run(["vm_stat"])
    page = int((re.search(r"page size of (\d+)", vm) or [0, 16384])[1])
    pages = {k.strip().strip('"'): int(v.strip(" .")) for k, v in re.findall(r"^(.+?):\s+(\d+)\.?$", vm, re.M)}
    reclaim = int(run(["sysctl", "-n", "vm.page_reusable_count"]).strip() or 0)
    wired = pages.get("Pages wired down", 0) + int(run(["sysctl", "-n", "vm.page_booter_count"]).strip() or 0)
    app = pages.get("Anonymous pages", pages.get("Pages active", 0)) - pages.get("Pages purgeable", 0) - reclaim
    used = (app + wired + pages.get("Pages occupied by compressor", 0)) * page
    free = [(pages.get("Pages free", 0) * page, "Empty"), (pages.get("File-backed pages", 0) * page, "Files kept in memory"),
            (pages.get("Pages purgeable", 0) * page, "Caches macOS can drop")]
    return total, used, wired * page, reclaim * page, free, swap_used(run)


def swap_used(run=core.run):
    m = re.search(r"used = ([\d.]+)([MG])", run(["sysctl", "-n", "vm.swapusage"]))
    if not m:
        return 0
    return float(m.group(1)) * (2**30 if m.group(2) == "G" else 2**20)


def pressure(run=core.run):
    """normal | warn | critical, from the kernel's memory-pressure level."""
    v = run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()
    return {"1": "normal", "2": "warn", "4": "critical"}.get(v, "normal")


def available_bytes(run=core.run):
    """Memory a new program can use right now without pushing anything to swap."""
    total, used = mem_counts(run)[:2]
    return total - used


def launchd_jobs():
    jobs = {}
    for line in core.run(["launchctl", "list"]).splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) == 3 and parts[0].isdigit():
            jobs[parts[0]] = parts[2]
    return jobs


def project_of(folder):
    d, home = folder, os.path.expanduser("~")
    while d not in ("/", home, ""):
        if os.path.exists(os.path.join(d, ".git")):
            return os.path.basename(d)
        d = os.path.dirname(d)
    return os.path.basename(folder)


def script_folder(cmdline):
    for token in cmdline.split():
        if not token.startswith("-") and "/node_modules/" in token:
            return token.split("/node_modules/")[0]
    return ""


def dev_roots_of(args, procs, me):
    """pid -> tool for dev servers: the user's node (or the tool itself) running node_modules/.bin/<tool>."""
    roots = {}
    for pid, a in args.items():
        _, uid, comm = procs.get(pid, ("", "", ""))
        m = re.search(r"node_modules/\.bin/([\w.-]+)", a)
        if m and uid == me and os.path.basename(comm) in ("node", m.group(1)):
            roots[pid] = m.group(1)
    return roots


def pie_parts(ranked, used, reclaim):
    """Slices in GB: the four biggest things you can stop, the rest together, macOS, untracked, reclaimable."""
    mine = [g for g in ranked if g["kind"] not in ("macos", "untracked")]
    untracked = sum(g["bytes"] for g in ranked if g["kind"] == "untracked")
    shown = untracked if untracked >= 2**29 else 0
    top = [g for g in mine[:4] if g["bytes"] > 512 * 2**20]
    others = mine[len(top):]
    parts = [{"name": g["name"], "gb": round(g["bytes"] / 2**30, 1)} for g in top]
    parts.append({"name": f"Other apps ({len(others)})", "gb": round(sum(g["bytes"] for g in others) / 2**30, 1), "other": True})
    parts.append({"name": "macOS", "gb": round(max(used - sum(g["bytes"] for g in mine) - shown, 0) / 2**30, 1), "macos": True})
    if shown:
        parts.append({"name": "Not tracked", "gb": round(shown / 2**30, 1), "untracked": True})
    parts.append({"name": "Reclaimable", "gb": round(reclaim / 2**30, 1), "reclaimable": True})
    return parts


class Memory(Widget):
    id = "memory"
    title = "Memory"
    category = "system"
    description = "What's using memory, grouped by app, with Quit and Stop. Counts reclaimable memory as available."
    interval = 15
    size = "l"
    depends_on = ["ai_runtimes"]
    settings = [{"key": "swap_warn_gb", "label": "Warn when swap passes (GB)", "type": "int", "default": 8}]
    alert_rules = [{"key": "memory-pressure", "label": "Memory is running short"},
                   {"key": "swap", "label": "A lot of memory has moved to swap"}]

    def __init__(self, app):
        super().__init__(app)
        self.groups = {}
        self.reclaim_by_pid = {}
        self.last_reclaim_scan = 0

    def scan_reclaimable(self):
        """Start the reclaimable-memory scan in the background every 2 minutes (it takes 5–20 s)."""
        if time.time() - self.last_reclaim_scan < 120 or getattr(self, "scanning", False):
            return
        self.last_reclaim_scan = time.time()
        self.scanning = True
        threading.Thread(target=self._scan_reclaimable, daemon=True, name="memory:reclaimable").start()

    def _scan_reclaimable(self):
        """Which of your processes hold reclaimable memory (footprint, no admin rights)."""
        try:
            self.reclaim_by_pid = self._read_reclaimable()
        finally:
            self.scanning = False

    def _read_reclaimable(self):
        me, found = str(os.getuid()), {}
        pids = [p[0] for p in (l.split() for l in core.run(["ps", "-axo", "pid=,uid="]).splitlines()) if len(p) == 2 and p[1] == me]
        out = core.home("footprint.json")
        for i in range(0, len(pids), 200):
            if os.path.exists(out):
                os.remove(out)
            core.run(["footprint", "-f", "bytes", "-j", out] + pids[i:i + 200], timeout=60)
            try:
                with open(out) as f:
                    procs = json.load(f).get("processes", [])
            except (OSError, ValueError):
                continue
            for p in procs:
                found[str(p["pid"])] = sum(c.get("reclaimable", 0) for c in p.get("categories", {}).values())
        return found

    def model_pids(self):
        """pid -> label for local-model processes, as the AI runtimes widget found them."""
        rt = self.data_of("ai_runtimes")
        return {str(k): v for k, v in (rt.get("pids") or {}).items()}

    def build_groups(self):
        me, mine_pid = str(os.getuid()), str(os.getpid())
        models = self.model_pids()
        fp, jobs, procs = core.footprints(), launchd_jobs(), core.processes()
        args = core.process_args()
        dev_roots = dev_roots_of(args, procs, me)
        cwds, cur = {}, None
        for line in (core.run(["lsof", "-a", "-d", "cwd", "-p", ",".join(dev_roots), "-Fpn"]) if dev_roots else "").splitlines():
            if line[:1] == "p":
                cur = line[1:]
            elif line[:1] == "n" and cur:
                cwds[cur] = line[1:]

        def climb(pid, test, limit=30):
            for _ in range(limit):
                r = test(pid)
                if r:
                    return r
                if pid not in procs or procs[pid][0] in ("0", "1", pid):
                    return None
                pid = procs[pid][0]
            return None

        def dev_server(pid):
            root = climb(pid, lambda p: p if p in dev_roots else None)
            if not root:
                return None
            tool = DEV_TOOLS.get(dev_roots[root], dev_roots[root])
            cwd = cwds.get(root) or script_folder(args.get(root, ""))
            port = re.search(r"--port[= ](\d+)", args.get(root, ""))
            where = cwd.replace(os.path.expanduser("~"), "~", 1)
            return (f"{tool} — {project_of(cwd)}" if cwd else tool,
                    f"{tool} dev server{' on port ' + port.group(1) if port else ''}{', in ' + where if where else ''}. "
                    "Stop asks it and its workers to close.", cwd)

        def owner(pid):
            def test(p):
                if p in models:
                    return ("model", None)
                if p == mine_pid:
                    return ("self", None)
                label = jobs.get(p)
                if label and not label.startswith(("application.", "com.apple.")):
                    return ("agent", label)
                return None
            return climb(pid, test) or (None, None)

        def app_of(pid):
            def test(p):
                ppid, uid, path = procs.get(p, ("1", "", ""))
                m = re.match(r"(/.*?/([^/]+)\.app)/", path)
                if m and uid == me and not m.group(1).startswith(SYSTEM_DIRS):
                    return (m.group(1), m.group(2))
                return None
            return climb(pid, test)

        groups = {}
        for pid, (ppid, uid, path) in procs.items():
            size = fp.get(pid, 0)
            if not size:
                continue
            base = os.path.basename(path)
            app = re.match(r"(/.*?/([^/]+)\.app)/", path)
            kind, label = owner(pid)
            bundle = folder = detail = None
            dev = dev_server(pid) if kind not in ("agent", "model", "self") else None
            if kind == "model":
                name = "Local models"
            elif kind == "self" or base == "Sightline":
                name, kind = "Sightline", "self"
            elif dev:
                (name, detail, folder), kind = dev, "process"
            elif "Virtualization.VirtualMachine" in path or "com.docker" in path:
                name, kind = "Docker", "app"
            elif "TGOnDevice" in path:
                name, kind = "Apple Intelligence", "macos"
            elif base == "WindowServer":
                name, kind = "WindowServer", "macos"
            elif app:
                bundle, name = app.group(1), app.group(2)
                system = uid != me or (bundle.startswith(SYSTEM_DIRS) and not bundle.startswith("/System/Applications/"))
                kind = "macos" if system else "app"
                if system:
                    name = "macOS services"
            elif uid != me or path.startswith(SYSTEM_DIRS) and not (path.startswith(("/bin/", "/usr/bin/")) and app_of(ppid)):
                name, kind = "macOS services", "macos"
            elif kind != "agent" and app_of(ppid):
                (bundle, name), kind = app_of(ppid), "app"
            else:
                name = CLI_NAMES.get(base, base)
                # Coding agents: no Stop here (it would end every session at once); quit them in their terminal.
                kind = kind if kind == "agent" else ("cli" if base in CLI_NAMES and base != "ollama" else "process")
            g = groups.setdefault(name, {"name": name, "kind": kind, "bytes": 0, "procs": [], "pids": [], "bases": {}})
            g["bytes"] += size
            g["pids"].append(pid)
            g["procs"].append((size, models.get(pid, base) if kind == "model" else base))
            g["bases"][pid] = base
            if kind == "app" and bundle and (not g.get("bundle") or bundle.startswith("/Applications/")):
                g["bundle"], g["kind"] = bundle, "app"
            if label and kind == "agent":
                g["label"] = label
            if folder:
                g["path"], g["detail"] = folder, detail
        return groups

    def collect(self):
        self.scan_reclaimable()
        total, used, wired, reclaim, free_parts, swap = mem_counts()
        groups = self.build_groups()
        for g in groups.values():
            g["reclaim"] = sum(self.reclaim_by_pid.get(pid, 0) for pid in g["pids"])
        left = used - sum(g["bytes"] for g in groups.values())
        kernel = min(max(left, 0), wired)
        if kernel > 0:
            groups["Kernel and graphics"] = {"name": "Kernel and graphics", "kind": "macos", "bytes": kernel, "procs": [], "pids": []}
        if left - kernel > 0:
            groups["Not tracked to an app"] = {"name": "Not tracked to an app", "kind": "untracked", "bytes": left - kernel,
                                               "procs": [], "pids": []}
        self.groups = groups
        ranked = sorted(groups.values(), key=lambda g: -g["bytes"])
        known = [(g.get("reclaim", 0), g["name"]) for g in ranked if g.get("reclaim")]
        unread = reclaim - sum(b for b, _ in known)
        held = sorted(known + ([(unread, "macOS helpers (need admin to see)")] if unread > 0 else []), reverse=True)
        available = [{"name": "Reclaimable", "kind": "reclaimable", "bytes": reclaim, "procs": held, "pids": []},
                     {"name": "Free", "kind": "free", "bytes": total - used - reclaim, "procs": free_parts, "pids": []}]
        level = pressure()
        used_gb = round(used / 2**30, 1)
        self.record("used_gb", used_gb)
        self.record("swap_gb", round(swap / 2**30, 2))
        alerts = []
        if level != "normal":
            alerts.append({"key": "memory-pressure", "level": "bad" if level == "critical" else "warn",
                           "text": f"Memory pressure is {level}: apps may slow down.", "short": f"Memory {level}"})
        warn_gb = self.setting("swap_warn_gb") or 8
        if swap > warn_gb * 2**30:
            alerts.append({"key": "swap", "level": "warn", "text": f"{swap / 2**30:.1f} GB in swap: the Mac is short of memory.",
                           "short": f"Swap {swap / 2**30:.0f} GB"})
        return {
            "summary": f"{used_gb:.0f} of {total / 2**30:.0f} GB used",
            "used_gb": used_gb, "total_gb": round(total / 2**30), "free_gb": round((total - used - reclaim) / 2**30, 1),
            "reclaimable_gb": round(reclaim / 2**30, 1), "available_gb": round((total - used) / 2**30, 1),
            "swap_gb": round(swap / 2**30, 2), "pressure": level,
            "parts": pie_parts(ranked, used, reclaim),
            "groups": [{"name": g["name"], "kind": g["kind"], "gb": round(g["bytes"] / 2**30, 2), "count": len(g["pids"]),
                        "about": " ".join(filter(None, [ABOUT.get(g["name"], ""), g.get("detail")])),
                        "reclaim_gb": round(g.get("reclaim", 0) / 2**30, 1),
                        "bundle": g.get("bundle"), "path": g.get("path"),
                        "procs": [{"name": n, "mb": round(b / 2**20)} for b, n in sorted(g["procs"], reverse=True)[:12]]}
                       for g in ranked + available if g["bytes"] >= 50 * 2**20],
            "alerts": alerts,
        }

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"stop": self.stop}

    def stop(self, params):
        """Quit an app (it can ask to save), stop a background agent or your own process, or stop local models."""
        name = params.get("name", "")
        g = self.groups.get(name)
        if not g:
            return {"ok": False, "message": "It isn't running any more."}
        kind = g["kind"]
        if kind == "app" and g.get("bundle"):
            subprocess.run(["osascript", "-e", f'tell application "{g["bundle"]}" to quit'], capture_output=True, timeout=15)
            return {"ok": True, "message": f"Asked {name} to quit."}
        if kind == "model":
            return self.app.action("ai_runtimes", "stop_all", {})
        if kind == "agent":
            p = subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{g['label']}"], capture_output=True, text=True)
            return {"ok": p.returncode == 0,
                    "message": f"{name} stopped until you next log in." if p.returncode == 0 else p.stderr.strip()[:200]}
        if kind == "process":
            for pid in g["pids"]:
                line = core.run(["ps", "-p", pid, "-o", "uid=,comm="]).strip().split(None, 1)
                if len(line) == 2 and line[0] == str(os.getuid()) and os.path.basename(line[1]) == g["bases"].get(pid):
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                    except OSError:
                        pass
            return {"ok": True, "message": f"Asked {name} to stop."}
        if kind == "cli":
            return {"ok": False, "message": f"End {name} sessions in their own terminal windows."}
        return {"ok": False, "message": "macOS manages this one." if kind == "macos" else "Quit it from its own window."}
