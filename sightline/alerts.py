"""Alerts: widgets report problems in their data ("alerts": [...]); this turns new ones into notifications.

A notification goes out when an alert first appears (or gets worse), at most once per cooldown per key.
Inside the Mac app, the app picks notifications up (/api/alerts/next) and shows them as its own;
without the app (plain `python3 -m sightline`), they go through osascript.
"""

import collections
import threading
import time

from . import core

COOLDOWN = 30 * 60
LEVELS = {"info": 0, "warn": 1, "bad": 2}


class Alerts:
    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        self.active = {}            # key -> alert (with widget, since)
        self.sent = {}              # key -> (time, level)
        self.outbox = collections.deque(maxlen=50)
        self.app_seen = 0           # last time the Mac app asked for notifications

    def rule_on(self, key):
        conf = self.config.get("alerts", {})
        if not conf.get("enabled", True):
            return False
        rules = conf.get("rules", {})
        prefix = key.split(":", 1)[0]
        return rules.get(prefix, True)

    def update(self, widget_id, alerts, now=None):
        """Replace a widget's current alerts; notify about new or worse ones."""
        now = now or time.time()
        with self.lock:
            current = {a["key"]: a for a in alerts or [] if a.get("key")}
            for key in [k for k, a in self.active.items() if a["widget"] == widget_id and k not in current]:
                del self.active[key]
            for key, a in current.items():
                level = a.get("level", "warn")
                prev = self.active.get(key)
                self.active[key] = dict(a, widget=widget_id, level=level, since=prev["since"] if prev else now)
                if not self.rule_on(key) or a.get("silent"):
                    continue
                last = self.sent.get(key)
                worse = last and LEVELS.get(level, 1) > LEVELS.get(last[1], 1)
                if prev is None and (not last or now - last[0] > COOLDOWN) or worse:
                    self.sent[key] = (now, level)
                    self._deliver(a.get("title") or "Sightline", a.get("text", ""), level)

    def _deliver(self, title, text, level):
        note = {"title": title, "text": text, "level": level, "at": time.time()}
        if time.time() - self.app_seen < 30:
            self.outbox.append(note)
        else:
            script = f'display notification {_q(text)} with title {_q(title)}'
            core.run(["osascript", "-e", script], timeout=5)

    def take(self):
        """The Mac app collects pending notifications."""
        with self.lock:
            self.app_seen = time.time()
            out = list(self.outbox)
            self.outbox.clear()
            return out

    def list(self):
        with self.lock:
            return sorted(self.active.values(), key=lambda a: (-LEVELS.get(a["level"], 1), a["since"]))


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
