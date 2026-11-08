"""AI coding agents: which ones are running (and where), and how many tokens they used today.

Sessions come from the process list: Claude Code, Codex CLI, Gemini CLI, Aider, Cursor's agent, GitHub Copilot CLI,
Goose and OpenCode, matched by executable name or by the script a node/python process runs.

Tokens come from the agents' own local log files, read incrementally (each collect reads only the new lines):
- Claude Code: ~/.claude/projects/<project>/<session>.jsonl (and subagents/*.jsonl), one assistant line per reply
  with message.usage; a reply can be written on several lines, so lines are de-duplicated by message id.
- Codex: ~/.codex/sessions/YYYY/MM/DD/*.jsonl, token_count events with a running total per session; we count the
  growth of that total, so repeated events add nothing.

Privacy: only usage numbers, model names, timestamps and message ids are kept. Message text, prompts, file contents
and tool output are never stored; lines that can't hold usage numbers aren't even parsed.
"""

import datetime
import glob
import json
import os
import re
import threading
import time

from .. import core
from . import Widget
from .memory import project_of

# Which processes are agents. exes: executable names. scripts: text that marks the agent in a node/python command line.
AGENTS = [
    {"key": "claude", "name": "Claude Code", "exes": ("claude",), "scripts": ("/@anthropic-ai/claude-code/",)},
    {"key": "codex", "name": "Codex", "exes": ("codex",), "scripts": ("/@openai/codex/",)},
    {"key": "gemini", "name": "Gemini CLI", "exes": ("gemini",), "scripts": ("/@google/gemini-cli/",)},
    {"key": "aider", "name": "Aider", "exes": ("aider",), "scripts": ("/bin/aider", "-m aider")},
    {"key": "cursor", "name": "Cursor agent", "exes": ("cursor-agent",), "scripts": ("/cursor-agent/",)},
    {"key": "copilot", "name": "Copilot CLI", "exes": ("copilot",), "scripts": ("/@github/copilot/",)},
    {"key": "goose", "name": "Goose", "exes": ("goose",), "scripts": ()},
    {"key": "opencode", "name": "OpenCode", "exes": ("opencode",), "scripts": ("/opencode-ai/",)},
]
AGENT_NAMES = {a["key"]: a["name"] for a in AGENTS}
EXE_TO_AGENT = {exe: a["key"] for a in AGENTS for exe in a["exes"]}
INTERPRETERS = re.compile(r"^(node|bun|deno|python[\d.]*|Python)$")
SCRIPT_EXT = re.compile(r"\.(js|mjs|cjs|py)$")

# API list prices in US dollars per million tokens. What the same tokens would cost if paid per token;
# subscriptions (Claude Pro/Max, ChatGPT Plus/Pro) are not billed this way.
# cache_write: 5-minute cache writes (1.25x input); cache_write_1h: 1-hour cache writes (2x input).
PRICES_AS_OF = "2026-10-06"
PRICES = {
    # Anthropic, from Anthropic's published model pricing as of the date above.
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5, "cache_write_1h": 20.0},
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0, "cache_write": 12.5, "cache_write_1h": 20.0},
    "claude-mythos-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25, "cache_write": 12.5, "cache_write_1h": 20.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 5.0, "cache_write_1h": 8.0},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25, "cache_write_1h": 10.0},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25, "cache_write_1h": 10.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5, "cache_write_1h": 4.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.5, "cache_write_1h": 4.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75, "cache_write_1h": 6.0},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125, "cache_write_1h": 0.20},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25, "cache_write_1h": 2.0},
    # OpenAI, from launch reports of the API price (check OpenAI's pricing page when updating).
    "gpt-6.1-sol": {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.5, "cache_write_1h": 2.5},
}
FIELDS = ("input", "output", "cache_write", "cache_write_1h", "cache_read")


def price_of(model):
    """The price row for a model name, matching dated variants (claude-opus-4-6-20260101) by prefix."""
    if not model:
        return None
    if model in PRICES:
        return PRICES[model]
    best = ""
    for name in PRICES:
        if model.startswith(name + "-") and len(name) > len(best):
            best = name
    return PRICES.get(best)


def cost(model, usage):
    """Dollars for one usage dict at API list prices, or None when the model has no price."""
    p = price_of(model)
    if not p:
        return None
    return sum(usage.get(f, 0) * p[f] for f in FIELDS) / 1e6


# Sessions -------------------------------------------------------------------------

