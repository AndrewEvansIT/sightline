"""Who's online: which apps are talking to the internet right now, and what's open to your network.

Reads `lsof` without admin rights, so it sees your own processes only (macOS hides other users' and
system processes). Remote addresses get names by reverse DNS on background threads, cached.
"""

import ipaddress
import os
import re
import time

from .. import core
from . import Widget
from .network import is_lan, resolver

SYSTEM_DIRS = ("/System/", "/usr/", "/sbin/", "/bin/", "/Library/Apple/", "/private/", "/Library/Developer/CoreSimulator/")
FRIENDLY = {
    "ControlCenter": "Control Center (AirPlay Receiver)", "rapportd": "Handoff and Continuity", "sharingd": "AirDrop and sharing",
    "identityservicesd": "iMessage and FaceTime", "UniversalControl": "Universal Control", "apsd": "Apple push notifications",
    "homed": "Home", "com.docker.backend": "Docker", "mDNSResponder": "Bonjour", "remoted": "Remote device connections",
    "screensharingd": "Screen Sharing", "launchd_sim": "iOS Simulator",
}
KNOWN_DOMAINS = {
    "1e100.net": "Google", "googleusercontent.com": "Google", "google.com": "Google", "amazonaws.com": "Amazon Web Services",
    "cloudfront.net": "Amazon CloudFront", "akamaitechnologies.com": "Akamai", "akamaiedge.net": "Akamai",
    "apple.com": "Apple", "aaplimg.com": "Apple", "icloud.com": "Apple", "cloudflare.com": "Cloudflare",
    "github.com": "GitHub", "fastly.net": "Fastly", "msedge.net": "Microsoft", "azure.com": "Microsoft",
    "slack.com": "Slack", "slack-edge.com": "Slack", "anthropic.com": "Anthropic", "openai.com": "OpenAI",
}
SECOND_LEVEL = ("co", "com", "net", "org", "ac", "gov", "edu")


# Parsers (pure) ---------------------------------------------------------------------

def parse_lsof(out):
    """[{"pid", "command", "uid", "proto", "name", "state"}] from `lsof -F pcunPT` output."""
    rows, proc, cur = [], {}, None
    for line in (out or "").splitlines():
        if not line:
            continue
        tag, val = line[0], line[1:]
        if tag == "p":
            proc, cur = {"pid": val, "command": "", "uid": ""}, None
        elif tag == "c":
            proc["command"] = val
        elif tag == "u":
            proc["uid"] = val
        elif tag == "f":
            cur = dict(proc, proto="", name="", state="")
            rows.append(cur)
        elif cur is not None:
            if tag == "P":
                cur["proto"] = val
            elif tag == "n":
                cur["name"] = val
            elif tag == "T" and val.startswith("ST="):
                cur["state"] = val[3:]
    return [r for r in rows if r["name"]]


def split_addr(s):
    """'[::1]:80' -> ('::1', '80'); '10.0.0.2:443' -> ('10.0.0.2', '443'); '*:*' -> ('*', '*')."""
    m = re.match(r"^\[(.+)\]:(\S+)$", s) or re.match(r"^([^:\[\]]+):(\S+)$", s)
    return (m.group(1), m.group(2)) if m else (s, "")


