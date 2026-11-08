"""Downloads: model downloads in progress, with speed and time left. No setup.

Hugging Face writes each file as blobs/<hash>.incomplete until it's done (in the repo's folder, or in the shared
blobs/<xx>/ store newer versions use); Ollama writes blobs/sha256-<hex>-partial plus small -partial-N files that say
how much of each piece has arrived. Speed comes from how much the files grew between collects. For Hugging Face,
file sizes come from the cache's own trees/*.json when it has them, else from the Hugging Face API (a setting).
LM Studio isn't covered: how it names unfinished files isn't documented, so this widget doesn't guess.
"""

import collections
import json
import os
import re
import threading
import time
import urllib.parse

from .. import core
from . import Widget
from .ai_library import hf_caches, ollama_roots, repo_of

ACTIVE = 120          # a partial file that changed this recently is downloading
FORGET = 30 * 60      # stop showing a download that hasn't grown for this long
DONE_SHOW = 10 * 60   # keep "finished" (and its notice) this long
WINDOW = 30           # seconds of samples used for the speed
SHARD = re.compile(r"\d{5}-of-(\d{5})")


def _stat(path):
    try:
        return os.stat(path)
    except OSError:
        return None


def _refs_repo(blob):
    """Newer caches keep <hash>.refs next to a shared blob: lines like models--org--name/blobs/<hash>."""
    try:
        with open(blob + ".refs") as f:
            first = f.readline().strip()
    except OSError:
        return None
    return repo_of(first.split("/", 1)[0]) if first else None


def hf_partials(cache):
    """Unfinished Hugging Face files in one cache: [{key, kind, repo, repo_dir, hash, path, bytes, mtime}]."""
    out = []

    def add(path, repo, repo_dir):
        st = _stat(path)
        if st:
            h = os.path.basename(path)[:-len(".incomplete")]
            out.append({"key": path, "kind": "hf", "repo": repo, "repo_dir": repo_dir, "cache": cache, "hash": h,
                        "path": path, "bytes": st.st_size, "mtime": st.st_mtime})

    try:
        entries = os.listdir(cache)
    except OSError:
        return out
    for e in entries:
        if e.startswith("models--"):
            d = os.path.join(cache, e, "blobs")
            try:
                with os.scandir(d) as it:
                    for b in it:
                        if b.name.endswith(".incomplete"):
                            add(b.path, repo_of(e), os.path.join(cache, e))
            except OSError:
                pass
    shared = os.path.join(cache, "blobs")
    try:
        with os.scandir(shared) as it:
            subs = [x.path for x in it if x.is_dir()] + [shared]
    except OSError:
        subs = []
    for sub in subs:
        try:
            with os.scandir(sub) as it:
                for b in it:
                    if b.name.endswith(".incomplete"):
                        final = b.path[:-len(".incomplete")]
                        repo = _refs_repo(final)
                        add(b.path, repo, os.path.join(cache, "models--" + repo.replace("/", "--")) if repo else None)
        except OSError:
            pass
    return out


def ollama_partials(root):
    """Unfinished Ollama blobs: the -partial file, with sizes from its -partial-N piece files."""
    out = []
    blobs = os.path.join(root, "blobs")
    try:
        names = os.listdir(blobs)
    except OSError:
        return out
    for n in names:
        if not n.endswith("-partial"):
            continue
        path = os.path.join(blobs, n)
        st = _stat(path)
        if not st:
            continue
        total = done = 0
        mtime = st.st_mtime
        for piece in (x for x in names if x.startswith(n + "-")):
            p = os.path.join(blobs, piece)
            try:
                with open(p) as f:
                    info = json.load(f)
                total += int(info.get("Size") or 0)
                done += int(info.get("Completed") or 0)
                mtime = max(mtime, os.stat(p).st_mtime)
            except (OSError, ValueError, TypeError, AttributeError):
                continue
        out.append({"key": path, "kind": "ollama", "repo": None, "path": path, "hash": n[:-len("-partial")],
                    "bytes": done if total else st.st_size, "total": total or None, "mtime": mtime})
    return out


def group_of(item):
    """One row per Hugging Face repo, and one for Ollama (it pulls a model's pieces one after another)."""
    return item["repo"] or ("ollama" if item["kind"] == "ollama" else item["path"])


def is_finished(item):
    """The partial file is gone and the finished file is in its place."""
    if item["kind"] == "hf":
        return os.path.exists(item["path"][:-len(".incomplete")])
    return os.path.exists(item["path"][:-len("-partial")])


