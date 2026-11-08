"""Shared helpers: running commands, small HTTP calls, persistent state, formatting.

Stdlib only, Python 3.9+ (the Python that ships with the Command Line Tools on macOS).
"""

import json
import os
import socket
import subprocess
import threading
import time
import urllib.request

HOME = os.environ.get("SIGHTLINE_HOME") or os.path.expanduser("~/Library/Application Support/Sightline")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repo, or the app's Resources folder


def helper(name):
    """Path of a compiled helper (helpers/build/<name>, built by helpers/build.sh), or None."""
    p = os.path.join(ROOT, "helpers", "build", name)
    return p if os.access(p, os.X_OK) else None


def home(*parts):
    """A path inside Sightline's data folder, created on first use."""
    os.makedirs(HOME, exist_ok=True)
    return os.path.join(HOME, *parts)


def run(cmd, timeout=20, env=None, stderr=True):
    """Run a command and return its output as text ("" on any failure). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        out = p.stdout + (p.stderr if stderr else b"")
        return out.decode("utf-8", errors="replace")
    except Exception:
        return ""


def run_json(cmd, timeout=40):
    """Run a command that prints JSON; None if it fails or prints something else."""
    out = run(cmd, timeout=timeout, stderr=False)
    try:
        return json.loads(out)
    except ValueError:
        return None


def which(name, extra=()):
    """Path of a command, also looking in Homebrew and app-bundled locations launchd's PATH misses."""
    dirs = os.environ.get("PATH", "").split(":") + ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                                                     "/usr/sbin", "/sbin", os.path.expanduser("~/.local/bin")] + list(extra)
    for d in dirs:
        p = os.path.join(d, name)
        if d and os.access(p, os.X_OK):
            return p
    return None


def port_open(port, host="127.0.0.1", timeout=0.4):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(url, data=None, timeout=3, method=None, headers=None):
    """GET (or POST when data is given) a URL and decode JSON. None on any failure."""
    try:
        body = json.dumps(data).encode() if data is not None else None
        h = {"Content-Type": "application/json"} if body is not None else {}
        h.update(headers or {})
        req = urllib.request.Request(url, data=body, headers=h, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "null")
    except Exception:
        return None


def ago(ts, now=None):
    """'just now', '5 min ago', '3 h ago', '2 days ago'."""
    if not ts:
        return "never"
    secs = (now or time.time()) - ts
    mins = int(secs / 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins} min ago"
    if mins < 48 * 60:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} days ago"


def gb(n_bytes, digits=1):
    return round((n_bytes or 0) / 2**30, digits)


def human_bytes(n):
    """1.2 GB, 340 MB, 12 KB."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


_cache = {}
_cache_lock = threading.Lock()


def cached(seconds):
    """Share one result for a few seconds between widgets that ask at the same time (ps, top)."""
    def wrap(fn):
        def inner():
            with _cache_lock:
                hit = _cache.get(fn.__name__)
                if hit and time.time() - hit[0] < seconds:
                    return dict(hit[1])
            value = fn()
            with _cache_lock:
                _cache[fn.__name__] = (time.time(), value)
            return dict(value)
        inner.__name__ = fn.__name__
        inner.__doc__ = fn.__doc__
        return inner
    return wrap


@cached(3)
def footprints():
    """pid -> memory in bytes as Activity Monitor counts it (top's MEM column; rss misses model weights)."""
    import re
    unit = {"B": 1, "K": 2**10, "M": 2**20, "G": 2**30}
    out = run(["top", "-l", "1", "-stats", "pid,mem"])
    return {m.group(1): float(m.group(2)) * unit[m.group(3)]
            for m in re.finditer(r"^\s*(\d+)\s+([\d.]+)([BKMG])[+-]?\s*$", out, re.M)}


@cached(2)
def processes():
    """pid -> (ppid, uid, command path) for every process. comm is last, so it isn't cut short."""
    procs = {}
    for line in run(["ps", "-axo", "pid=,ppid=,uid=,comm="]).splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            procs[parts[0]] = (parts[1], parts[2], parts[3])
    return procs


@cached(2)
def process_args():
    """pid -> full command line."""
    out = {}
    for line in run(["ps", "-axwwo", "pid=,args="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2:
            out[parts[0]] = parts[1]
    return out


class State:
    """Small persistent key/value store (state.json): known devices, baselines, dropouts."""

    def __init__(self, path=None):
        self.path = path or home("state.json")
        self.lock = threading.Lock()
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def get(self, key, default=None):
        with self.lock:
            v = self.data.get(key, default)
            return json.loads(json.dumps(v)) if v is not None else default

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f)
            os.replace(tmp, self.path)
