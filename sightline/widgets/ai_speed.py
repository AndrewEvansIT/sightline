"""Speed test: a quick, standard test of a local model through its OpenAI-compatible API.

The same prompt every time (about 500 tokens, built in code), 256 tokens out, temperature 0, streamed. It measures
time to first token, how fast the prompt is read (prompt tokens/s) and how fast the answer is written (tokens/s).
With the Power widget on, it also measures the energy one answer takes. Each result is kept in the history, so a
model can be compared with itself over time (after an update, a different quantization, another runtime).

Only talks to servers on this Mac (127.0.0.1 / localhost), and only when you press Run.
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request

from .. import core
from . import Widget
from .ai_runtimes import installed_runtimes

MAX_TOKENS = 256
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")

COLORS = ["red", "blue", "green", "yellow", "white", "black", "orange"]
THINGS = ["lamp", "bicycle", "kettle", "notebook", "umbrella", "clock", "ladder", "basket", "radio", "map", "key"]
PLACES = ["the kitchen", "the garden shed", "the hallway", "the attic", "the workshop"]
STATES = ["in good order", "slightly dusty", "missing a part", "freshly painted"]


def build_prompt(lines=21):
    """The fixed test prompt: a numbered inventory (about 500 tokens) and a request for a story using it.
    Deterministic, so every run and every Mac sends exactly the same text."""
    items = []
    for i in range(lines):
        items.append(f"{i + 1}. The {COLORS[i % len(COLORS)]} {THINGS[i % len(THINGS)]} in {PLACES[i % len(PLACES)]} "
                     f"was checked at {(i * 5) % 12 + 1} o'clock and found {STATES[i % len(STATES)]}.")
    return ("Here is an inventory of a house, one item per line.\n\n" + "\n".join(items) +
            "\n\nWrite a long, calm story about a caretaker walking through the house and visiting every item above, "
            "in order, describing each one. Do not stop early.")


PROMPT = build_prompt()


def is_local(base_url):
    try:
        host = urllib.parse.urlparse(base_url).hostname or ""
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def chunk_text(obj):
    """The text (or reasoning) a streamed chat/completions chunk carries, or ''."""
    out = ""
    for ch in obj.get("choices") or []:
        delta = ch.get("delta") or {}
        out += (delta.get("content") or "") + (delta.get("reasoning_content") or "") + (delta.get("reasoning") or "")
        out += ch.get("text") or ""
    return out


def parse_stream(lines, clock=time.time):
    """Read an OpenAI-style SSE stream. lines: an iterable of bytes or str lines.
    Returns {"first": t of first token, "end": t, "chunks": n token chunks, "usage": {...} or None, "text": str}."""
    first, chunks, usage, text = None, 0, None, []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if body == "[DONE]":
            break
        try:
            obj = json.loads(body)
        except ValueError:
            continue
        if obj.get("usage"):
            usage = obj["usage"]
        t = chunk_text(obj)
        if t:
            if first is None:
                first = clock()
            chunks += 1
            text.append(t)
    return {"first": first, "end": clock(), "chunks": chunks, "usage": usage, "text": "".join(text)}


def compute(start, stream, prompt_chars=len(PROMPT)):
    """Numbers from one run. Token counts come from the server's usage report; without one, each streamed chunk
    counts as one token and the prompt as chars/4, and the result is marked approx."""
    first, end = stream["first"], stream["end"]
    if first is None:
        return None
    usage = stream.get("usage") or {}
    approx = not (usage.get("completion_tokens") and usage.get("prompt_tokens"))
    completion = usage.get("completion_tokens") or stream["chunks"]
    prompt = usage.get("prompt_tokens") or round(prompt_chars / 4)
    ttft = max(first - start, 1e-6)
    gen_time = end - first
    # The first token arrives at `first`; the rest are written in the time after it.
    gen_tps = (completion - 1) / gen_time if completion > 1 and gen_time > 0 else None
    return {"ttft_s": round(ttft, 3), "prompt_tps": round(prompt / ttft, 1), "gen_tps": round(gen_tps, 1) if gen_tps else None,
            "prompt_tokens": prompt, "completion_tokens": completion, "total_s": round(end - start, 2), "approx": approx}


def energy(samples, total_s):
    """Watt-hours for one answer from power samples (watts) taken during the run, plus the average draw."""
    vals = [w for w in samples if w]
    if not vals or not total_s:
        return None, None
    avg = sum(vals) / len(vals)
    return round(avg * total_s / 3600, 4), round(avg, 1)


def watts_now(helper):
    """The whole Mac's power draw (or the chip's total) from the power helper, or None."""
    try:
        d = json.loads(core.run([helper, "400"], timeout=5, stderr=False) or "{}")
    except ValueError:
        return None
    return d.get("system_w") or d.get("total_w")


class SpeedTest(Widget):
    id = "ai_speed"
    title = "Speed test"
    category = "ai"
    description = ("Times a loaded local model with the same short test every run: time to first word, prompt reading "
                   "speed and writing speed (and energy per answer when Power is on). Runs only when you press Run.")
    interval = 15
    size = "m"
    depends_on = ["ai_runtimes", "power", "system"]

    def __init__(self, app):
        super().__init__(app)
        self.lock = threading.Lock()
        self.running = None
        self.last = None

    def available(self):
        return bool(installed_runtimes()) or bool(self.data_of("ai_runtimes").get("models"))

    def targets(self):
        out = []
        for m in self.data_of("ai_runtimes").get("models") or []:
            if m.get("base_url") and m.get("api_model") and m.get("state") == "loaded":
                out.append({"label": f"{m.get('name')} · {m.get('runtime')}", "model": m["api_model"], "name": m.get("name"),
                            "runtime": m.get("runtime"), "base_url": m["base_url"]})
        return out

    def collect(self):
        with self.lock:
            running, last = (dict(self.running) if self.running else None), self.last
        try:
            history = self.app.history.runs("speed", limit=20)
        except Exception:
            history = []
        if not last and history:
            last = history[0]
        targets = self.targets()
        summary = (f"running on {running['name']}…" if running else
                   f"{last['gen_tps']:.0f} tok/s · {last.get('name') or last.get('model')}" if last and last.get("gen_tps") else
                   f"{len(targets)} model{'s' if len(targets) != 1 else ''} ready" if targets else "no model loaded")
        return {"summary": summary, "targets": targets, "running": running, "last": last, "history": history,
                "power": bool(core.helper("power") and self.data_of("power"))}

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"run": self.run}

    def run(self, params):
        base_url = (params.get("base_url") or "").rstrip("/")
        model = params.get("model") or ""
        if not base_url or not model:
            return {"ok": False, "message": "Pick a loaded model first."}
        if not is_local(base_url):
            return {"ok": False, "message": "The speed test only talks to model servers on this Mac."}
        with self.lock:
            if self.running:
                return {"ok": False, "message": f"A speed test is already running on {self.running['name']}."}
            name = params.get("name") or model.rstrip("/").split("/")[-1]
            self.running = {"model": model, "name": name, "runtime": params.get("runtime"), "started": time.time()}
        threading.Thread(target=self.go, args=(base_url, model, name, params.get("runtime") or ""), daemon=True,
                         name="speed-test").start()
        return {"ok": True, "message": f"Speed test started on {name}. It takes a few seconds to a minute."}

    def go(self, base_url, model, name, runtime):
        result, error = None, None
        stop = threading.Event()
        samples = []
        helper = core.helper("power") if self.data_of("power") else None

        def sample():
            while not stop.is_set():
                samples.append(watts_now(helper))

        sampler = threading.Thread(target=sample, daemon=True) if helper else None
        if sampler:
            sampler.start()
        try:
            result = run_test(base_url, model)
            if result is None:
                error = "The model sent no text back."
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:200]
        stop.set()
        if sampler:
            sampler.join(2)
        if result:
            info = self.data_of("system").get("info") or {}
            wh, avg_w = energy(samples, result["total_s"])
            result.update(model=model, name=name, runtime=runtime, base_url=base_url, chip=info.get("chip"),
                          memory_gb=info.get("memory_gb"), wh=wh, avg_w=avg_w, ts=int(time.time()),
                          date=time.strftime("%Y-%m-%d"))
            try:
                self.app.history.add_run("speed", model, result)
            except Exception:
                pass
        with self.lock:
            self.running = None
            self.last = result or {"model": model, "name": name, "runtime": runtime, "error": error, "ts": int(time.time())}
        self.app.poke(self.id)


def run_test(base_url, model, timeout=600, prompt=PROMPT):
    """One streamed request; returns compute()'s numbers or None. Raises on connection errors."""
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": MAX_TOKENS,
            "temperature": 0, "stream": True, "stream_options": {"include_usage": True}}
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        stream = parse_stream(r)
    return compute(start, stream, len(prompt))
