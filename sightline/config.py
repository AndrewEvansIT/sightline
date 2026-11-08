"""Settings: defaults merged with the user's config.json. Everything works with no config at all."""

import copy
import json
import os
import threading

from . import core

DEFAULTS = {
    "version": 1,
    "port": 47800,
    "layout": "window",         # strip (bottom of a screen) | column (side of a screen) | window
    "screen": "auto",           # auto = the portrait screen if there is one, else the main screen
    "strip_height": 520,
    "column_width": 440,
    "theme": "dark",            # dark | light | auto
    "density": "comfortable",   # comfortable | compact
    "order": [],                # packing order chosen in Settings (empty = Sightline decides)
    "grid": {},                 # saved card positions per layout profile: "window:12" -> {id: {x, y, w, h}}
    "locked": False,            # stop dragging and resizing
    "widgets": {},              # id -> {"enabled": bool, ...widget settings}
    "alerts": {"enabled": True, "rules": {}},   # rule key prefix -> bool
    "lan": {"enabled": False, "token": ""},     # read-only view for other devices on your network
    "history_days": 30,
    "custom": [],               # script tiles: [{"title", "command", "interval"}]
    "setup_done": False,
}


def deep_merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, path=None):
        self.path = path or core.home("config.json")
        self.lock = threading.Lock()
        self.data = deep_merge(DEFAULTS, self._read())

    def _read(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def get(self, key, default=None):
        with self.lock:
            return copy.deepcopy(self.data.get(key, default))

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, changes):
        """Merge changes in and save. Lists are replaced whole (order, custom)."""
        with self.lock:
            self.data = deep_merge(self.data, changes)
            self._save()
            return copy.deepcopy(self.data)

    def replace(self, data):
        with self.lock:
            self.data = deep_merge(DEFAULTS, data)
            self._save()

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # Widget settings ---------------------------------------------------------

    def widget(self, wid):
        with self.lock:
            return copy.deepcopy(self.data.get("widgets", {}).get(wid, {}))

    def set_widget(self, wid, **values):
        return self.update({"widgets": {wid: values}})
