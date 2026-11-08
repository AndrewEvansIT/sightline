"""The widget contract. Every file in this package that defines a Widget subclass with an `id` is picked up.

A widget:
- says whether it applies to this Mac (`available`): no setup, so a widget that can't find its data hides itself;
- collects its data on its own schedule (`collect`, every `interval` seconds, in its own thread);
- may offer actions the page can call (`actions`), and settings the Settings window shows (`settings`).

The data a widget returns is JSON. Any widget can use the generic shape below and needs no page code;
widgets with a richer look ship a renderer in web/widgets/<id>.js.

Generic shape (all keys optional):
    {
      "summary": "short text for the card header",
      "state": "ok" | "warn" | "bad" | "off",
      "big":    [{"label": "CPU", "value": 42, "unit": "%", "state": "ok"}],
      "meters": [{"label": "Internal", "pct": 63, "text": "310 of 994 GB free", "state": "ok"}],
      "rows":   [{"label": "Firewall", "value": "on", "state": "ok", "sub": "detail line",
                  "actions": [{"id": "stop", "label": "Stop", "params": {...}, "confirm": true}]}],
      "note":   "a line of small print",
      "alerts": [{"key": "disk-full:Internal", "level": "warn", "text": "Internal disk is 95% full"}]
    }
"""

import importlib
import pkgutil
import threading
import time
import traceback


class Widget:
    id = ""                 # stable id, used in config and the page
    title = ""
    category = "system"     # system | ai | health | network | security | developer | custom
    description = ""        # one plain sentence for the Settings window
    interval = 30           # seconds between collects
    delay = 0               # seconds to wait before the first collect
    size = "m"              # s | m | l | xl: how much room the card wants
    default_on = True
    recheck = 300           # seconds between available() checks (also while available, so a card can go away)
    depends_on = []         # widget ids whose data this one reads; it collects again when they first report
    settings = []           # [{"key", "label", "type": bool|int|text|choice|list, "default", "choices", "help"}]
    alert_rules = []        # [{"key": "disk-full", "label": "A disk is almost full"}]; alert keys are "<rule>:<detail>"

    def __init__(self, app):
        self.app = app

    # Override these ----------------------------------------------------------

    def available(self):
        """True when this Mac has what the widget shows. Checked at start and every few minutes."""
        return True

    def collect(self):
        """Return the widget's data (a dict), or None to hide the card for now. Runs in the widget's own thread."""
        return {}

    def actions(self):
        """name -> callable(params) -> dict. Called from the page; the result is shown as a toast."""
        return {}

    # Helpers -----------------------------------------------------------------

    def setting(self, key):
        conf = self.app.config.widget(self.id)
        if key in conf:
            return conf[key]
        for s in self.settings:
            if s["key"] == key:
                return s.get("default")
        return None

    def record(self, key, value):
        """Add a number to the history (sparklines now, charts over days later)."""
        if value is not None:
            self.app.history.add(f"{self.id}.{key}", value)

    def data_of(self, wid):
        """Another widget's latest data (or {})."""
        return self.app.data(wid)

    @property
    def state(self):
        return self.app.state

    def meta(self):
        return {"id": self.id, "title": self.title, "category": self.category, "description": self.description,
                "size": self.size, "default_on": self.default_on, "settings": self.settings, "alert_rules": self.alert_rules,
                "actions": sorted(self.actions().keys())}


def discover():
    """All Widget subclasses defined in this package, by id."""
    found = {}
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            m = importlib.import_module(f"{__name__}.{mod.name}")
        except Exception:
            traceback.print_exc()
            continue
        for obj in vars(m).values():
            if isinstance(obj, type) and issubclass(obj, Widget) and obj is not Widget and obj.id:
                found[obj.id] = obj
    return found


class Runner:
    """Runs one widget: a thread that collects on schedule while the widget is enabled and available."""

    def __init__(self, app, widget):
        self.app, self.widget = app, widget
        self.wake = threading.Event()
        self.is_available = None
        self.checked = 0
        self.thread = threading.Thread(target=self.loop, daemon=True, name=f"widget:{widget.id}")

    def start(self):
        self.thread.start()

    def poke(self):
        """Collect again now (after an action or a settings change)."""
        self.wake.set()

    def loop(self):
        time.sleep(self.widget.delay)
        while True:
            wait = self.widget.interval
            if self.app.enabled(self.widget.id):
                if self.is_available is None or time.time() - self.checked > self.widget.recheck:
                    self.checked = time.time()
                    try:
                        self.is_available = bool(self.widget.available())
                    except Exception:
                        self.is_available = False
                if self.is_available:
                    started = time.time()
                    try:
                        data = self.widget.collect()
                        if data is None:
                            self.app.store_unavailable(self.widget.id)
                        else:
                            self.app.store(self.widget.id, data, took=time.time() - started)
                    except Exception as e:
                        traceback.print_exc()
                        self.app.store_error(self.widget.id, f"{type(e).__name__}: {e}")
                else:
                    self.app.store_unavailable(self.widget.id)
                    wait = min(self.widget.recheck, 300)
            self.wake.wait(wait)
            self.wake.clear()
