"""Model library: every model file on this Mac and its drives, with duplicates and whether each one fits in memory now.

Looks in the usual places with no setup: Hugging Face caches (here and on every drive under /Volumes), Ollama,
LM Studio, and loose .gguf files in ~/models and ~/Downloads. Scanning big folders runs in a background thread;
collect() returns the last scan right away and adds the memory verdicts, which change minute to minute.
"""

import json
import os
import re
import struct
import subprocess
import threading
import time
import traceback

from .. import core
from . import Widget
from .memory import available_bytes

VOLUMES = "/Volumes"
HF_SUBDIRS = ("HuggingFace/hub", "huggingface/hub", "hf/hub", ".cache/huggingface/hub", "models/hub")
CONTEXTS = (8192, 32768, 131072)
SCAN_EVERY = 600          # seconds before a scan counts as stale
GGUF_LIMIT = 64 * 2**20   # never read more than this much of a GGUF header
WEIGHT_EXT = (".safetensors", ".gguf", ".bin", ".pt", ".pth", ".onnx", ".npz", ".mlpackage")

# llama.cpp's general.file_type numbers.
GGUF_FILE_TYPES = {0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K", 11: "Q3_K_S",
                   12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S", 17: "Q5_K_M", 18: "Q6_K",
                   19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS", 23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL",
                   26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S", 29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
                   36: "TQ1_0", 37: "TQ2_0", 38: "MXFP4"}
SHARD = re.compile(r"-(\d{5})-of-(\d{5})(?=\.gguf$)", re.I)


# Where models live -----------------------------------------------------------------

def _home(home=None):
    return home or os.path.expanduser("~")


def _key(path):
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def volume_roots(volumes=VOLUMES):
    """Mounted drives under /Volumes, skipping the startup disk's alias and anything we can't read."""
    out = []
    try:
        names = sorted(os.listdir(volumes))
    except OSError:
        return out
    for name in names:
        p = os.path.join(volumes, name)
        if os.path.realpath(p) == "/" or not os.path.isdir(p):
            continue
        out.append(p)
    return out


def is_hub(path):
    """A Hugging Face cache folder: it holds models--<org>--<name> folders."""
    try:
        with os.scandir(path) as it:
            return any(e.name.startswith("models--") and e.is_dir() for e in it)
    except OSError:
        return False


def drive_of(path, volumes=VOLUMES):
    """The drive's name for paths under /Volumes, else "This Mac"."""
    bases = {volumes.rstrip("/"), os.path.realpath(volumes)}
    for p in (path, os.path.realpath(path)):
        for base in bases:
            if p.startswith(base + "/"):
                return p[len(base) + 1:].split("/", 1)[0]
    return "This Mac"


def hf_caches(env=None, home=None, volumes=VOLUMES):
    """Every Hugging Face cache on this Mac and its drives, once each: [(path, drive name)]."""
    env = os.environ if env is None else env
    home = _home(home)
    cands = []
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        if env.get(var):
            cands.append(env[var])
    if env.get("HF_HOME"):
        cands.append(os.path.join(env["HF_HOME"], "hub"))
    if env.get("XDG_CACHE_HOME"):
        cands.append(os.path.join(env["XDG_CACHE_HOME"], "huggingface", "hub"))
    cands.append(os.path.join(home, ".cache", "huggingface", "hub"))
    for root in volume_roots(volumes):
        cands += [os.path.join(root, sub) for sub in HF_SUBDIRS]
        # A shallow look for any other folder named "hub": <drive>/hub and <drive>/<folder>/hub.
        cands.append(os.path.join(root, "hub"))
        try:
            with os.scandir(root) as it:
                for e in it:
                    if e.is_dir() and not e.name.startswith("."):
                        cands.append(os.path.join(e.path, "hub"))
        except OSError:
            pass
    seen, out = set(), []
    for c in cands:
        if not is_hub(c):
            continue
        k = _key(c)
        if k in seen:
            continue
        seen.add(k)
        out.append((c, drive_of(c, volumes)))
    return out


def ollama_roots(env=None, home=None):
    env = os.environ if env is None else env
    cands = ([env["OLLAMA_MODELS"]] if env.get("OLLAMA_MODELS") else []) + [os.path.join(_home(home), ".ollama", "models")]
    return _unique_dirs(c for c in cands if os.path.isdir(os.path.join(c, "manifests")) or os.path.isdir(os.path.join(c, "blobs")))