def addr_kind(ip):
    """loopback | link-local | multicast | lan | internet | unknown."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return "unknown"
    mapped = getattr(a, "ipv4_mapped", None)
    if mapped:
        a = mapped
    if a.is_loopback:
        return "loopback"
    if a.is_link_local:
        return "link-local"
    if a.is_multicast:
        return "multicast"
    if a.is_unspecified:
        return "unknown"
    return "lan" if is_lan(str(a)) else "internet"


def connections(rows):
    """Connected sockets with a remote address worth showing: [(row, remote ip, remote port, kind)]."""
    out = []
    for r in rows:
        if "->" not in r["name"]:
            continue
        local, remote = r["name"].split("->", 1)
        ip, port = split_addr(remote.split(" ")[0])
        kind = addr_kind(ip)
        if kind in ("internet", "lan"):
            out.append((r, ip, port, kind))
    return out


def listeners(rows):
    """Bound sockets: [(row, address, port, exposed)] where exposed means every interface or a non-loopback address."""
    out = []
    for r in rows:
        if "->" in r["name"]:
            continue
        if r["proto"] == "TCP" and r["state"] and r["state"] != "LISTEN":
            continue
        ip, port = split_addr(r["name"])
        if not port or port == "*":
            continue
        if ip in ("*", "0.0.0.0", "::", "[::]"):
            exposed = True
        else:
            kind = addr_kind(ip)
            if kind == "multicast":
                continue
            exposed = kind != "loopback"
        out.append((r, ip, port, exposed))
    return out


def short_domain(host):
    """'lax17s55-in-f14.1e100.net' -> ('1e100.net', 'Google'); unknown names keep their last two labels."""
    host = (host or "").rstrip(".").lower()
    parts = host.split(".")
    if len(parts) < 2:
        return host, ""
    base = ".".join(parts[-3:]) if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2 else ".".join(parts[-2:])
    return base, KNOWN_DOMAINS.get(base, "")


def app_of(pid, procs, me):
    """(name, kind) for a process: its .app bundle (helpers live inside their app's bundle), else a friendly or
    command name. kind: app | macos | process. No climbing to parents: a tool started from Terminal is not Terminal."""
    ppid, uid, path = procs.get(pid, ("", "", ""))
    if not path:
        return None, "process"
    base = os.path.basename(path)
    if "CoreSimulator" in path or base == "launchd_sim":
        return "iOS Simulator", "macos"
    m = re.match(r"(/.*?/([^/]+)\.app)/", path)
    if m:
        bundle, name = m.group(1), m.group(2)
        if not bundle.startswith(SYSTEM_DIRS) or bundle.startswith("/System/Applications/"):
            return name, "app"
        return FRIENDLY.get(base, FRIENDLY.get(name, name)), "macos"
    if path.startswith(SYSTEM_DIRS) or (uid and uid != me):
        return FRIENDLY.get(base, base), "macos"
    return FRIENDLY.get(base, base), "process"


def group_talkers(conns, procs, me, names=None, top_hosts=6):
    """Apps by connection count, each with its top remote hosts."""
    names = names or {}
    apps = {}
    for r, ip, port, kind in conns:
        name, akind = app_of(r["pid"], procs, me)
        name = name or FRIENDLY.get(r["command"], r["command"])
        a = apps.setdefault(name, {"name": name, "kind": akind, "count": 0, "hosts": {}, "pids": set()})
        a["count"] += 1
        a["pids"].add(r["pid"])
        if kind == "lan":
            key, label, full = ip, "local network", names.get(ip, "")
        else:
            full = names.get(ip, "")
            base, owner = short_domain(full) if full else ("", "")
            key = base or ip
            label = owner
        h = a["hosts"].setdefault(key, {"host": key, "owner": label, "ips": set(), "names": set(), "count": 0, "local": kind == "lan"})
        h["count"] += 1
        h["ips"].add(ip)
        if full:
            h["names"].add(full)
    out = []
    for a in sorted(apps.values(), key=lambda a: (-a["count"], a["name"].lower())):
        hosts = sorted(a["hosts"].values(), key=lambda h: (-h["count"], h["host"]))
        out.append({"name": a["name"], "kind": a["kind"], "count": a["count"], "processes": len(a["pids"]),
                    "host_count": len(hosts),
                    "hosts": [{"host": h["host"], "owner": h["owner"], "count": h["count"], "local": h["local"],
                               "ips": sorted(h["ips"])[:4], "names": sorted(h["names"])[:4]} for h in hosts[:top_hosts]]})
    return out


def group_listeners(found, procs, me):
    """One row per app: its ports, and whether any is open to the network."""
    apps = {}
    for r, ip, port, exposed in found:
        name, kind = app_of(r["pid"], procs, me)
        name = name or FRIENDLY.get(r["command"], r["command"])
        a = apps.setdefault(name, {"name": name, "kind": kind, "ports": {}})
        key = (r["proto"], port)
        p = a["ports"].setdefault(key, {"proto": r["proto"], "port": int(port) if port.isdigit() else port,
                                        "exposed": False, "addresses": set()})
        p["exposed"] = p["exposed"] or exposed
        p["addresses"].add("all" if ip in ("*", "0.0.0.0", "::") else ip)
    out = []
    for a in apps.values():
        ports = sorted(a["ports"].values(), key=lambda p: (not p["exposed"], p["proto"] != "TCP", str(p["port"]).zfill(6)))
        out.append({"name": a["name"], "kind": a["kind"], "exposed": any(p["exposed"] for p in ports),
                    "ports": [{"proto": p["proto"], "port": p["port"], "exposed": p["exposed"],
                               "where": "every network" if "all" in p["addresses"] else
                               ("this Mac only" if not p["exposed"] else ", ".join(sorted(p["addresses"])))} for p in ports]})
    out.sort(key=lambda a: (not a["exposed"], a["kind"] == "macos", a["name"].lower()))
    return out


# The widget -----------------------------------------------------------------------

class Connections(Widget):
    id = "connections"
    title = "Who's online"
    category = "security"
    description = "Which apps are talking to the internet right now, and which are open to your network."
    interval = 30
    delay = 4
    size = "l"
    settings = [
        {"key": "resolve_names", "label": "Look up names for addresses", "type": "bool", "default": True,
         "help": "Asks your DNS server which name belongs to each address (reverse DNS), so you see google.com instead of numbers."},
        {"key": "show_udp", "label": "Include UDP", "type": "bool", "default": True,
         "help": "Also list UDP sockets (calls, streaming, AirDrop and other quick traffic)."},
        {"key": "alert_exposed", "label": "Tell me when an app opens a port to the network", "type": "bool", "default": False},
    ]
    alert_rules = [{"key": "exposed-port", "label": "An app opened a port to your network"}]

    def available(self):
        return bool(core.which("lsof"))

    def collect(self):
        now = time.time()
        me = str(os.getuid())
        udp = self.setting("show_udp")
        est = parse_lsof(core.run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED", "-FpcunPT"], timeout=15, stderr=False))
        lis = parse_lsof(core.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-FpcunPT"], timeout=15, stderr=False))
        udp_rows = parse_lsof(core.run(["lsof", "-nP", "-iUDP", "-FpcunPT"], timeout=15, stderr=False)) if udp else []
        procs = core.processes()

        conns = connections(est + udp_rows)
        names = {}
        if self.setting("resolve_names"):
            res = resolver()
            names = {ip: res.name(ip) for ip in {c[1] for c in conns if c[3] == "internet"}}
        talkers = group_talkers(conns, procs, me, names)
        open_ports = group_listeners(listeners(lis + udp_rows), procs, me)

        # New ports open to every network (baseline on the first run, so nothing alerts on day one).
        seen = self.state.get("connections.exposed")
        current = {f"{a['name']}:{p['port']}" for a in open_ports for p in a["ports"] if p["exposed"] and p["proto"] == "TCP"}
        first = seen is None
        seen = seen or {}
        for key in current:
            seen.setdefault(key, 0 if first else now)
        if first or any(seen[k] == now for k in current):
            self.state.set("connections.exposed", seen)
        fresh = {k for k in current if seen.get(k) and now - seen[k] < 86400}
        for a in open_ports:
            for p in a["ports"]:
                p["new"] = f"{a['name']}:{p['port']}" in fresh and p["proto"] == "TCP"
        alerts = []
        if self.setting("alert_exposed"):
            for key in sorted(fresh):
                app, port = key.rsplit(":", 1)
                alerts.append({"key": f"exposed-port:{key}", "level": "info",
                               "text": f"{app} is accepting connections from your network on port {port}.",
                               "short": f"{app} open on port {port}"})

        internet = sum(1 for c in conns if c[3] == "internet")
        exposed_apps = sum(1 for a in open_ports if a["exposed"])
        return {
            "summary": f"{len(talkers)} app{'s' if len(talkers) != 1 else ''} online · {exposed_apps} open to network",
            "talkers": talkers[:12], "talker_count": len(talkers), "connection_count": len(conns), "internet_count": internet,
            "listening": open_ports, "exposed_count": exposed_apps,
            "note": "Shows your own apps only: without admin rights, macOS hides other users' and system processes.",
            "alerts": alerts,
        }