def agent_of(comm, args):
    """Agent key for a process, from its executable path and command line, or None."""
    base = os.path.basename(comm or "")
    if base in EXE_TO_AGENT:
        return EXE_TO_AGENT[base]
    if not INTERPRETERS.match(base):
        return None
    for a in AGENTS:
        if any(s in (args or "") for s in a["scripts"]):
            return a["key"]
    # node /opt/homebrew/bin/gemini, python /…/bin/aider: the script's own name.
    for token in (args or "").split()[1:4]:
        if token.startswith("-"):
            continue
        name = SCRIPT_EXT.sub("", os.path.basename(token))
        if name in EXE_TO_AGENT:
            return EXE_TO_AGENT[name]
        break
    return None


def parse_etime(s):
    """ps etime ([[dd-]hh:]mm:ss) -> seconds."""
    s = (s or "").strip()
    if not s:
        return None
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [int(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return days * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def elapsed():
    out = {}
    for line in core.run(["ps", "-axo", "pid=,etime="]).splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = parse_etime(parts[1])
            except ValueError:
                pass
    return out


def cwds_of(pids):
    """pid -> working folder, for a list of pids (one lsof call)."""
    out, cur = {}, None
    if not pids:
        return out
    for line in core.run(["lsof", "-a", "-d", "cwd", "-p", ",".join(pids), "-Fpn"], timeout=10).splitlines():
        if line[:1] == "p":
            cur = line[1:]
        elif line[:1] == "n" and cur:
            out[cur] = line[1:]
    return out


def children_map(procs):
    kids = {}
    for pid, (ppid, _, _) in procs.items():
        kids.setdefault(ppid, []).append(pid)
    return kids


def descendants(pid, kids):
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        out.append(p)
        todo.extend(kids.get(p, []))
    return out


def find_sessions(procs, args, me):
    """[(pid, agent key)] for the user's agent processes, top-most only (a wrapper and its worker are one session)."""
    found = {}
    for pid, (_, uid, comm) in procs.items():
        if uid != me:
            continue
        key = agent_of(comm, args.get(pid, ""))
        if key:
            found[pid] = key
    roots = []
    for pid, key in found.items():
        p, nested = procs[pid][0], False
        for _ in range(30):
            if p in found and found[p] == key:
                nested = True
                break
            if p not in procs or p in ("0", "1"):
                break
            p = procs[p][0]
        if not nested:
            roots.append((pid, key))
    return roots


def project_label(cwd):
    if not cwd:
        return ""
    if cwd.rstrip("/") == os.path.expanduser("~"):
        return "~"
    return project_of(cwd)


# Tokens ---------------------------------------------------------------------------

def day_window(now=None):
    """(label, lo, hi): today's local date and its start and end as UTC 'YYYY-MM-DDTHH:MM:SS' strings."""
    now = now or time.time()
    local = datetime.datetime.fromtimestamp(now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    utc = datetime.timezone.utc
    to_utc = lambda d: datetime.datetime.fromtimestamp(time.mktime(d.timetuple()), utc).strftime(fmt)
    return start.strftime("%Y-%m-%d"), to_utc(start), to_utc(end)


def utc_key(ts):
    """A timestamp as a comparable UTC 'YYYY-MM-DDTHH:MM:SS' string, or '' if it can't be read."""
    if not isinstance(ts, str) or len(ts) < 19:
        return ""
    if ts.endswith("Z"):
        return ts[:19]
    try:
        d = datetime.datetime.fromisoformat(ts)
    except ValueError:
        return ""
    if d.tzinfo is None:
        return ""
    return d.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def claude_usage(line, lo, hi):
    """(message id, model, usage) from one Claude Code transcript line, or None. Keeps numbers only."""
    if '"usage"' not in line or (lo[:10] not in line and hi[:10] not in line):
        return None
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict) or o.get("type") != "assistant":
        return None
    msg = o.get("message") or {}
    u = msg.get("usage") if isinstance(msg, dict) else None
    model = msg.get("model") if isinstance(msg, dict) else None
    if not isinstance(u, dict) or not model or model.startswith("<"):
        return None
    k = utc_key(o.get("timestamp"))
    if not (lo <= k < hi):
        return None
    write = int(u.get("cache_creation_input_tokens") or 0)
    split = u.get("cache_creation") if isinstance(u.get("cache_creation"), dict) else {}
    write_1h = int(split.get("ephemeral_1h_input_tokens") or 0)
    usage = {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
             "cache_write": max(write - write_1h, 0), "cache_write_1h": min(write_1h, write),
             "cache_read": int(u.get("cache_read_input_tokens") or 0)}
    return (msg.get("id") or o.get("requestId") or o.get("uuid"), model, usage)


def codex_usage(total):
    """Codex token counts (input includes cached, output includes reasoning) -> our fields."""
    total = total or {}
    inp = int(total.get("input_tokens") or 0)
    cached = int(total.get("cached_input_tokens") or 0)
    write = int(total.get("cache_write_input_tokens") or 0)
    return {"input": max(inp - cached - write, 0), "output": int(total.get("output_tokens") or 0),
            "cache_write": write, "cache_write_1h": 0, "cache_read": cached}


class TokenScanner:
    """Reads the agents' log files incrementally and keeps today's totals by (agent, model)."""

    def __init__(self, claude_dirs=None, codex_dir=None):
        claude_home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        self.claude_dir = claude_dirs or os.path.join(claude_home, "projects")
        self.codex_dir = codex_dir or os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "sessions")
        self.lock = threading.Lock()
        self.thread = None
        self.files = {}          # path -> {"off", "ino", and for Codex: "model", "prev"}
        self.day = None
        self.seen = {}           # Claude message id -> usage already counted today
        self.totals = {}         # (agent, model) -> usage
        self.limits = None       # Codex's own usage-limit readout, latest seen
        self.scans = 0
        self.first_took = None
        self.last_took = None

    # Reading -------------------------------------------------------------------

    def candidates(self, midnight):
        """(agent, path) for log files changed since local midnight."""
        out = []
        for pattern in ("*/*.jsonl", "*/*/subagents/*.jsonl"):
            for p in glob.glob(os.path.join(self.claude_dir, pattern)):
                out.append(("claude", p))
        for p in glob.glob(os.path.join(self.codex_dir, "*", "*", "*", "*.jsonl")):
            out.append(("codex", p))
        fresh = []
        for agent, p in out:
            try:
                st = os.stat(p)
            except OSError:
                continue
            if st.st_mtime >= midnight:
                fresh.append((agent, p, st))
        return fresh

    def read_new(self, path, st):
        """Yield the complete new lines of a file since the last read, and remember the new offset."""
        state = self.files.setdefault(path, {"off": 0, "ino": st.st_ino})
        if state.get("ino") != st.st_ino or st.st_size < state["off"]:
            state.clear()
            state.update(off=0, ino=st.st_ino)
        if st.st_size == state["off"]:
            return
        try:
            with open(path, "rb") as f:
                f.seek(state["off"])
                while True:
                    raw = f.readline()
                    if not raw or not raw.endswith(b"\n"):
                        break  # a line still being written: read it next time
                    state["off"] += len(raw)
                    yield state, raw.decode("utf-8", "replace")
        except OSError:
            return

    def add(self, agent, model, usage):
        t = self.totals.setdefault((agent, model), dict.fromkeys(FIELDS, 0))
        for f in FIELDS:
            t[f] += usage.get(f, 0)

    def scan_claude(self, path, st, lo, hi):
        for _, line in self.read_new(path, st):
            got = claude_usage(line, lo, hi)
            if not got:
                continue
            mid, model, usage = got
            if mid:
                old = self.seen.get(mid)
                if old:
                    # Same reply on another line: count only growth (normally none).
                    usage = {f: max(usage[f] - old[f], 0) for f in FIELDS}
                    self.seen[mid] = {f: old[f] + usage[f] for f in FIELDS}
                else:
                    self.seen[mid] = dict(usage)
            self.add("claude", model, usage)

    def scan_codex(self, path, st, lo, hi):
        for state, line in self.read_new(path, st):
            if '"token_count"' not in line and '"turn_context"' not in line and '"thread_settings' not in line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            payload = o.get("payload") if isinstance(o, dict) else None
            if not isinstance(payload, dict):
                continue
            if o.get("type") == "turn_context" and payload.get("model"):
                state["model"] = payload["model"]
                continue
            settings = payload.get("thread_settings")
            if isinstance(settings, dict) and settings.get("model"):
                state["model"] = settings["model"]
                continue
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            total = info.get("total_token_usage")
            if not isinstance(total, dict):
                continue
            prev = state.get("prev")
            now_total = int(total.get("total_tokens") or 0)
            if prev is None:
                delta = codex_usage(total)
            elif now_total >= int(prev.get("total_tokens") or 0):
                a, b = codex_usage(total), codex_usage(prev)
                delta = {f: max(a[f] - b[f], 0) for f in FIELDS}
            else:  # the running total restarted: count this call alone
                delta = codex_usage(info.get("last_token_usage"))
            state["prev"] = {k: total.get(k) for k in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                                       "output_tokens", "total_tokens")}
            k = utc_key(o.get("timestamp"))
            if lo <= k < hi:
                self.add("codex", state.get("model") or "unknown", delta)
            limits = payload.get("rate_limits")
            if isinstance(limits, dict) and (not self.limits or k >= self.limits["at"]):
                self.limits = {"at": k, "windows": [
                    {"minutes": w.get("window_minutes"), "pct": w.get("used_percent"), "resets": w.get("resets_at")}
                    for w in (limits.get("primary"), limits.get("secondary")) if isinstance(w, dict)]}

    def scan(self, now=None):
        started = time.time()
        label, lo, hi = day_window(now)
        midnight = time.mktime(datetime.datetime.strptime(label, "%Y-%m-%d").timetuple())
        with self.lock:
            if label != self.day:
                self.day, self.seen, self.totals = label, {}, {}
            for agent, path, st in self.candidates(midnight):
                if agent == "claude":
                    self.scan_claude(path, st, lo, hi)
                else:
                    self.scan_codex(path, st, lo, hi)
            self.scans += 1
            self.last_took = round(time.time() - started, 2)
            if self.first_took is None:
                self.first_took = self.last_took

    # Running in the background ---------------------------------------------------

    def kick(self):
        """Start a scan in the background unless one is running."""
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._scan_safely, daemon=True, name="agents:tokens")
        self.thread.start()

    def _scan_safely(self):
        try:
            self.scan()
        except Exception:
            import traceback
            traceback.print_exc()

    def scanning(self):
        return self.scans == 0

    def report(self):
        """Today's totals by agent (with models), the dollar estimate, and models with no price."""
        with self.lock:
            totals = {k: dict(v) for k, v in self.totals.items()}
            limits = dict(self.limits) if self.limits else None
        agents, unpriced = {}, set()
        for (agent, model), u in totals.items():
            n = sum(u.values())
            if not n:
                continue
            a = agents.setdefault(agent, {"key": agent, "agent": AGENT_NAMES.get(agent, agent), "tokens": 0, "usd": 0.0,
                                          "priced": True, "models": [], **dict.fromkeys(FIELDS, 0)})
            usd = cost(model, u)
            if usd is None:
                unpriced.add(model)
                a["priced"] = False
            a["tokens"] += n
            a["usd"] += usd or 0
            for f in FIELDS:
                a[f] += u[f]
            a["models"].append({"model": model, "tokens": n, "usd": round(usd, 2) if usd is not None else None})
        out = sorted(agents.values(), key=lambda a: -a["tokens"])
        for a in out:
            a["usd"] = round(a["usd"], 2)
            a["models"].sort(key=lambda m: -m["tokens"])
        return {"by_agent": out, "total": sum(a["tokens"] for a in out), "usd": round(sum(a["usd"] for a in out), 2),
                "unpriced": sorted(unpriced), "limits": limits}