def lmstudio_roots(home=None):
    home = _home(home)
    cands = [os.path.join(home, ".lmstudio", "models"), os.path.join(home, ".cache", "lm-studio", "models")]
    try:  # LM Studio lets people move the models folder; it saves the choice here.
        with open(os.path.join(home, ".lmstudio", "settings.json")) as f:
            folder = json.load(f).get("downloadsFolder")
        if isinstance(folder, str) and folder:
            cands.insert(0, os.path.expanduser(folder))
    except (OSError, ValueError, AttributeError):
        pass
    return _unique_dirs(c for c in cands if os.path.isdir(c))


def loose_dirs(home=None, downloads=True):
    home = _home(home)
    dirs = [(os.path.join(home, "models"), "Models folder")]
    if downloads:
        dirs.append((os.path.join(home, "Downloads"), "Downloads"))
    return [(d, label) for d, label in dirs if os.path.isdir(d)]


def _unique_dirs(paths):
    seen, out = set(), []
    for p in paths:
        k = _key(p)
        if k and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def any_location(env=None, home=None, volumes=VOLUMES, downloads=True):
    if hf_caches(env, home, volumes) or ollama_roots(env, home) or lmstudio_roots(home):
        return True
    for d, _ in loose_dirs(home, downloads):
        try:
            if any(n.lower().endswith(".gguf") for n in os.listdir(d)):
                return True
        except OSError:
            pass
    return False


# Names and formats ---------------------------------------------------------------------

def repo_of(folder_name):
    """models--mlx-community--Qwen3-4B-4bit -> mlx-community/Qwen3-4B-4bit."""
    parts = folder_name.split("--")
    if len(parts) < 2 or parts[0] != "models":
        return None
    return parts[1] if len(parts) == 2 else parts[1] + "/" + "--".join(parts[2:])


def parse_quant(name):
    """4bit, 8bit, Q4_K_M, IQ2_XXS, bf16 ... from a model or file name; "" when it doesn't say."""
    s = os.path.basename(str(name or ""))
    s = re.sub(r"\.gguf$", "", s, flags=re.I)
    m = re.search(r"(?i)(?<![a-z0-9])((?:i|d)?q\d+(?:_[a-z0-9]+)*)(?![a-z0-9])", s)
    if m:
        return m.group(1).upper()
    m = re.search(r"(?i)(?<![a-z0-9])(\d{1,2})[-_ ]?bits?(?![a-z0-9])", s)
    if m:
        return f"{m.group(1)}bit"
    m = re.search(r"(?i)(?<![a-z0-9])(bf16|fp16|f16|fp8|f32|fp32|mxfp4|nvfp4|int4|int8)(?![a-z0-9])", s)
    return m.group(1).lower() if m else ""


