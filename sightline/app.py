"""The running app: config, state, history, alerts and one runner per widget."""

import threading
import time

from . import __version__, widgets
from .alerts import Alerts
from .config import Config
from .core import State
from .history import History


class App:
    def __init__(self, config=None, state=None, history=None, start=True):
        self.config = config or Config()
        self.state = state or State()
        self.history = history or History(keep_days=self.config.get("history_days", 30))
        self.alerts = Alerts(self.config)
        self.lock = threading.Lock()
        self.status = {}  # widget id -> {"data", "at", "took", "error", "available"}
        self.classes = widgets.discover()
        self.widgets = {wid: cls(self) for wid, cls in self.classes.items()}
        self.runners = {wid: widgets.Runner(self, w) for wid, w in self.widgets.items()}
        self.started = time.time()
        if start:
            for r in self.runners.values():
                r.start()

    # Widget state --------------------------------------------------------------

    def enabled(self, wid):
        w = self.widgets.get(wid)
        if not w:
            return False
        return self.config.widget(wid).get("enabled", w.default_on)

    def store(self, wid, data, took=0.0):
        alerts = data.pop("alerts", None) if isinstance(data, dict) else None
        with self.lock:
            first = not (self.status.get(wid) or {}).get("data")
            self.status[wid] = {"data": data, "at": time.time(), "took": round(took, 2), "error": None, "available": True}
        self.alerts.update(wid, alerts or [])
        if first:  # widgets that read this one's data shouldn't wait a whole interval for it
            for other, w in self.widgets.items():
                if wid in (w.depends_on or []):
                    self.poke(other)

    def store_error(self, wid, error):
        with self.lock:
            entry = self.status.setdefault(wid, {"data": None})
            entry.update(error=error, at=time.time(), available=True)

    def store_unavailable(self, wid):
        with self.lock:
            self.status[wid] = {"data": None, "at": time.time(), "error": None, "available": False}
        self.alerts.update(wid, [])

    def data(self, wid):
        with self.lock:
            entry = self.status.get(wid) or {}
            return entry.get("data") or {}

    def poke(self, wid):
        r = self.runners.get(wid)
        if r:
            r.poke()

    # What the page reads ---------------------------------------------------------

    def order(self):
        """Widget ids in display order: the saved order first, then any others by category."""
        cats = ["system", "ai", "health", "network", "security", "developer", "custom"]
        saved = [w for w in self.config.get("order", []) if w in self.widgets]
        rest = sorted((w for w in self.widgets if w not in saved),
                      key=lambda w: (cats.index(self.widgets[w].category) if self.widgets[w].category in cats else 99,
                                     self.widgets[w].title))
        return saved + rest

    def snapshot(self):
        with self.lock:
            st = {k: dict(v) for k, v in self.status.items()}
        return {
            "version": __version__,
            "now": time.time(),
            "order": self.order(),
            "widgets": {wid: dict(st.get(wid, {}), enabled=self.enabled(wid)) for wid in self.widgets},
            "alerts": self.alerts.list(),
            "recent": self.history.all_recent(),
        }

    def meta(self):
        out = []
        for wid in self.order():
            w = self.widgets[wid]
            m = w.meta()
            m["enabled"] = self.enabled(wid)
            m["available"] = self.runners[wid].is_available
            m["values"] = self.config.widget(wid)
            out.append(m)
        return out

    def action(self, wid, name, params):
        w = self.widgets.get(wid)
        if not w:
            return {"ok": False, "message": "No such widget."}
        fn = w.actions().get(name)
        if not fn:
            return {"ok": False, "message": "No such action."}
        try:
            result = fn(params or {}) or {}
        except Exception as e:
            result = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        result.setdefault("ok", True)
        self.poke(wid)
        return result
