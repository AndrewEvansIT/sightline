"""History: recent samples in memory (sparklines), one-minute averages in SQLite (charts over days).

Also keeps the model speed-test results ("your own history" for each model).
"""

import collections
import json
import sqlite3
import threading
import time

from . import core

RECENT = 120  # samples kept in memory per key (10 minutes at 5 s)


class History:
    def __init__(self, path=None, keep_days=30):
        self.lock = threading.Lock()
        self.recent = collections.defaultdict(lambda: collections.deque(maxlen=RECENT))
        self.pending = collections.defaultdict(list)  # key -> values in the current minute
        self.minute = int(time.time() // 60)
        self.keep_days = keep_days
        self.db = sqlite3.connect(path or core.home("history.db"), check_same_thread=False)
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS samples (key TEXT, ts INTEGER, value REAL)")
            self.db.execute("CREATE INDEX IF NOT EXISTS samples_key_ts ON samples (key, ts)")
            self.db.execute("CREATE TABLE IF NOT EXISTS runs (ts INTEGER, kind TEXT, subject TEXT, data TEXT)")
        self.last_prune = 0

    def add(self, key, value, now=None):
        now = now or time.time()
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        with self.lock:
            self.recent[key].append((round(now), value))
            minute = int(now // 60)
            if minute != self.minute:
                self._flush(self.minute)
                self.minute = minute
            self.pending[key].append(value)

    def _flush(self, minute):
        rows = [(k, minute * 60, sum(v) / len(v)) for k, v in self.pending.items() if v]
        self.pending.clear()
        if rows:
            with self.db:
                self.db.executemany("INSERT INTO samples VALUES (?, ?, ?)", rows)
        if time.time() - self.last_prune > 3600:
            self.last_prune = time.time()
            with self.db:
                self.db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - self.keep_days * 86400,))

    def recent_values(self, key):
        with self.lock:
            return [v for _, v in self.recent.get(key, ())]

    def all_recent(self):
        with self.lock:
            return {k: [v for _, v in d] for k, d in self.recent.items()}

    def keys(self):
        with self.lock:
            mem = set(self.recent)
        rows = self.db.execute("SELECT DISTINCT key FROM samples").fetchall()
        return sorted(mem | {r[0] for r in rows})

    def series(self, key, seconds, points=240):
        """[(ts, value)] over the last `seconds`, averaged into at most `points` buckets."""
        since = time.time() - seconds
        if seconds <= RECENT * 5:
            with self.lock:
                return [(t, v) for t, v in self.recent.get(key, ()) if t >= since]
        rows = self.db.execute("SELECT ts, value FROM samples WHERE key = ? AND ts >= ? ORDER BY ts",
                               (key, since)).fetchall()
        if len(rows) <= points:
            return rows
        width = seconds / points
        buckets = collections.OrderedDict()
        for ts, v in rows:
            buckets.setdefault(int((ts - since) // width), []).append((ts, v))
        return [(vals[len(vals) // 2][0], sum(x for _, x in vals) / len(vals)) for vals in buckets.values()]

    # Runs (speed tests and the like) -------------------------------------------

    def add_run(self, kind, subject, data):
        with self.lock, self.db:
            self.db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (int(time.time()), kind, subject, json.dumps(data)))

    def runs(self, kind, subject=None, limit=50):
        q, args = "SELECT ts, subject, data FROM runs WHERE kind = ?", [kind]
        if subject:
            q += " AND subject = ?"
            args.append(subject)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        return [{"ts": ts, "subject": s, **json.loads(d)} for ts, s, d in self.db.execute(q, args).fetchall()]