def norm(name):
    """A loose name for matching a running model to a file: no publisher, tag, extension or punctuation."""
    s = str(name or "").strip().lower().rstrip("/")
    if "/snapshots/" in s:
        s = s.split("/snapshots/")[0]
    s = s.split("/")[-1]
    if s.startswith("models--"):
        s = s.split("--")[-1]
    s = re.sub(r"\.gguf$", "", s)
    s = SHARD.sub("", s + ".gguf")[:-5]
    s = re.sub(r":latest$", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _read_json(path, limit=4 * 2**20):
    try:
        if os.path.getsize(path) > limit:
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _shard_groups(files):
    """Group .gguf files so the parts of a split model stay together: {first part path: [paths]}."""
    groups = {}
    for p in sorted(files):
        base = SHARD.sub("", os.path.basename(p))
        groups.setdefault((os.path.dirname(p), base.lower()), []).append(p)
    return {g[0]: g for g in groups.values()}


# Context memory ----------------------------------------------------------------------

def _first(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            return int(v)
    return 0


def kv_from_config(cfg):
    """KV-cache bytes per token from a Hugging Face config.json, or None when it doesn't describe a language model.

    {"full": bytes per token across full-attention layers, "sliding": bytes per token across sliding-window layers,
     "window": sliding window in tokens, "max": longest context the model takes}
    """
    if not isinstance(cfg, dict):
        return None
    c = dict(cfg)
    for k in ("text_config", "llm_config", "language_config"):
        if isinstance(cfg.get(k), dict):
            c.update(cfg[k])
            break
    layers = _first(c, "num_hidden_layers", "n_layer", "num_layers")
    heads = _first(c, "num_attention_heads", "n_head")
    hidden = _first(c, "hidden_size", "n_embd", "d_model")
    if not layers or not (heads or c.get("kv_lora_rank")):
        return None
    if c.get("kv_lora_rank"):  # multi-head latent attention (DeepSeek-style): one compressed vector per layer
        per_layer = (_first(c, "kv_lora_rank") + _first(c, "qk_rope_head_dim")) * 2
    else:
        kv = _first(c, "num_key_value_heads", "num_kv_heads", "n_head_kv") or (1 if c.get("multi_query") else heads)
        head_dim = _first(c, "head_dim") or (hidden // heads if hidden and heads else 0)
        if not head_dim:
            return None
        per_layer = 2 * kv * head_dim * 2
    window = _first(c, "sliding_window")
    types = c.get("layer_types")
    if isinstance(types, list) and types:
        attn = [str(t) for t in types if "attention" in str(t) and "linear" not in str(t)]
        sliding = sum(1 for t in attn if "sliding" in t or "local" in t)
        full = len(attn) - sliding
    elif _first(c, "full_attention_interval"):
        full, sliding = layers // _first(c, "full_attention_interval"), 0
    elif _first(c, "sliding_window_pattern") and window:
        full = layers // _first(c, "sliding_window_pattern")
        sliding = layers - full
    else:
        full, sliding = layers, 0
    if not window:
        full, sliding = full + sliding, 0
    return {"full": per_layer * full, "sliding": per_layer * sliding, "window": window,
            "max": _first(c, "max_position_embeddings", "n_positions", "max_sequence_length", "seq_length")}


def kv_from_gguf(meta):
    """The same numbers from GGUF header metadata (llama.cpp key names)."""
    if not meta:
        return None
    arch = meta.get("general.architecture")
    if not isinstance(arch, str):
        return None

    def g(k):
        return meta.get(f"{arch}.{k}")

    def n(v):
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    layers = n(g("block_count"))
    heads, kv = g("attention.head_count"), g("attention.head_count_kv")
    if not layers:
        return None
    if n(g("attention.kv_lora_rank")):
        per_token = layers * (n(g("attention.kv_lora_rank")) + n(g("rope.dimension_count"))) * 2
    else:
        heads_n = max(heads) if isinstance(heads, list) and heads else n(heads)
        klen = n(g("attention.key_length")) or (n(g("embedding_length")) // heads_n if heads_n else 0)
        vlen = n(g("attention.value_length")) or klen
        if isinstance(kv, list):
            kv_total = sum(n(x) for x in kv)
        else:
            kv_total = (n(kv) or heads_n) * layers
        if not klen or not kv_total:
            return None
        per_token = kv_total * (klen + vlen) * 2
    return {"full": per_token, "sliding": 0, "window": 0, "max": n(g("context_length"))}


def kv_bytes(kv, tokens):
    if not kv:
        return 0
    t = min(tokens, kv["max"]) if kv.get("max") else tokens
    return kv["full"] * t + kv["sliding"] * min(t, kv["window"] or t)


def context_costs(kv, sizes=CONTEXTS):
    """[{"tokens", "gb"}]: the extra memory for 8K, 32K and 128K tokens, capped at the model's longest context."""
    if not kv or not (kv["full"] or kv["sliding"]):
        return []
    out, seen = [], set()
    for s in sizes:
        t = min(s, kv["max"]) if kv.get("max") else s
        if t in seen:
            continue
        seen.add(t)
        out.append({"tokens": t, "gb": round(kv_bytes(kv, t) / 2**30, 2)})
    return out


# GGUF headers ------------------------------------------------------------------------

_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_SKIP = object()


class _Reader:
    def __init__(self, f, limit):
        self.f, self.limit = f, limit

    def read(self, n):
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("short read")
        return b

    def skip(self, n):
        self.f.seek(n, 1)
        if self.f.tell() > self.limit:
            raise ValueError("header too big")

    def scalar(self, t):
        fmt = _SCALARS[t]
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self, keep=1024):
        n = self.scalar(10)
        if n > keep:
            self.skip(n)
            return _SKIP
        return self.read(n).decode("utf-8", "replace")

    def value(self, t, depth=0):
        if t in _SCALARS:
            return self.scalar(t)
        if t == 8:
            return self.string()
        if t == 9 and depth < 2:
            it, count = self.scalar(4), self.scalar(10)
            if it in _SCALARS:
                size = struct.calcsize(_SCALARS[it])
                if count <= 4096:
                    return [self.scalar(it) for _ in range(count)]
                self.skip(size * count)
                return _SKIP
            if it == 8:
                for _ in range(count):  # tokenizer vocabularies: skip each string by its length
                    self.skip(self.scalar(10))
                return _SKIP
            for _ in range(count):
                self.value(it, depth + 1)
            return _SKIP
        raise ValueError(f"unknown GGUF type {t}")


def read_gguf_header(path, limit=GGUF_LIMIT):
    """Key/value metadata from a GGUF file's header (v2 and v3), stopping before the tensor list. None if unreadable.

    Long strings and big arrays (tokenizer vocabularies, chat templates) are skipped, not loaded.
    """
    try:
        with open(path, "rb") as f:
            r = _Reader(f, limit)
            if r.read(4) != b"GGUF":
                return None
            version = r.scalar(4)
            if version < 2 or version > 3:
                return None
            r.scalar(10)            # tensor count
            count = r.scalar(10)
            if count > 100000:
                return None
            meta = {"gguf.version": version}
            for _ in range(count):
                key = r.string(4096)
                if key is _SKIP:
                    return None
                val = r.value(r.scalar(4))
                if val is not _SKIP:
                    meta[key] = val
                if f.tell() > limit:
                    return None
            return meta
    except (OSError, ValueError, KeyError, struct.error):
        return None


_gguf_cache = {}


def gguf_meta(path):
    """read_gguf_header, remembered per file (path, size, modified time) across scans."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    k = (path, st.st_size, st.st_mtime)
    if k not in _gguf_cache:
        meta = read_gguf_header(path)
        keep = None
        if meta:
            keep = {kk: v for kk, v in meta.items() if not isinstance(v, list) or len(v) <= 512}
        _gguf_cache[k] = keep
    return _gguf_cache[k]


# Scanning ------------------------------------------------------------------------------

class Tally:
    """Counts each file once across the whole scan (symlinks and hard links share an inode)."""

    def __init__(self):
        self.sizes = {}

    def add(self, inodes, path):
        """Stat a file (following links) into `inodes`; returns the stat or None when it's missing."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        k = (st.st_dev, st.st_ino)
        inodes[k] = st.st_size
        self.sizes[k] = st.st_size
        return st

    def total(self):
        return sum(self.sizes.values())


def _model(**kw):
    m = {"name": "", "store": "", "drive": "", "path": "", "bytes": 0, "own": 0, "format": "", "quant": "",
         "modified": 0, "kv": None, "keys": [], "inodes": {}, "trashable": False, "partial": 0, "match": []}
    m.update(kw)
    return m


def _format(names, cfg, name):
    low = [n.lower() for n in names]
    if any(n.endswith(".gguf") for n in low):
        return "GGUF"
    if any(n.endswith(".safetensors") or n.endswith(".npz") for n in low):
        if "model_index.json" in low:
            return "Diffusers"
        if "mlx" in name.lower() or (isinstance(cfg, dict) and isinstance(cfg.get("quantization"), dict)):
            return "MLX"
        return "safetensors"
    if any(n.endswith((".bin", ".pt", ".pth")) for n in low):
        return "PyTorch"
    if any(n.endswith(".onnx") for n in low):
        return "ONNX"
    return "other"


def _cfg_quant(cfg):
    q = cfg.get("quantization") if isinstance(cfg, dict) else None
    if isinstance(q, dict) and isinstance(q.get("bits"), int):
        return f"{q['bits']}bit"
    return ""


def _gguf_info(first):
    meta = gguf_meta(first)
    quant = ""
    if meta and isinstance(meta.get("general.file_type"), int):
        quant = GGUF_FILE_TYPES.get(meta["general.file_type"], "")
    return kv_from_gguf(meta), quant


def scan_hf_repo(repo_dir, drive, tally):
    name = repo_of(os.path.basename(repo_dir))
    snaps = os.path.join(repo_dir, "snapshots")
    try:
        snap_dirs = [os.path.join(snaps, s) for s in os.listdir(snaps)]
    except OSError:
        return None
    snap_dirs = [s for s in snap_dirs if os.path.isdir(s)]
    if not name or not snap_dirs:
        return None
    snap = max(snap_dirs, key=lambda s: os.stat(s).st_mtime)
    real_repo = os.path.realpath(repo_dir) + os.sep
    m = _model(name=name, store="Hugging Face", drive=drive, path=repo_dir, trashable=True, match=[norm(name)])
    names, ggufs = [], []
    for dirpath, _, files in os.walk(snap):
        for fn in files:
            p = os.path.join(dirpath, fn)
            before = len(m["inodes"])
            st = tally.add(m["inodes"], p)
            if st is None:
                continue  # a link to a blob that isn't there (yet)
            names.append(fn)
            m["modified"] = max(m["modified"], st.st_mtime)
            if len(m["inodes"]) > before and os.path.realpath(p).startswith(real_repo):
                m["own"] += st.st_size
            if fn.lower().endswith(".gguf") and not fn.lower().startswith("mmproj"):
                ggufs.append(p)
                m["keys"].append(("gguf", fn.lower(), st.st_size))
    m["bytes"] = sum(m["inodes"].values())
    if not any(n.lower().endswith(WEIGHT_EXT) for n in names):
        return None  # only a tokenizer or a few settings files: nothing to run
    try:
        with os.scandir(os.path.join(repo_dir, "blobs")) as it:
            m["partial"] = sum(1 for e in it if e.name.endswith(".incomplete"))
    except OSError:
        pass
    cfg = _read_json(os.path.join(snap, "config.json"))
    m["format"] = _format(names, cfg, name)
    m["kv"] = kv_from_config(cfg)
    quants = sorted({parse_quant(SHARD.sub("", os.path.basename(p))) for p in ggufs} - {""})
    m["quant"] = ", ".join(quants) or parse_quant(name) or _cfg_quant(cfg)
    if ggufs:
        first = sorted(ggufs)[0]
        kv, q = _gguf_info(first)
        m["kv"] = m["kv"] or kv
        m["quant"] = m["quant"] or q
        m["match"] += [norm(p) for p in ggufs]
    else:
        m["keys"].append(("repo", name.lower()))
    return m


def scan_hf_cache(cache, drive, tally):
    out = []
    try:
        entries = sorted(os.listdir(cache))
    except OSError:
        return out
    for e in entries:
        if e.startswith("models--"):
            m = scan_hf_repo(os.path.join(cache, e), drive, tally)
            if m:
                out.append(m)
    return out


def _gguf_models(files, store, drive, tally, label=None, folder=None):
    """One model per .gguf file (split models count once), as LM Studio and llama.cpp see them."""
    out = []
    groups = _shard_groups([f for f in files if not os.path.basename(f).lower().startswith("mmproj")])
    for first, parts in groups.items():
        base = SHARD.sub("", os.path.basename(first))
        m = _model(name=label(base, len(groups)) if label else re.sub(r"\.gguf$", "", base, flags=re.I), store=store,
                   drive=drive, path=first, format="GGUF", match=[norm(first)])
        for p in parts:
            st = tally.add(m["inodes"], p)
            if st:
                m["modified"] = max(m["modified"], st.st_mtime)
                m["keys"].append(("gguf", os.path.basename(p).lower(), st.st_size))
        m["bytes"] = m["own"] = sum(m["inodes"].values())
        if folder and len(groups) == 1 and len(parts) >= 1:
            m["path"], m["trashable"] = folder, True
        elif len(parts) == 1:
            m["trashable"] = True
        kv, q = _gguf_info(first)
        m["kv"], m["quant"] = kv, parse_quant(base) or q
        if m["bytes"]:
            out.append(m)
    return out


def scan_lmstudio(root, tally):
    """LM Studio keeps models as <publisher>/<model>/ folders: .gguf files, or MLX safetensors with a config.json."""
    out, drive = [], drive_of(root)
    try:
        pubs = sorted(os.listdir(root))
    except OSError:
        return out
    for pub in pubs:
        pdir = os.path.join(root, pub)
        if pub.startswith(".") or not os.path.isdir(pdir):
            continue
        for model in sorted(os.listdir(pdir)):
            mdir = os.path.join(pdir, model)
            if model.startswith(".") or not os.path.isdir(mdir):
                continue
            files = []
            for dirpath, _, fns in os.walk(mdir):
                files += [os.path.join(dirpath, f) for f in fns]
            ggufs = [f for f in files if f.lower().endswith(".gguf")]
            repo = f"{pub}/{model}"
            if ggufs:
                out += _gguf_models(ggufs, "LM Studio", drive, tally, folder=mdir,
                                    label=lambda base, n, r=repo: r if n == 1 else f"{r} {parse_quant(base) or base}")
                continue
            if not any(f.lower().endswith(WEIGHT_EXT) for f in files):
                continue
            cfg = _read_json(os.path.join(mdir, "config.json"))
            m = _model(name=repo, store="LM Studio", drive=drive, path=mdir, trashable=True, keys=[("repo", repo.lower())],
                       match=[norm(repo)], kv=kv_from_config(cfg))
            for f in files:
                st = tally.add(m["inodes"], f)
                if st:
                    m["modified"] = max(m["modified"], st.st_mtime)
            m["bytes"] = m["own"] = sum(m["inodes"].values())
            m["format"] = _format([os.path.basename(f) for f in files], cfg, repo)
            m["quant"] = parse_quant(model) or _cfg_quant(cfg)
            out.append(m)
    return out


def scan_ollama(root, tally):
    """Ollama: manifests/<registry>/<namespace>/<model>/<tag> list the blobs (sha256-<hex>) each model uses."""
    out, drive = [], drive_of(root)
    man = os.path.join(root, "manifests")
    for dirpath, _, files in os.walk(man):
        for tag in files:
            path = os.path.join(dirpath, tag)
            parts = os.path.relpath(path, man).split(os.sep)
            data = _read_json(path, limit=2**20)
            if len(parts) != 4 or not isinstance(data, dict):
                continue
            registry, ns, model = parts[:3]
            name = f"{model}:{tag}"
            if ns != "library":
                name = f"{ns}/{name}"
            if registry != "registry.ollama.ai":
                name = f"{registry}/{name}"
            m = _model(name=name, store="Ollama", drive=drive, path=path, format="GGUF", match=[norm(name)])
            try:
                m["modified"] = os.stat(path).st_mtime
            except OSError:
                pass
            weights = None
            layers = list(data.get("layers") or [])
            for layer in layers + [data.get("config") or {}]:
                digest = str(layer.get("digest", ""))
                blob = os.path.join(root, "blobs", digest.replace(":", "-"))
                if not digest or tally.add(m["inodes"], blob) is None:
                    continue
                if str(layer.get("mediaType", "")).endswith("image.model"):
                    weights = blob
            m["bytes"] = sum(m["inodes"].values())
            cfg = _read_json(os.path.join(root, "blobs", str((data.get("config") or {}).get("digest", "")).replace(":", "-")))
            ftype = cfg.get("file_type") if isinstance(cfg, dict) else None
            m["quant"] = ftype if isinstance(ftype, str) else parse_quant(tag)
            if weights:
                kv, q = _gguf_info(weights)
                m["kv"], m["quant"] = kv, m["quant"] or q
            if m["bytes"]:
                out.append(m)
    return out


def scan_loose(folder, label, tally):
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".gguf")]
    except OSError:
        return []
    return _gguf_models([f for f in files if os.path.isfile(f)], label, drive_of(folder), tally)


def find_duplicates(models):
    """Groups of models that are the same thing in two places: [{"members": [indexes], "reclaim": bytes}].

    Same repo in two Hugging Face caches or an LM Studio copy (matched by publisher/name), or the same .gguf file
    (name and size) anywhere. Reclaimable = the group's distinct bytes minus the biggest copy (files shared by
    links aren't counted twice).
    """
    parent = list(range(len(models)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner = {}
    for i, m in enumerate(models):
        for k in m["keys"]:
            if k in owner:
                parent[find(i)] = find(owner[k])
            else:
                owner[k] = i
    groups = {}
    for i in range(len(models)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        paths = {models[i]["path"] for i in members}
        if len(members) < 2 or len(paths) < 2:
            continue
        union = {}
        for i in members:
            union.update(models[i]["inodes"])
        reclaim = max(sum(union.values()) - max(models[i]["bytes"] for i in members), 0)
        out.append({"members": sorted(members, key=lambda i: -models[i]["bytes"]), "reclaim": reclaim})
    out.sort(key=lambda g: -g["reclaim"])
    return out


def scan_all(env=None, home=None, volumes=VOLUMES, downloads=True):
    """Everything, once. Slow on big drives: run it off the collect thread."""
    started = time.time()
    tally = Tally()
    models, places = [], []
    for cache, drive in hf_caches(env, home, volumes):
        found = scan_hf_cache(cache, drive, tally)
        models += found
        places.append({"store": "Hugging Face", "drive": drive, "path": cache, "count": len(found)})
    for root in ollama_roots(env, home):
        found = scan_ollama(root, tally)
        models += found
        places.append({"store": "Ollama", "drive": drive_of(root), "path": root, "count": len(found)})
    for root in lmstudio_roots(home):
        found = scan_lmstudio(root, tally)
        models += found
        places.append({"store": "LM Studio", "drive": drive_of(root), "path": root, "count": len(found)})
    for folder, label in loose_dirs(home, downloads):
        found = scan_loose(folder, label, tally)
        models += found
        if found:
            places.append({"store": label, "drive": drive_of(folder), "path": folder, "count": len(found)})
    models.sort(key=lambda m: -m["bytes"])
    dups = find_duplicates(models)
    for gi, g in enumerate(dups):
        for i in g["members"]:
            models[i]["dup"] = gi
    return {"at": time.time(), "took": round(time.time() - started, 2), "models": models, "duplicates": dups,
            "total": tally.total(), "places": places,
            "trashable": {os.path.realpath(m["path"]) for m in models if m["trashable"]}}


# Will it fit -------------------------------------------------------------------------

def gpu_ceiling(run=core.run):
    """The most memory the GPU may use: iogpu.wired_limit_mb, or about 75% of memory when it's unset (0)."""
    total = int(run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    try:
        limit = int(run(["sysctl", "-n", "iogpu.wired_limit_mb"]).strip() or 0)
    except ValueError:
        limit = 0
    return limit * 2**20 if limit > 0 else int(total * 0.75)


def needs(m):
    """Memory to run a model: the weights plus 10%, plus the context memory for 8K tokens."""
    return int(m["bytes"] * 1.1) + kv_bytes(m.get("kv"), CONTEXTS[0])


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def verdict(need, avail, ceiling, loaded, is_loaded=False):
    """(text, state). `avail` already has the headroom taken off; `loaded` is [(name, bytes)] of running models."""
    if is_loaded:
        return "loaded now", "ok"
    if ceiling and need > ceiling:
        return "too big for this Mac", "bad"
    if need <= avail:
        return "fits now", "ok"
    freed, stop = 0, []
    for name, b in sorted(loaded, key=lambda x: -x[1]):
        freed += b
        stop.append(name)
        if need <= avail + freed:
            return f"fits if you stop {_join(stop)}", "warn"
    return "would push into swap", "bad"


def _rt_bytes(m):
    for k in ("memory_gb", "gb", "mem_gb", "size_gb", "vram_gb", "memory"):
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v * 2**30)
    for k in ("bytes", "memory_bytes", "size", "size_vram"):
        v = m.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return int(v)
    return 0


def running_models(rt):
    """[(name, bytes, match keys, path)] from the AI runtimes widget's data; [] when it has none."""
    out = []
    for m in (rt or {}).get("models") or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "")
        path = str(m.get("path") or "")
        if not name and not path:
            continue
        keys = {norm(m.get(k)) for k in ("name", "model", "api_model", "path") if isinstance(m.get(k), str)} - {""}
        out.append((name or os.path.basename(path), _rt_bytes(m), keys, path))
    return out


def is_running(m, running):
    real = os.path.realpath(m["path"])
    keys = set(m.get("match") or [])
    for _, _, rkeys, rpath in running:
        if keys & rkeys:
            return True
        if rpath and (os.path.realpath(rpath) == real or os.path.realpath(rpath).startswith(real + os.sep)):
            return True
    return False


# The widget --------------------------------------------------------------------------

class AILibrary(Widget):
    id = "ai_library"
    title = "Model library"
    category = "ai"
    description = ("Every model on this Mac and its drives (Hugging Face, Ollama, LM Studio, .gguf files): size, "
                   "copies you could remove, and whether each one fits in memory right now.")
    interval = 120
    delay = 5
    size = "l"
    depends_on = ["ai_runtimes", "memory"]
    settings = [{"key": "headroom_gb", "label": "Memory to keep free for everything else (GB)", "type": "int", "default": 8,
                 "help": "A model only counts as fitting if this much memory is still left over."},
                {"key": "look_in_downloads", "label": "Look for .gguf files in Downloads", "type": "bool", "default": True,
                 "help": "macOS may ask once whether Sightline can see your Downloads folder."}]

    def __init__(self, app):
        super().__init__(app)
        self.lock = threading.Lock()
        self.scan = None
        self.scanning = False

    def available(self):
        return any_location(downloads=bool(self.setting("look_in_downloads")))

    def start_scan(self, force=False):
        with self.lock:
            if self.scanning or (not force and self.scan and time.time() - self.scan["at"] < SCAN_EVERY):
                return False
            self.scanning = True
        threading.Thread(target=self._scan, daemon=True, name="ai_library:scan").start()
        return True

    def _scan(self):
        result = None
        try:
            result = scan_all(downloads=bool(self.setting("look_in_downloads")))
        except Exception:
            traceback.print_exc()
        with self.lock:
            self.scanning = False
            if result:
                self.scan = result
        self.app.poke(self.id)

    def collect(self):
        self.start_scan()
        with self.lock:
            scan, scanning = self.scan, self.scanning
        if not scan:
            return {"summary": "Looking for models…", "scanning": True, "models": [], "duplicates": []}
        headroom = (self.setting("headroom_gb") or 0) * 2**30
        avail = max(available_bytes() - headroom, 0)
        ceiling = gpu_ceiling()
        running = running_models(self.data_of("ai_runtimes"))
        loaded = [(name, b) for name, b, _, _ in running if b]
        models = []
        for i, m in enumerate(scan["models"]):
            up = is_running(m, running)
            need = needs(m)
            text, state = verdict(need, avail, ceiling, loaded, up)
            models.append({
                "i": i, "name": m["name"], "store": m["store"], "drive": m["drive"], "path": m["path"],
                "gb": core.gb(m["bytes"], 2), "frees_gb": core.gb(m["own"], 2), "format": m["format"], "quant": m["quant"],
                "modified": m["modified"], "loaded": up, "need_gb": core.gb(need, 1), "verdict": text, "state": state,
                "context": context_costs(m.get("kv")), "max_tokens": (m.get("kv") or {}).get("max") or None,
                "partial": m["partial"], "dup": m.get("dup"), "can_trash": m["trashable"]})
        dups = [{"name": scan["models"][g["members"][0]]["name"], "members": g["members"], "reclaim_gb": core.gb(g["reclaim"], 1)}
                for g in scan["duplicates"]]
        reclaim = sum(g["reclaim"] for g in scan["duplicates"])
        fits = sum(1 for m in models if m["state"] == "ok")
        self.record("disk_gb", core.gb(scan["total"]))
        note = None
        if any(m["partial"] for m in models):
            note = "Some Hugging Face downloads didn't finish; their unfinished files aren't counted."
        return {
            "summary": f"{len(models)} models · {int(scan['total'] / 2**30)} GB",
            "scanning": scanning, "scanned_at": scan["at"], "scan_took": scan["took"],
            "count": len(models), "total_gb": core.gb(scan["total"], 1), "reclaim_gb": core.gb(reclaim, 1),
            "fit_count": fits, "available_gb": core.gb(avail, 1), "headroom_gb": core.gb(headroom, 0),
            "ceiling_gb": core.gb(ceiling, 0), "models": models, "duplicates": dups,
            "places": scan["places"], "note": note,
        }

    # Actions ---------------------------------------------------------------------

    def actions(self):
        return {"rescan": self.rescan, "trash": self.trash}

    def rescan(self, params):
        started = self.start_scan(force=True)
        return {"ok": True, "message": "Looking for models again…" if started else "Already looking."}

    def trash(self, params, run=subprocess.run):
        """Move ONE model folder or file to the Trash via Finder (it can be put back). Only paths the last scan found."""
        path = str(params.get("path") or "")
        with self.lock:
            allowed = self.scan["trashable"] if self.scan else set()
        real = os.path.realpath(path) if path else ""
        if not path or real not in allowed or real in ("/", os.path.expanduser("~")) or not os.path.exists(real):
            return {"ok": False, "message": "That isn't a model folder from the last scan. Try Look again."}
        script = ['on run argv', 'set target to (POSIX file (item 1 of argv)) as alias',
                  'tell application "Finder" to delete target', 'end run']
        cmd = ["osascript"] + sum((["-e", line] for line in script), []) + [real]
        try:
            p = run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as e:
            return {"ok": False, "message": f"Couldn't move it to the Trash: {e}"}
        if getattr(p, "returncode", 1) != 0:
            return {"ok": False, "message": "Finder couldn't move it to the Trash: " + (p.stderr or "").strip()[:200]}
        with self.lock:
            if self.scan:
                self.scan["trashable"].discard(real)
        self.start_scan(force=True)
        return {"ok": True, "message": f"Moved {os.path.basename(real)} to the Trash. You can put it back from there."}