class Agents(Widget):
    id = "agents"
    title = "AI coding agents"
    category = "developer"
    description = "Coding agents running now (Claude Code, Codex, Gemini CLI and others) and the tokens they used today."
    interval = 30
    size = "m"

    def __init__(self, app):
        super().__init__(app)
        self.tokens = TokenScanner()

    def available(self):
        if any(core.which(exe) for a in AGENTS for exe in a["exes"]):
            return True
        return os.path.isdir(self.tokens.claude_dir) or os.path.isdir(self.tokens.codex_dir)

    def sessions(self):
        me = str(os.getuid())
        procs, args = core.processes(), core.process_args()
        roots = find_sessions(procs, args, me)
        if not roots:
            return []
        kids, fp, ages = children_map(procs), core.footprints(), elapsed()
        cwds = cwds_of([pid for pid, _ in roots])
        out = []
        for pid, key in roots:
            mem = sum(fp.get(p, 0) for p in descendants(pid, kids))
            out.append({"pid": int(pid), "key": key, "agent": AGENT_NAMES[key], "project": project_label(cwds.get(pid, "")),
                        "mem": int(mem), "secs": ages.get(pid)})
        out.sort(key=lambda s: (s["agent"], s["secs"] if s["secs"] is not None else 0))
        return out

    def collect(self):
        self.tokens.kick()
        sessions = self.sessions()
        tokens = self.tokens.report()
        scanning = self.tokens.scanning()
        if not scanning:
            self.record("tokens_today", tokens["total"])
        running = {}
        for s in sessions:
            running[s["agent"]] = running.get(s["agent"], 0) + 1
        parts = [f"{len(sessions)} running" if sessions else "None running"]
        if not scanning and tokens["total"]:
            parts.append(f"{short_count(tokens['total'])} tokens today")
        return {
            "summary": " · ".join(parts),
            "sessions": sessions,
            "running": running,
            "tokens": tokens,
            "scanning": scanning,
            "scan_took": self.tokens.first_took,
            "prices_as_of": PRICES_AS_OF,
        }


def short_count(n):
    """12.4M, 830K, 950."""
    n = float(n or 0)
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            v = n / size
            return f"{v:.1f}{unit}" if v < 100 else f"{v:.0f}{unit}"
    return f"{n:.0f}"
