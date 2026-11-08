"""Dev servers: your own processes listening on a TCP port that look like development servers.

Found with lsof (your processes only), named by tool (Vite, Next.js, …) and project (the git repo they run in).
Model runtimes (Ollama, llama.cpp, MLX, LM Studio) belong to the Local AI card; apps and macOS services are skipped.
Stop sends SIGTERM to the server and its workers, only if each is still yours and still running the same command.
"""

import os
import re
import signal

from .. import core
from . import Widget
from .agents import agent_of, children_map, cwds_of, descendants, elapsed, project_label
from .memory import DEV_TOOLS, dev_roots_of, script_folder

RUNTIMES = re.compile(r"^(node|bun|deno|python[\d.]*|Python|ruby|php|php-fpm|java|dotnet|go|beam\.smp|elixir|erl|"
                      r"uvicorn|gunicorn|hypercorn|flask|rails|puma|hugo|caddy|air|cargo-watch|wrangler|workerd)$")
MODEL_RUNTIMES = re.compile(r"ollama|llama|mlx|lm.?studio|\blms\b|vllm|kobold|localai|omlx|exo\b|jan\b|text-generation|"
                            r"whisper|comfyui|sglang|lemonade", re.I)
NOT_DEV_PATHS = ("/System/", "/usr/libexec/", "/usr/sbin/", "/Library/Apple/", "/Library/Application Support/",
                 "/Applications/", "/Library/Developer/PrivateFrameworks/")
TOOL_HINTS = [  # command-line text -> tool name, for servers not started through node_modules/.bin
    (r"manage\.py runserver", "Django"), (r"\buvicorn\b", "Uvicorn"), (r"\bgunicorn\b", "Gunicorn"),
    (r"\bflask\b", "Flask"), (r"\bstreamlit\b", "Streamlit"), (r"\bjupyter", "Jupyter"), (r"http\.server", "Python web server"),
    (r"\brails\b|\bpuma\b", "Rails"), (r"\bhugo\b", "Hugo"), (r"\bjekyll\b", "Jekyll"), (r"\bwrangler\b|workerd", "Wrangler"),
    (r"\bphp\b.* -S ", "PHP server"), (r"\bartisan serve", "Laravel"), (r"\bmix phx\.server|phoenix", "Phoenix"),
    (r"\bbun\b", "Bun"), (r"\bdeno\b", "Deno"), (r"\bnode\b", "Node"), (r"python", "Python"), (r"\bruby\b", "Ruby"),
    (r"\bjava\b", "Java"), (r"\bdotnet\b", "dotnet"), (r"/go-build", "Go"),
]
SHELLS = {"zsh", "bash", "sh", "fish", "login", "tmux", "screen", "launchd", "-zsh", "-bash"}


def parse_listeners(text):
    """lsof -F pcn output -> {pid: {"command", "ports": [..], "local_only": bool}}."""
    out, cur = {}, None
    for line in text.splitlines():
        tag, val = line[:1], line[1:]
        if tag == "p":
            cur = out.setdefault(val, {"command": "", "ports": [], "addrs": []})
        elif tag == "c" and cur is not None:
            cur["command"] = val
        elif tag == "n" and cur is not None:
            m = re.match(r"^(.*):(\d+)$", val)
            if m:
                port = int(m.group(2))
                if port not in cur["ports"]:
                    cur["ports"].append(port)
                cur["addrs"].append(m.group(1))
    for v in out.values():
        v["ports"].sort()
    return out


def looks_dev(comm, args):
    """True for a runtime or dev tool binary that isn't an app, a model runtime or part of macOS."""
    path = comm or ""
    python = re.search(r"/Python3?\.framework/", path) is not None  # Homebrew, python.org and Xcode Pythons run as Python.app
    if not python and (path.startswith(NOT_DEV_PATHS) or ".app/" in path):
        return False
    head = " ".join(os.path.basename(t) for t in (args or "").split()[:3])  # the program and its script or module
    if MODEL_RUNTIMES.search(os.path.basename(path)) or MODEL_RUNTIMES.search(head):
        return False
    if "-m sightline" in (args or ""):
        return False
    base = os.path.basename(path)
    return python or bool(RUNTIMES.match(base)) or "/go-build" in path


def tool_name(args, dev_tool=None):
    if dev_tool:
        return DEV_TOOLS.get(dev_tool, dev_tool)
    for pattern, name in TOOL_HINTS:
        if re.search(pattern, args or ""):
            return name
    return "Server"


