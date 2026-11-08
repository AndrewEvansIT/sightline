"""Local AI: every model runtime on this Mac, found with no setup, and what each has loaded.

Ollama and LM Studio are asked over their local APIs. Everything else (llama.cpp's llama-server, MLX servers and
scripts, oMLX, vllm-mlx, LocalAI, Jan, and any process holding MLX or PyTorch with a model's worth of memory) is
found from the process list: its command line, else the libraries it has open. A server's listening port is asked
GET /v1/models (OpenAI-compatible) for the models it serves, at most once a minute.

The Memory widget reads data["pids"] to name these processes "Local models"; the Speed test reads data["models"].
"""

import os
import re
import signal
import subprocess
import threading
import time

from .. import core
from . import Widget

OLLAMA = "http://127.0.0.1:11434"
LMSTUDIO = "http://127.0.0.1:1234"
LMS_DIRS = (os.path.expanduser("~/.lmstudio/bin"), os.path.expanduser("~/.cache/lm-studio/bin"))
GB = 2**30

# Command-line patterns that name a runtime, checked in order. The first match wins.
RUNTIME_PATTERNS = [
    ("oMLX", re.compile(r"(^|[/\s])omlx(-server)?(\s|$)|\bomlx\s+serve\b")),
    ("vllm-mlx", re.compile(r"vllm[-_]mlx")),
    ("MLX", re.compile(r"\bmlx_lm[.\s]server\b|\bmlx_vlm[.\s]server\b|\bmlx_lm\.|\bmlx_vlm\.|\bmlx_audio\b|\bmflux\b|"
                       r"\bmlx_lm\s+\w|\bmlx_vlm\s+\w|\bmlx-lm\b")),
    ("llama.cpp", re.compile(r"(^|/)llama-server(\s|$)|\bllama_cpp\.server\b|(^|/)llama-cli(\s|$)|(^|/)llama-run(\s|$)")),
    ("LocalAI", re.compile(r"(^|/)local-?ai(\s|$)", re.I)),
]
OPENAI_LIKE = ("llama.cpp", "MLX", "oMLX", "vllm-mlx", "LocalAI", "Jan")  # serve /v1/chat/completions
# Programs that only start another program: their command line names the runtime but they hold no model.
WRAPPERS = {"timeout", "caffeinate", "nohup", "env", "sh", "zsh", "bash", "fish", "uv", "uvx", "pipx", "sudo", "script",
            "nice", "time", "watch", "tmux", "screen", "login", "make", "npx", "node", "conda", "pixi", "rye", "poetry"}
DOWNLOADS = re.compile(r"\b(hf|huggingface-cli)\s+download\b|snapshot_download|\bmlx_lm\.convert\b|\bmlx_lm\s+convert\b")
AGENTS = {"claude": "Claude Code", "codex": "Codex", "aider": "Aider", "gemini": "Gemini CLI", "cursor-agent": "Cursor Agent",
          "opencode": "OpenCode", "goose": "Goose", "amp": "Amp"}
INTERPRETERS = ("node", "python", "python3", "bun", "deno", "Python")
FRAMEWORK_FILES = re.compile(r"site-packages/(mlx|torch)/|/libmlx\.dylib|/mlx\.metallib|/libtorch[_\w]*\.dylib")


# Parsers and decisions (pure; tested) --------------------------------------------------

def parse_quant(name):
    """The quantization in a model name: "...-4bit" -> "4bit", "...Q4_K_M.gguf" -> "Q4_K_M", "...:q8_0" -> "Q8_0"."""
    if not name:
        return None
    m = re.search(r"(?i)(?:^|[-_.:/\s])(i?q\d(?:_k)?(?:_[a-z0-9]{1,2})?)(?=$|[-_.\s/])", name)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?i)(\d{1,2})[-_ ]?bits?(?=$|[-_.\s/])", name)
    if m:
        return f"{m.group(1)}bit"
    m = re.search(r"(?i)(?:^|[-_.:/\s])(bf16|fp16|f16|fp32|f32|fp8|mxfp4|nvfp4|int4|int8)(?=$|[-_.\s/])", name)
    return m.group(1).upper() if m else None


