"""Containers: Docker, OrbStack or Podman containers, with memory and CPU, and Stop / Start / Restart.

Finds the command-line tool on its own (Homebrew, Docker Desktop, OrbStack, Podman). When the engine isn't
running, the card says so quietly instead of showing errors.
"""

import json
import os
import re

from .. import core
from . import Widget

DOCKER_PLACES = ["/Applications/OrbStack.app/Contents/MacOS/xbin", os.path.expanduser("~/.orbstack/bin"),
                 "/Applications/Docker.app/Contents/Resources/bin", "/opt/podman/bin"]
SIZE = {"b": 1, "kb": 1e3, "kib": 2**10, "mb": 1e6, "mib": 2**20, "gb": 1e9, "gib": 2**30, "tb": 1e12, "tib": 2**40}


def find_cli():
    """(path, kind) of the container tool: docker (Docker Desktop, OrbStack, Colima) or podman."""
    p = core.which("docker", extra=DOCKER_PLACES)
    if p:
        return p, "docker"
    p = core.which("podman", extra=DOCKER_PLACES)
    if p:
        return p, "podman"
    return None, None


def parse_size(text):
    """'12.3MiB' -> bytes."""
    m = re.match(r"\s*([\d.]+)\s*([a-zA-Z]*)", text or "")
    if not m:
        return 0
    return float(m.group(1)) * SIZE.get(m.group(2).lower() or "b", 1)


def parse_pct(text):
    try:
        return float(str(text).strip().rstrip("%"))
    except ValueError:
        return None


def json_lines(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def labels_of(text):
    if isinstance(text, dict):
        return text
    out = {}
    for part in (text or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v
    return out


def short_ports(text):
    """'0.0.0.0:5432->5432/tcp, :::5432->5432/tcp' -> ['5432→5432']; unpublished ports are left out."""
    seen = []
    for m in re.finditer(r"(?:[\d.]+|\[?[:\w]*\]?):(\d+)->(\d+)/(tcp|udp)", text or ""):
        s = m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)}→{m.group(2)}"
        if s not in seen:
            seen.append(s)
    return seen


def parse_ps(text):
    """`docker ps -a --format '{{json .}}'` lines -> containers."""
    out = []
    for c in json_lines(text):
        labels = labels_of(c.get("Labels"))
        out.append({"id": c.get("ID", ""), "name": c.get("Names", ""), "image": c.get("Image", ""),
                    "state": (c.get("State") or "").lower(), "status": c.get("Status", ""), "ports": short_ports(c.get("Ports")),
                    "project": labels.get("com.docker.compose.project", ""), "health": health_of(c)})
    return out


def health_of(c):
    """healthy | unhealthy | starting | '' from the HealthStatus field or the status text."""
    h = (c.get("HealthStatus") or "").lower()
    if h in ("healthy", "unhealthy", "starting"):
        return h
    m = re.search(r"\((healthy|unhealthy|health: starting)\)", c.get("Status") or "")
    return m.group(1).replace("health: ", "") if m else ""


def parse_podman_ps(text):
    """`podman ps -a --format json` (one JSON array) -> containers."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for c in items if isinstance(items, list) else []:
        names = c.get("Names") or []
        ports = []
        for p in c.get("Ports") or []:
            hp, cp = p.get("host_port"), p.get("container_port")
            if hp:
                ports.append(str(hp) if hp == cp else f"{hp}→{cp}")
        out.append({"id": (c.get("Id") or "")[:12], "name": names[0] if names else "", "image": c.get("Image", ""),
                    "state": (c.get("State") or "").lower(), "status": c.get("Status", ""), "ports": ports,
                    "project": (c.get("Labels") or {}).get("com.docker.compose.project", "")})
    return out


def parse_stats(text):
    """`docker stats --no-stream --format '{{json .}}'` lines -> {id or name: {"mem", "cpu"}}."""
    out = {}
    for s in json_lines(text):
        used = (s.get("MemUsage") or "").split("/")[0]
        row = {"mem": int(parse_size(used)), "cpu": parse_pct(s.get("CPUPerc"))}
        for key in (s.get("ID") or s.get("Container"), s.get("Name")):
            if key:
                out[key] = row
    return out


def stats_for(c, stats):
    for key in (c["id"], c["id"][:12], c["name"]):
        if key in stats:
            return stats[key]
    return {}


class Containers(Widget):
    id = "containers"
    title = "Containers"
    category = "developer"
    description = "Docker, OrbStack or Podman containers with their memory and CPU, and Stop, Start and Restart."
    interval = 30
    size = "m"

    def __init__(self, app):
        super().__init__(app)
        self.known = {}  # container id -> name, from the last collect

    def available(self):
        return find_cli()[0] is not None

    def engine_up(self, cli):
        out = core.run([cli, "info", "--format", "{{json .ServerVersion}}"], timeout=3, stderr=False).strip()
        try:
            return isinstance(json.loads(out), str) and bool(json.loads(out))
        except ValueError:
            return False

    def collect(self):
        cli, kind = find_cli()
        if not cli:
            return {"summary": "Not installed", "running": False, "containers": []}
        name = "Podman" if kind == "podman" else "OrbStack" if "orbstack" in os.path.realpath(cli).lower() else "Docker"
        if not self.engine_up(cli):
            self.known = {}
            return {"summary": "Not running", "running": False, "engine": name, "containers": [],
                    "note": f"{name} isn't running."}
        if kind == "podman":
            containers = parse_podman_ps(core.run([cli, "ps", "-a", "--format", "json"], timeout=10, stderr=False))
        else:
            containers = parse_ps(core.run([cli, "ps", "-a", "--format", "{{json .}}"], timeout=10, stderr=False))
        up = [c for c in containers if c["state"] == "running"]
        stats = {}
        if up:
            stats = parse_stats(core.run([cli, "stats", "--no-stream", "--format", "{{json .}}"], timeout=15, stderr=False))
        for c in containers:
            c.update(stats_for(c, stats))
        containers.sort(key=lambda c: (c["state"] != "running", c["project"], c["name"]))
        self.known = {c["id"]: c["name"] for c in containers}
        mem = sum(c.get("mem") or 0 for c in up)
        self.record("running", len(up))
        return {"summary": f"{len(up)} of {len(containers)} running" if containers else "No containers",
                "running": True, "engine": name, "containers": containers, "mem": mem}

    # Actions -----------------------------------------------------------------------

    def actions(self):
        return {"stop": lambda p: self.act("stop", p), "start": lambda p: self.act("start", p),
                "restart": lambda p: self.act("restart", p)}

    def act(self, verb, params):
        cid = str(params.get("id", ""))
        if cid not in self.known:
            return {"ok": False, "message": "That container isn't there any more."}
        cli, _ = find_cli()
        if not cli:
            return {"ok": False, "message": "Docker isn't installed."}
        name = self.known[cid] or cid
        out = core.run([cli, verb, cid], timeout=60, stderr=True).strip()
        ok = cid in out or name in out
        past = {"stop": "Stopped", "start": "Started", "restart": "Restarted"}[verb]
        return {"ok": ok, "message": f"{past} {name}." if ok else (out.splitlines()[-1][:200] if out else f"Couldn't {verb} {name}.")}