def find_servers(listeners, procs, args, me, own_pid):
    """Group listening pids under their server root: [{root, pids (root + workers), ports, tool, dev_tool}]."""
    dev_roots = dev_roots_of(args, procs, me)

    def is_dev(pid):
        _, uid, comm = procs.get(pid, ("", "", ""))
        return uid == me and (pid in dev_roots or looks_dev(comm, args.get(pid, "")))

    def under_dev_root(pid):
        for _ in range(10):
            if pid in dev_roots:
                return True
            if pid not in procs:
                return False
            pid = procs[pid][0]
        return False

    def runner(pid):
        """Parents that are part of the server (npm run dev, a reloader), not a shell or an agent."""
        _, uid, comm = procs.get(pid, ("", "", ""))
        if uid != me or os.path.basename(comm) in SHELLS or agent_of(comm, args.get(pid, "")):
            return False
        return is_dev(pid)

    servers = {}
    for pid, info in listeners.items():
        if pid not in procs or pid == own_pid or procs[pid][1] != me or not (is_dev(pid) or under_dev_root(pid)):
            continue
        root = pid
        for _ in range(10):
            parent = procs[root][0]
            if parent in ("0", "1") or parent not in procs or not runner(parent):
                break
            root = parent
        s = servers.setdefault(root, {"root": root, "ports": [], "listeners": []})
        s["listeners"].append(pid)
        for p in info["ports"]:
            if p not in s["ports"]:
                s["ports"].append(p)
    kids = children_map(procs)
    out = []
    for root, s in servers.items():
        pids = descendants(root, kids)
        tool = next((dev_roots[p] for p in pids if p in dev_roots), None)
        if not tool:
            p = root
            for _ in range(10):  # a dev root above the listener (vite's esbuild worker, say)
                if p in dev_roots:
                    tool = dev_roots[p]
                    break
                if p not in procs:
                    break
                p = procs[p][0]
        s["ports"].sort()
        s.update(pids=pids, dev_tool=tool, tool=tool_name(args.get(root, "") + " " + " ".join(args.get(p, "") for p in s["listeners"]), tool))
        out.append(s)
    return out


class DevServers(Widget):
    id = "devservers"
    title = "Dev servers"
    category = "developer"
    description = "Your development servers listening on a port (Vite, Next.js, Django and others), with Open and Stop."
    interval = 15
    size = "m"

    def __init__(self, app):
        super().__init__(app)
        self.targets = {}  # root pid -> {pid: command line} at the last collect

    def available(self):
        return os.path.exists("/usr/sbin/lsof")

    def collect(self):
        me = str(os.getuid())
        listeners = parse_listeners(core.run(["lsof", "-nP", "-a", "-u", me, "-iTCP", "-sTCP:LISTEN", "-Fpcn"], timeout=10))
        procs, args = core.processes(), core.process_args()
        servers = find_servers(listeners, procs, args, me, str(os.getpid()))
        fp = core.footprints() if servers else {}
        ages = elapsed() if servers else {}
        cwds = cwds_of([s["root"] for s in servers])
        rows, targets = [], {}
        for s in servers:
            root = s["root"]
            cwd = cwds.get(root) or script_folder(args.get(root, ""))
            targets[root] = {p: args.get(p, "") for p in s["pids"]}
            rows.append({"pid": int(root), "tool": s["tool"], "project": project_label(cwd), "ports": s["ports"],
                         "secs": ages.get(root), "mem": int(sum(fp.get(p, 0) for p in s["pids"])), "workers": len(s["pids"]) - 1,
                         "folder": cwd.replace(os.path.expanduser("~"), "~", 1) if cwd else ""})
        self.targets = targets
        rows.sort(key=lambda r: r["ports"][0] if r["ports"] else 0)
        return {"summary": f"{len(rows)} running" if rows else "None running", "servers": rows}

    # Actions -----------------------------------------------------------------------

    def actions(self):
        return {"stop": self.stop}

    def stop(self, params):
        root = str(params.get("pid", ""))
        want = self.targets.get(root)
        if not want:
            return {"ok": False, "message": "It isn't running any more."}
        me = str(os.getuid())
        now = {}
        for line in core.run(["ps", "-o", "pid=,uid=,args=", "-p", ",".join(want)]).splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3:
                now[parts[0]] = (parts[1], parts[2])
        uid, cmd = now.get(root, ("", ""))
        if uid != me or cmd != want[root]:
            return {"ok": False, "message": "It isn't running any more."}
        sent = 0
        for pid, cmd_then in want.items():
            uid, cmd = now.get(pid, ("", ""))
            if uid == me and cmd == cmd_then:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                    sent += 1
                except OSError:
                    pass
        return {"ok": sent > 0, "message": "Asked the server to stop." if sent else "It isn't running any more."}