def is_download(args):
    return bool(DOWNLOADS.search(args or ""))


def runtime_of(comm, args):
    """The runtime a process is, from its executable path and command line, or None.
    Wrappers (timeout, caffeinate, a shell, uv) that only start the runtime don't count."""
    base = os.path.basename(comm or "")
    if base in WRAPPERS or is_download(args):
        return None
    text = f"{comm} {args or ''}"
    if "/Jan.app/" in text and re.search(r"llama-server|cortex", text):
        return "Jan"
    if "/LM Studio.app/" in text or "/.lmstudio/" in text:
        return None  # LM Studio is read from its own API
    if base in ("ollama", "ollama_llama_server") or re.search(r"(^|/)ollama(\s|$)", args or ""):
        return "Ollama" if re.search(r"\brunner\b", args or "") or base == "ollama_llama_server" else None
    for name, pat in RUNTIME_PATTERNS:
        if pat.search(base) or pat.search(args or ""):
            return name
    if "mlx" in base.lower() and base.lower() not in ("mlx",):
        return "MLX"  # an app's own MLX helper (e.g. a dictation app's model process)
    return None


def framework_in(files):
    """'MLX' or 'PyTorch' when a process's open files show one loaded, else None."""
    m = FRAMEWORK_FILES.search(files or "")
    if not m:
        return None
    return "PyTorch" if "torch" in m.group(0) else "MLX"


def model_from_args(args):
    """The model a command line names: --model/-m/--model-path/-hf, a Hugging Face cache folder, or None."""
    if not args:
        return None
    m = re.search(r"models--([^/\s]+?)--([^/\s]+)", args)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    m = re.search(r"(?:^|\s)(?:--model|--model-path|--model-id|-hf|--hf-repo|--repo)[= ](\S+)", args)
    if not m:
        # -m is llama.cpp's model path, but Python's "run module": only a path or a model file counts.
        m = re.search(r"(?:^|\s)-m[= ](\S*/\S*|\S+\.(?:gguf|bin|safetensors))(?=\s|$)", args)
    if m:
        v = m.group(1).strip("'\"").rstrip("/")
        if v.startswith(("/", "~", ".")):
            v = os.path.basename(v)
            v = re.sub(r"\.gguf$", "", v)
        return v or None
    return None


def port_from_args(args):
    m = re.search(r"(?:^|\s)--port[= ](\d{2,5})\b", args or "")
    return int(m.group(1)) if m else None


def parse_listen_ports(out):
    """Ports from `lsof -nP -iTCP -sTCP:LISTEN -a -p PID -Fn` (lines like n127.0.0.1:8080, n*:1234, n[::1]:8080)."""
    ports = []
    for line in (out or "").splitlines():
        m = re.match(r"n.*:(\d+)$", line.strip())
        if m and int(m.group(1)) not in ports:
            ports.append(int(m.group(1)))
    return ports


def parse_footprint(out):
    """Bytes from `footprint PID` ("... Footprint: 291 GB ..."), or None."""
    m = re.search(r"Footprint:\s+([\d.]+)\s*([KMGT]?B)", out or "")
    if not m:
        return None
    return float(m.group(1)) * {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30, "TB": 2**40}[m.group(2)]


def agent_name(comm, args):
    """'Claude Code', 'Codex', … when a process is an AI agent's command-line tool, else None."""
    base = os.path.basename(comm or "")
    if base in AGENTS:
        return AGENTS[base]
    if base in INTERPRETERS or base.startswith("python"):
        parts = (args or "").split()
        for token in parts[1:3]:
            b = os.path.basename(token)
            if b in AGENTS:
                return AGENTS[b]
            if "@openai/codex" in token:
                return "Codex"
            if "@google/gemini-cli" in token:
                return "Gemini CLI"
            if "@anthropic-ai/claude-code" in token:
                return "Claude Code"
    return None