def local_tree(repo_dir):
    """{file path: (size, sha256)} from the newest trees/<commit>.json a newer Hugging Face cache keeps."""
    d = os.path.join(repo_dir or "", "trees")
    try:
        trees = [os.path.join(d, t) for t in os.listdir(d) if t.endswith(".json")]
    except OSError:
        return {}
    if not trees:
        return {}
    try:
        with open(max(trees, key=os.path.getmtime)) as f:
            files = json.load(f).get("files") or {}
    except (OSError, ValueError, AttributeError):
        return {}
    return {p: (int(v.get("size") or 0), v.get("lfs_sha256") or v.get("blob_id")) for p, v in files.items() if isinstance(v, dict)}


def parse_api_tree(data):
    """The Hugging Face tree API's list -> {file path: (size, sha256)}."""
    out = {}
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and e.get("type") == "file" and e.get("path"):
            lfs = e.get("lfs") or {}
            out[e["path"]] = (int(lfs.get("size") or e.get("size") or 0), lfs.get("oid") or e.get("oid"))
    return out


def snapshot_has(repo_dir, path):
    snaps = os.path.join(repo_dir or "", "snapshots")
    try:
        return any(os.path.exists(os.path.join(snaps, s, path)) for s in os.listdir(snaps))
    except OSError:
        return False


def repo_progress(items, tree, repo_dir):
    """(done, total or None) for one repo's download: files in flight, plus the other parts of a split model."""
    by_hash = {sha: p for p, (_, sha) in tree.items() if sha}
    flying = {}
    for it in items:
        p = by_hash.get(it["hash"])
        if not p:
            return sum(i["bytes"] for i in items), None
        flying[p] = it["bytes"]
    want = set(flying)
    for p in flying:
        m = SHARD.search(p)
        if m:
            pat = SHARD.sub("#", p)
            want |= {q for q in tree if SHARD.search(q) and SHARD.sub("#", q) == pat}
    done = total = 0
    for p in want:
        size = tree[p][0]
        total += size
        if p in flying:
            done += min(flying[p], size)
        elif snapshot_has(repo_dir, p):
            done += size
    return done, total


def speed_of(samples):
    if len(samples) < 2:
        return 0.0
    (t0, b0), (t1, b1) = samples[0], samples[-1]
    return max((b1 - b0) / (t1 - t0), 0.0) if t1 > t0 else 0.0


def ollama_pull_names(args=None):
    """Model names from running `ollama pull <name>` commands."""
    args = core.process_args() if args is None else args
    names = []
    for line in args.values():
        m = re.search(r"(?:^|/)ollama\s+pull\s+(?:-\S+\s+)*(\S+)", line)
        if m:
            names.append(m.group(1))
    return names


