"""Script tiles: your own commands, each shown as a small section of this card.

Tiles live in config.json under "custom": [{"title", "command", "interval", "enabled"}]. Each runs through
/bin/zsh -lc on its own interval, with a 10 second limit, and never twice at once. If the output is JSON it is drawn
like any widget's data (summary, big, meters, rows, note); otherwise the first 2,000 characters are shown as text.
Tiles imported from someone else's profile arrive switched off.
"""

import json
import os
import signal
import subprocess
import threading
import time

from . import Widget

TIMEOUT = 10
MIN_INTERVAL = 5
TEXT_LIMIT = 2000
GENERIC_KEYS = ("summary", "state", "big", "meters", "rows", "note", "text")


def clean_data(d):
    """Keep only the generic display keys, and drop buttons: a script's output can't trigger actions."""
    out = {k: d[k] for k in GENERIC_KEYS if k in d}
    if isinstance(out.get("rows"), list):
        out["rows"] = [{k: v for k, v in r.items() if k != "actions"} if isinstance(r, dict) else r for r in out["rows"]]
    return out


def parse_output(stdout):
    """('data', dict) when the output is a JSON object, else ('text', first 2,000 characters)."""
    s = (stdout or "").strip()
    if s.startswith("{"):
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return "data", clean_data(d)
        except ValueError:
            pass
    return "text", s[:TEXT_LIMIT]


def run_command(command, timeout=TIMEOUT, shell="/bin/zsh"):
    """Run a command in a login shell. Returns {"ok", "data" or "text", "error"}. Stops the whole group on timeout."""
    try:
        p = subprocess.Popen([shell, "-lc", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        return {"ok": False, "error": f"Couldn't start: {e}"}
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(p.pid, sig)
            except OSError:
                pass
            try:
                p.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                continue
        return {"ok": False, "error": f"Took longer than {timeout} seconds, so it was stopped."}
    kind, value = parse_output(out.decode("utf-8", "replace"))
    result = {"ok": p.returncode == 0, kind: value}
    if p.returncode != 0:
        lines = [l for l in err.decode("utf-8", "replace").strip().splitlines() if l.strip()]
        result["error"] = (lines[-1][:300] if lines else f"Exited with code {p.returncode}.")
    return result


def tile_key(t):
    return f"{t.get('title', '')}\x00{t.get('command', '')}"


class Custom(Widget):
    id = "custom"
    title = "Script tiles"
    category = "custom"
    description = "Your own commands, each shown as a tile. Add them in Settings; output can be plain text or JSON."
    interval = 10
    size = "m"

    def __init__(self, app):
        super().__init__(app)
        self.lock = threading.Lock()
        self.results = {}   # tile key -> last result (with "ran")
        self.last = {}      # tile key -> when it last started
        self.busy = set()   # tile keys running now

    def tiles(self):
        tiles = self.app.config.get("custom", []) or []
        return [t for t in tiles if isinstance(t, dict) and (t.get("command") or "").strip() and t.get("enabled", True)]

    def available(self):
        return bool(self.tiles())

    def run_tile(self, key, command):
        result = run_command(command)
        result["ran"] = time.time()
        with self.lock:
            self.results[key] = result
            self.busy.discard(key)
        self.app.poke(self.id)

    def collect(self):
        tiles, now, started = self.tiles(), time.time(), []
        for t in tiles:
            key = tile_key(t)
            try:
                every = max(int(t.get("interval") or 60), MIN_INTERVAL)
            except (TypeError, ValueError):
                every = 60
            with self.lock:
                due = key not in self.busy and now - self.last.get(key, 0) >= every
                if due:
                    self.busy.add(key)
                    self.last[key] = now
            if due:
                th = threading.Thread(target=self.run_tile, args=(key, t["command"]), daemon=True, name="custom:tile")
                th.start()
                started.append(th)
        deadline = time.time() + 1.5  # quick commands show on this collect; slow ones on the next
        for th in started:
            th.join(max(deadline - time.time(), 0))
        out = []
        with self.lock:
            live = {tile_key(t) for t in tiles}
            for k in list(self.results):
                if k not in live:
                    del self.results[k]
            for t in tiles:
                key = tile_key(t)
                r = self.results.get(key)
                tile = {"title": t.get("title") or t["command"][:40], "running": key in self.busy}
                if r:
                    tile.update({k: r[k] for k in ("ok", "data", "text", "ran", "error") if k in r})
                out.append(tile)
        bad = sum(1 for t in out if t.get("ok") is False)
        return {"summary": f"{len(out)} tile{'s' if len(out) != 1 else ''}" + (f" · {bad} failing" if bad else ""), "tiles": out}