def app_name(path):
    """The app a program belongs to: '/Applications/LM Studio.app/Contents/…' -> 'LM Studio'. Outermost app wins."""
    m = re.match(r"(/.*?/([^/]+)\.app)(/|$)", path or "")
    if not m or m.group(1).startswith(("/System/Library/", "/usr/", "/Library/Apple/")) or ".framework/" in m.group(1):
        return None  # macOS's own, or a framework's stub app (Python.app inside Python.framework)
    return m.group(2)


def who_started(pid, procs, args, limit=40):
    """Who started a process: the nearest agent CLI or app above it (itself included), else 'you'.
    procs: pid -> (ppid, uid, comm). args: pid -> command line."""
    cur = str(pid)
    for _ in range(limit):
        if cur not in procs:
            break
        ppid, _uid, comm = procs[cur]
        agent = agent_name(comm, args.get(cur, ""))
        if agent:
            return agent
        app = app_name(comm)
        if app:
            return "Visual Studio Code" if app in ("Code", "Visual Studio Code") else app
        if ppid in ("0", "1", cur):
            break
        cur = ppid
    return None


def parse_ollama_ps(data):
    """Rows from Ollama's /api/ps."""
    rows = []
    for m in (data or {}).get("models") or []:
        name = m.get("name") or m.get("model") or "?"
        details = m.get("details") or {}
        size = m.get("size") or 0
        rows.append({"runtime": "Ollama", "id": name, "name": name,
                     "quant": details.get("quantization_level") or parse_quant(name),
                     "params": details.get("parameter_size"),
                     "gb": round(size / GB, 1) if size else None,
                     "gpu_gb": round((m.get("size_vram") or 0) / GB, 1),
                     "expires_at": m.get("expires_at"), "port": 11434, "state": "loaded",
                     "base_url": OLLAMA + "/v1", "api_model": name})
    return rows


def parse_lmstudio(data):
    """Loaded models from LM Studio's /api/v0/models."""
    rows = []
    for m in (data or {}).get("data") or []:
        if m.get("state") != "loaded":
            continue
        mid = m.get("id") or "?"
        rows.append({"runtime": "LM Studio", "id": mid, "name": mid, "quant": m.get("quantization") or parse_quant(mid),
                     "kind": m.get("type"), "port": 1234, "state": "loaded",
                     "base_url": LMSTUDIO + "/v1", "api_model": mid})
    return rows


def served_ids(data):
    """Model ids from an OpenAI-compatible /v1/models answer."""
    if not isinstance(data, dict):
        return []
    return [str(m.get("id")) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]


def short_name(model):
    """'mlx-community/Qwen3-32B-4bit' -> 'Qwen3-32B-4bit'; a path -> its last part."""
    return (model or "").rstrip("/").split("/")[-1] or model


def model_label(model, served, comm, args):
    """What to call a model process: its model's name, else what its server serves, else where it runs."""
    if model:
        return short_name(model)
    if served:
        return short_name(served[0]) if len(served) == 1 else f"{len(served)} models served"
    if app_name(comm):
        return f"{app_name(comm)} model"
    parts = (args or "").split()
    base = os.path.basename(comm or "")
    if base.lower().startswith("python") and len(parts) > 1:
        script = next((t for t in parts[1:] if not t.startswith("-")), "")
        if script:
            return f"Model in {os.path.basename(script)}"
    return f"Model in {base or '?'}"


def summarize(models):
    loaded = [m for m in models if m.get("state") == "loaded"]
    total = round(sum(m.get("gb") or 0 for m in models), 1)
    if not loaded:
        return "nothing loaded", total
    n = len(loaded)
    return f"{n} model{'s' if n != 1 else ''} · {total:.0f} GB", total


def runtimes_text(runtimes):
    """The empty-state line: 'Ollama is running, nothing loaded.' / 'LM Studio is installed, not running.'"""
    running = [r["name"] for r in runtimes if r.get("running")]
    idle = [r["name"] for r in runtimes if r.get("installed") and not r.get("running")]

    def join(names):
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    parts = []
    if running:
        parts.append(f"{join(running)} {'is' if len(running) == 1 else 'are'} running, nothing loaded.")
    if idle:
        parts.append(f"{join(idle)} {'is' if len(idle) == 1 else 'are'} installed, not running.")
    return " ".join(parts) or "No local AI runtime found."