class AIDownloads(Widget):
    id = "ai_downloads"
    title = "Downloads"
    category = "ai"
    description = "Model downloads in progress (Hugging Face and Ollama), with speed and time left. Shows up only while something downloads."
    interval = 5
    size = "m"
    settings = [{"key": "ask_hf_for_sizes", "label": "Ask Hugging Face how big a download is", "type": "bool", "default": True,
                 "help": "Only while a download is running, and only when the cache doesn't already know. Sends the model's name to huggingface.co."}]
    alert_rules = [{"key": "download-done", "label": "A model finished downloading"}]

    def __init__(self, app):
        super().__init__(app)
        self.tracked = {}       # partial path -> {item, samples, first, grew}
        self.finished = []      # [{"repo", "name", "at"}]
        self.remote = {}        # repo -> {"files", "at"}
        self.fetching = set()
        self.cache_list = (0, [])
        self.lock = threading.Lock()

    def caches(self):
        at, found = self.cache_list
        if time.time() - at > 300:
            found = [c for c, _ in hf_caches()]
            self.cache_list = (time.time(), found)
        return found

    def partials(self):
        out = []
        for c in self.caches():
            out += hf_partials(c)
        for root in ollama_roots():
            out += ollama_partials(root)
        return out

    recheck = 30  # appear within 30 s of a download starting, and go away once it's done

    def available(self):
        """While something downloads, and for 10 minutes after one finished (so the "done" alert and row can show)."""
        now = time.time()
        if self.tracked or any(now - f.get("at", 0) < 600 for f in self.finished):
            return True
        return any(now - p["mtime"] < ACTIVE for p in self.partials())

    # Hugging Face sizes ----------------------------------------------------------

    def tree_for(self, repo, repo_dir):
        tree = local_tree(repo_dir)
        if tree or not repo:
            return tree
        with self.lock:
            got = self.remote.get(repo)
            busy = repo in self.fetching
        if got and (got["files"] or time.time() - got["at"] < 600):
            return got["files"]
        if not busy and self.setting("ask_hf_for_sizes"):
            with self.lock:
                self.fetching.add(repo)
            threading.Thread(target=self.fetch_tree, args=(repo,), daemon=True, name="ai_downloads:hf").start()
        return {}

    def fetch_tree(self, repo):
        url = f"https://huggingface.co/api/models/{urllib.parse.quote(repo, safe='/')}/tree/main?recursive=true"
        files = parse_api_tree(core.http_json(url, timeout=10))
        with self.lock:
            self.remote[repo] = {"files": files, "at": time.time()}
            self.fetching.discard(repo)

    # Collect ---------------------------------------------------------------------

    def collect(self):
        now = time.time()
        current = {p["key"]: p for p in self.partials()}
        for key, p in current.items():
            t = self.tracked.get(key)
            if t is None:
                if now - p["mtime"] >= ACTIVE:
                    continue  # an old leftover from a download that stopped long ago
                t = self.tracked[key] = {"item": p, "samples": collections.deque(), "first": now, "grew": now}
            if t["samples"] and p["bytes"] > t["samples"][-1][1]:
                t["grew"] = now
            t["item"] = p
            t["samples"].append((now, p["bytes"]))
            while len(t["samples"]) > 2 and now - t["samples"][0][0] > WINDOW:
                t["samples"].popleft()
        pull_names = None
        for key in list(self.tracked):
            t = self.tracked[key]
            if key in current:
                if now - max(t["grew"], t["item"]["mtime"]) > FORGET:
                    del self.tracked[key]
                continue
            del self.tracked[key]
            item = t["item"]
            group = group_of(item)
            if is_finished(item) and not any(group_of(x["item"]) == group for x in self.tracked.values()):
                name = item["repo"] or t.get("name") or "An Ollama model"
                self.finished.append({"repo": item["repo"] or name, "name": name, "at": now})
        self.finished = [f for f in self.finished if now - f["at"] < DONE_SHOW]

        groups = collections.OrderedDict()
        for key, t in sorted(self.tracked.items(), key=lambda kv: kv[1]["first"]):
            it = t["item"]
            groups.setdefault(group_of(it), []).append(t)
        rows = []
        for gkey, ts in groups.items():
            items = [t["item"] for t in ts]
            first = items[0]
            if first["kind"] == "hf":
                tree = self.tree_for(first["repo"], first.get("repo_dir"))
                done, total = repo_progress(items, tree, first.get("repo_dir"))
                name, source = first["repo"] or "A Hugging Face model", "Hugging Face"
            else:
                if pull_names is None:
                    pull_names = ollama_pull_names()
                done = sum(i["bytes"] for i in items)
                total = sum(i["total"] or 0 for i in items) or None
                name = pull_names[0] if len(pull_names) == 1 else "An Ollama model"
                for t in ts:
                    t["name"] = name
                source = "Ollama"
            speed = sum(speed_of(t["samples"]) for t in ts)
            paused = all(now - t["grew"] > ACTIVE for t in ts) and all(len(t["samples"]) > 1 for t in ts)
            eta = (total - done) / speed if total and speed > 0 and done < total else None
            rows.append({"name": name, "source": source, "done_gb": core.gb(done, 2), "total_gb": core.gb(total, 2) if total else None,
                         "pct": round(100.0 * done / total, 1) if total else None, "speed": round(speed), "eta": eta,
                         "files": len(items), "paused": paused})
        speed_all = sum(r["speed"] for r in rows if not r["paused"])
        alerts = [{"key": f"download-done:{f['repo']}", "level": "info", "text": f"{f['name']} finished downloading",
                   "short": f"{f['name'].split('/')[-1]} downloaded"} for f in self.finished]
        if rows:
            summary = f"{len(rows)} downloading" + (f" · {core.human_bytes(speed_all)}/s" if speed_all else "")
        else:
            summary = "Nothing downloading"
        return {"summary": summary, "idle": not rows, "downloads": rows,
                "finished": [{"name": f["name"], "at": f["at"]} for f in reversed(self.finished)], "alerts": alerts}
