"""Repos: git repositories with loose ends (uncommitted changes, commits not pushed, stashes).

Read-only. Every git call uses --no-optional-locks so it never rewrites the index while you work, and nothing
here fetches: ahead/behind is against what git already knows about the upstream.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .. import core
from . import Widget

COMMON_ROOTS = ("~/Developer", "~/Projects", "~/projects", "~/code", "~/Code", "~/src", "~/repos", "~/GitHub", "~/dev",
                "~/workspace", "~/Documents/GitHub")
SKIP = {"node_modules", ".build", "vendor", "build", "dist", "Pods", "DerivedData", "venv", "__pycache__", "target"}
MAX_REPOS = 200
MAX_DEPTH = 3
GIT_TIMEOUT = 10


def find_repos(roots, max_depth=MAX_DEPTH, cap=MAX_REPOS):
    """Folders holding a .git (folder or file), at most max_depth below each root. Doesn't look inside a repo."""
    found, seen = [], set()

    def walk(folder, depth):
        if len(found) >= cap:
            return
        try:
            real = os.path.realpath(folder)
        except OSError:
            return
        if real in seen:
            return
        seen.add(real)
        if os.path.exists(os.path.join(folder, ".git")):
            found.append(folder)
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            if e.name.startswith(".") or e.name in SKIP:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    walk(e.path, depth + 1)
            except OSError:
                continue

    for root in roots:
        if os.path.isdir(root):
            walk(root, 0)
    return found[:cap]


def parse_status(text):
    """`git status --porcelain=v1 -b` -> branch, upstream, ahead, behind, changed, untracked."""
    out = {"branch": "", "upstream": "", "ahead": 0, "behind": 0, "changed": 0, "untracked": 0, "detached": False}
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:]
            m = re.match(r"(?:No commits yet on|Initial commit on) (.+)$", head)
            if m:
                out["branch"] = m.group(1).strip()
                continue
            if head.startswith("HEAD (no branch)"):
                out["branch"], out["detached"] = "detached", True
                continue
            m = re.match(r"(.+?)(?:\.\.\.(\S+))?(?: \[(.+)\])?$", head)
            if m:
                out["branch"] = m.group(1)
                out["upstream"] = m.group(2) or ""
                info = m.group(3) or ""
                a = re.search(r"ahead (\d+)", info)
                b = re.search(r"behind (\d+)", info)
                out["ahead"] = int(a.group(1)) if a else 0
                out["behind"] = int(b.group(1)) if b else 0
                if "gone" in info:
                    out["upstream_gone"] = True
        elif line.startswith("?? "):
            out["untracked"] += 1
        elif len(line) > 3 and not line.startswith("!! "):
            out["changed"] += 1
    return out


def git(path, *args, timeout=GIT_TIMEOUT):
    return core.run(["git", "--no-optional-locks", "-C", path] + list(args), timeout=timeout, stderr=False)


def inspect(path, now=None):
    """Everything we show about one repo. Read-only git calls only."""
    # Status can be slow in a big working tree: without writing the index, git re-checks changed files each time.
    text = git(path, "status", "--porcelain=v1", "-b", timeout=2 * GIT_TIMEOUT)
    st = parse_status(text)
    st["unchecked"] = not text.startswith("## ")
    last = git(path, "log", "-1", "--format=%ct").strip()
    stashes = len([l for l in git(path, "stash", "list").splitlines() if l.strip()])
    remotes = [r for r in git(path, "remote").splitlines() if r.strip()]
    unpushed = 0
    if not st["upstream"] and not st["detached"] and remotes and last:
        try:
            unpushed = int(git(path, "rev-list", "--count", "HEAD", "--not", "--remotes").strip() or 0)
        except ValueError:
            unpushed = 0
    st.update(name=os.path.basename(path.rstrip("/")), path=path, stashes=stashes, remote=bool(remotes), unpushed=unpushed,
              last_commit=int(last) if last.isdigit() else None)
    st["loose"] = bool(st["changed"] or st["untracked"] or st["ahead"] or unpushed or stashes)
    return st


class Repos(Widget):
    id = "repos"
    title = "Repos"
    category = "developer"
    description = "Git repos with loose ends: uncommitted changes, commits not pushed yet, stashes. Never fetches or writes."
    interval = 300
    delay = 5
    size = "m"
    settings = [
        {"key": "roots", "label": "Extra folders to look in", "type": "list", "default": [],
         "help": "Sightline already looks in ~/Developer, ~/Projects, ~/code, ~/src and other common folders."},
        {"key": "show_clean", "label": "Also show repos with nothing to do", "type": "bool", "default": False},
    ]

    def roots(self):
        extra = self.setting("roots") or []
        if isinstance(extra, str):
            extra = [extra]
        out = []
        for r in list(COMMON_ROOTS) + list(extra):
            p = os.path.expanduser(str(r).strip())
            if p and os.path.isdir(p) and p not in out:
                out.append(p)
        return out

    def available(self):
        return bool(core.which("git")) and bool(self.roots())

    def collect(self):
        roots = self.roots()
        paths = find_repos(roots)
        with ThreadPoolExecutor(max_workers=6) as pool:
            repos = list(pool.map(inspect, paths))
        loose = [r for r in repos if r["loose"]]
        slow = [r for r in repos if r["unchecked"]]
        shown = repos if self.setting("show_clean") else [r for r in repos if r["loose"] or r["unchecked"]]
        shown.sort(key=lambda r: (not r["loose"], r["unchecked"], -(r["last_commit"] or 0)))
        self.record("loose", len(loose))
        return {
            "summary": (f"{len(loose)} with loose ends" if loose else
                        f"{len(slow)} too slow to check" if slow else f"All {len(repos)} clean"),
            "repos": shown,
            "count": len(repos),
            "loose": len(loose),
            "unchecked": len(slow),
            "capped": len(paths) >= MAX_REPOS,
            "roots": [r.replace(os.path.expanduser("~"), "~", 1) for r in roots],
            "now": time.time(),
        }