# The widget ------------------------------------------------------------------------------

def lms_path():
    return core.which("lms", extra=LMS_DIRS)


def installed_runtimes():
    """Runtimes installed on this Mac (not necessarily running)."""
    found = []
    if core.which("ollama") or os.path.isdir("/Applications/Ollama.app"):
        found.append("Ollama")
    if lms_path() or os.path.isdir("/Applications/LM Studio.app") or os.path.isdir(os.path.expanduser("~/Applications/LM Studio.app")):
        found.append("LM Studio")
    if core.which("llama-server"):
        found.append("llama.cpp")
    if os.path.isdir("/Applications/Jan.app"):
        found.append("Jan")
    if core.which("local-ai"):
        found.append("LocalAI")
    if core.which("mlx_lm.server"):
        found.append("MLX")
    return found


class AIRuntimes(Widget):
    id = "ai_runtimes"
    title = "Local AI"
    category = "ai"
    description = ("Local model runtimes (Ollama, LM Studio, llama.cpp, MLX and others): which models are loaded, "
                   "how much memory each uses, who started it, and a Stop button.")
    interval = 10
    size = "l"
    settings = [{"key": "ask_servers", "label": "Ask running model servers which models they serve", "type": "bool",
                 "default": True, "help": "A small request to each local server's /v1/models, at most once a minute."}]

    def __init__(self, app):
        super().__init__(app)
        self.lock = threading.Lock()
        self.models = []          # last rows, for the Stop action
        self.cmds = {}            # pid -> command line when last seen (checked again before Stop)
        self.ports = {}           # pid -> (time, [ports])
        self.served = {}          # port -> (time, [ids])
        self.precise = {}         # pid -> (time, bytes) from `footprint`
        self.files = {}           # pid -> (time, framework or None)

    def available(self):
        return bool(installed_runtimes()) or bool(self.find_processes(core.processes(), core.process_args(), core.footprints()))

    # Finding things --------------------------------------------------------------

    def framework(self, pid):
        hit = self.files.get(pid)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        fw = framework_in(core.run(["lsof", "-p", pid, "-Fn"], timeout=10, stderr=False))
        self.files[pid] = (time.time(), fw)
        return fw

    def find_processes(self, procs, args, fp):
        """pid -> runtime for every model process in the process list (Ollama runners included)."""
        me = str(os.getpid())
        found = {}
        for pid, (_ppid, _uid, comm) in procs.items():
            if pid == me:
                continue
            a = args.get(pid, "")
            rt = runtime_of(comm, a)
            if rt:
                found[pid] = rt
                continue
            base = os.path.basename(comm).lower()
            if fp.get(pid, 0) >= GB / 2 and (base.startswith("python") or "/.venv/" in comm or "/venv/" in comm) \
                    and not is_download(a):
                fw = self.framework(pid)
                if fw:
                    found[pid] = fw
        return found

    def listen_ports(self, pid):
        hit = self.ports.get(pid)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        ports = parse_listen_ports(core.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", pid, "-Fn"],
                                            timeout=10, stderr=False))
        self.ports[pid] = (time.time(), ports)
        return ports

    def served_models(self, port):
        if not self.setting("ask_servers"):
            return None
        hit = self.served.get(port)
        if hit and time.time() - hit[0] < 60:
            return hit[1]
        data = core.http_json(f"http://127.0.0.1:{port}/v1/models", timeout=1.5)
        ids = served_ids(data) if data is not None else None
        self.served[port] = (time.time(), ids)
        return ids

    def footprint(self, pid, rough):
        """The precise memory number (`footprint PID`, ~10 ms) for model processes, refreshed every 30 s."""
        hit = self.precise.get(pid)
        if hit and time.time() - hit[0] < 30:
            return hit[1]
        b = parse_footprint(core.run(["footprint", pid], timeout=10)) or rough
        self.precise[pid] = (time.time(), b)
        return b

    # Collect ---------------------------------------------------------------------

    def collect(self):
        procs, args, fp = core.processes(), core.process_args(), core.footprints()
        found = self.find_processes(procs, args, fp)
        installed = installed_runtimes()
        runtimes = {name: {"name": name, "installed": True, "running": False} for name in installed}
        models, pids, cmds = [], {}, {}

        # Ollama
        version = core.http_json(OLLAMA + "/api/version", timeout=1)
        if version is not None:
            runtimes["Ollama"] = {"name": "Ollama", "installed": True, "running": True, "version": version.get("version")}
            serve = next((p for p, (_, _, c) in procs.items() if os.path.basename(c) == "ollama"
                          and re.search(r"\bserve\b", args.get(p, ""))), None)
            app = next((p for p, (_, _, c) in procs.items() if "/Ollama.app/" in c), None)
            who = (who_started(serve, procs, args) if serve else None) or ("Ollama" if app else "you")
            rows = parse_ollama_ps(core.http_json(OLLAMA + "/api/ps", timeout=2))
            runners = [p for p, rt in found.items() if rt == "Ollama"]
            for r in rows:
                r.update(who=who, can_stop=True)
            for pid in runners:
                pid_gb = self.footprint(pid, fp.get(pid, 0)) / GB
                # Match a runner to the loaded model closest in size (Ollama's API doesn't give pids).
                best = min(rows, key=lambda r: abs((r.get("gb") or 0) - pid_gb)) if rows else None
                pids[pid] = f"{short_name(best['name']) if best else 'Ollama model'} · {who}"
                if best:
                    best.setdefault("pids", []).append(int(pid))
            models += rows

        # LM Studio
        lm = core.http_json(LMSTUDIO + "/api/v0/models", timeout=1.5)
        if lm is not None:
            runtimes["LM Studio"] = {"name": "LM Studio", "installed": True, "running": True}
            rows = parse_lmstudio(lm)
            lms = lms_path()
            helpers = [p for p, (_, _, c) in procs.items()
                       if ("/LM Studio.app/" in c or "/.lmstudio/" in c) and fp.get(p, 0) >= GB]
            held = sum(self.footprint(p, fp.get(p, 0)) for p in helpers) / GB if rows else 0
            for r in rows:
                r.update(who="LM Studio", can_stop=bool(lms), gb=round(held, 1) if len(rows) == 1 and held else None)
            for p in helpers if rows else []:
                pids[p] = f"{short_name(rows[0]['name']) if len(rows) == 1 else 'LM Studio models'} · LM Studio"
            models += rows

        # Everything else: one row per model process.
        uid = str(os.getuid())
        for pid, rt in sorted(found.items(), key=lambda kv: -fp.get(kv[0], 0)):
            if rt == "Ollama":
                continue
            a = args.get(pid, "")
            comm = procs[pid][2]
            size = self.footprint(pid, fp.get(pid, 0)) if fp.get(pid, 0) >= GB / 5 else fp.get(pid, 0)
            ports = self.listen_ports(pid)
            port = port_from_args(a) if port_from_args(a) in ports else (ports[0] if ports else None)
            model = model_from_args(a)
            served = self.served_models(port) if port else None
            who = who_started(pid, procs, args)
            if not who and " CLAUDECODE=1" in core.run(["ps", "-wwE", "-o", "command=", "-p", pid], stderr=False):
                who = "Claude Code"  # detached from its session, it still carries Claude Code's environment
            who = who or "you"
            label = model_label(model, served, comm, a)
            state = "loaded" if size >= GB / 2 else "idle"
            runtimes.setdefault(rt, {"name": rt, "installed": True})["running"] = True
            models.append({"runtime": rt, "id": pid, "pid": int(pid), "name": label, "model": model,
                           "quant": parse_quant(model or label), "gb": round(size / GB, 1), "port": port,
                           "who": who, "state": state, "served": served or [],
                           "base_url": f"http://127.0.0.1:{port}/v1" if port and (served or rt in OPENAI_LIKE) else None,
                           "api_model": (model or (served[0] if served else None)) if port else None,
                           "can_stop": procs[pid][1] == uid and not app_name(comm),
                           "note": f"Quit {app_name(comm)} to free it." if app_name(comm) else None})
            cmds[pid] = a
            pids[pid] = f"{label} · {who}"

        summary, total = summarize(models)
        with self.lock:
            self.models, self.cmds = models, cmds
        rts = sorted(runtimes.values(), key=lambda r: (not r.get("running"), r["name"]))
        return {
            "summary": summary,
            "state": "ok" if any(m["state"] == "loaded" for m in models) else "off",
            "models": models,
            "pids": pids,
            "total_gb": total,
            "runtimes": rts,
            "empty_text": runtimes_text([r for r in rts if not any(m["runtime"] == r["name"] for m in models)]),
        }

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"stop": self.stop, "stop_all": self.stop_all}

    def find(self, params):
        rt, mid = params.get("runtime"), str(params.get("id") or params.get("pid") or "")
        with self.lock:
            for m in self.models:
                if m["runtime"] == rt and str(m["id"]) == mid:
                    return dict(m)
        return None

    def stop(self, params):
        m = self.find(params)
        if not m:
            return {"ok": False, "message": "That model isn't loaded any more."}
        return self.stop_one(m)

    def stop_one(self, m):
        name = m.get("name")
        if m["runtime"] == "Ollama":
            r = core.http_json(OLLAMA + "/api/generate", {"model": m["id"], "keep_alive": 0}, timeout=15)
            return {"ok": r is not None, "message": f"Asked Ollama to unload {name}." if r is not None else "Ollama didn't answer."}
        if m["runtime"] == "LM Studio":
            lms = lms_path()
            if not lms:
                return {"ok": False, "message": "Unload it in LM Studio (its lms command isn't installed)."}
            p = subprocess.run([lms, "unload", m["id"]], capture_output=True, text=True, timeout=30)
            return {"ok": p.returncode == 0,
                    "message": f"LM Studio unloaded {name}." if p.returncode == 0 else (p.stderr or p.stdout).strip()[:200]}
        if not m.get("can_stop"):
            return {"ok": False, "message": m.get("note") or f"{name} isn't yours to stop."}
        with self.lock:
            cmd = self.cmds.get(str(m["pid"]), "")
        return stop_process(m["pid"], cmd, name)

    def stop_all(self, params=None):
        with self.lock:
            models = [dict(m) for m in self.models]
        if not models:
            return {"ok": True, "message": "No local models are loaded."}
        done, failed = [], []
        for m in models:
            r = self.stop_one(m)
            (done if r.get("ok") else failed).append(m["name"])
        msg = f"Asked {len(done)} model{'s' if len(done) != 1 else ''} to stop." if done else "Nothing was stopped."
        if failed:
            msg += f" Couldn't stop: {', '.join(failed)}."
        return {"ok": not failed, "message": msg}


def stop_process(pid, cmd, name, run=core.run, kill=os.kill, uid=None):
    """SIGTERM a model process, only if it's yours and still runs the same command line."""
    uid = str(os.getuid() if uid is None else uid)
    line = run(["ps", "-ww", "-p", str(pid), "-o", "uid=,args="], stderr=False).strip().split(None, 1)
    if len(line) != 2:
        return {"ok": True, "message": f"{name} has already stopped."}
    if line[0] != uid:
        return {"ok": False, "message": f"{name} belongs to another user; Sightline only stops your own processes."}
    if not cmd or line[1] != cmd:
        return {"ok": False, "message": f"Process {pid} is running something else now; left it alone."}
    try:
        kill(int(pid), signal.SIGTERM)
    except OSError as e:
        return {"ok": False, "message": f"Couldn't stop {name}: {e.strerror}."}
    return {"ok": True, "message": f"Asked {name} to stop."}
