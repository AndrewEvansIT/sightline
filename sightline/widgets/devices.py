"""On your network: the devices on this Mac's local network, and a heads-up when a new one joins.

Works with no setup: the subnet comes from the active connection's address and netmask. Small networks
(/24 or smaller) get a quick ping sweep so sleeping devices show up in the ARP table; bigger ones only
read the ARP table. The first sweep counts everything present as known, so day one has no alerts.
"""

import concurrent.futures
import ipaddress
import re
import socket
import struct
import time

from .. import core
from . import Widget
from .network import default_route, parse_netmask, resolver

FORGET_AFTER = 30 * 86400   # offline devices drop off the list after 30 days (they stay known)


# Parsers (pure) ---------------------------------------------------------------------

def norm_mac(mac):
    """'0:11:2:33:44:5' -> '00:11:02:33:44:05'."""
    return ":".join(p.zfill(2) for p in mac.lower().split(":"))


def parse_arp(out, dev=""):
    """[(ip, mac, device, permanent)] from `arp -an`, skipping incomplete, broadcast and multicast entries."""
    rows = []
    for ip, mac, on, rest in re.findall(r"\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-fA-F:]+) on (\S+)(.*)", out or ""):
        mac = norm_mac(mac)
        first = int(ip.split(".")[0])
        if mac == "ff:ff:ff:ff:ff:ff" or mac.startswith("01:00:5e") or 224 <= first <= 239 or ip.endswith(".255"):
            continue
        if dev and on != dev:
            continue
        rows.append((ip, mac, on, "permanent" in rest))
    return rows


def parse_ethers(ifconfig_out):
    """All MAC addresses of this Mac's own interfaces, from plain `ifconfig`."""
    return {norm_mac(m) for m in re.findall(r"^\s+ether ([0-9a-f:]+)", ifconfig_out or "", re.M)}


def sweep_targets(ip, prefix):
    """Addresses to ping: every host on the subnet except this Mac, only for /24 or smaller. [] otherwise."""
    if not ip or prefix is None or prefix < 24 or prefix > 30:
        return []
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return [str(h) for h in net.hosts() if str(h) != ip]


def in_subnet(ip, net):
    try:
        return ipaddress.ip_address(ip) in net
    except ValueError:
        return False


def private_mac(mac):
    """True for a 'private Wi-Fi address' (locally administered: second hex digit is 2, 6, A or E)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except ValueError:
        return False


def short_host(name):
    """Tidy a reverse-DNS name for display: drop .local / .lan / .home and router-added domains."""
    if not name:
        return ""
    for suffix in (".local", ".lan", ".home", ".localdomain", ".home.arpa", ".router", ".gateway", ".attlocal.net"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name.split(".")[0] if re.search(r"\.(hsd1|comcast|xfinity|fios|verizon|frontier|spectrum)", name) else name


def mdns_query(ip, qid):
    """A legacy-unicast mDNS question: 'which name has this address?' (PTR for d.c.b.a.in-addr.arpa)."""
    labels = list(reversed(ip.split("."))) + ["in-addr", "arpa"]
    qname = b"".join(bytes([len(l)]) + l.encode() for l in labels) + b"\0"
    return struct.pack(">HHHHHH", qid, 0, 1, 0, 0, 0) + qname + struct.pack(">HH", 12, 1)


def _dns_name(data, pos, depth=0):
    """(name, next position) at `pos`, following compression pointers."""
    labels, jumped, end = [], False, pos
    while depth < 20:
        n = data[pos]
        if n == 0:
            pos += 1
            break
        if n & 0xC0 == 0xC0:
            if not jumped:
                end = pos + 2
            pos, jumped, depth = ((n & 0x3F) << 8) | data[pos + 1], True, depth + 1
            continue
        labels.append(data[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n
    return ".".join(labels), (end if jumped else pos)


def parse_mdns_ptr(data):
    """{ip: name} from the PTR answers in an mDNS response packet. Malformed packets give {}."""
    out = {}
    try:
        _, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
        pos = 12
        for _ in range(qd):
            pos = _dns_name(data, pos)[1] + 4
        for _ in range(an + ns + ar):
            owner, pos = _dns_name(data, pos)
            rtype, _, _, rdlen = struct.unpack(">HHIH", data[pos:pos + 10])
            pos += 10
            if rtype == 12 and owner.endswith(".in-addr.arpa"):
                parts = owner[: -len(".in-addr.arpa")].split(".")
                if len(parts) == 4:
                    out[".".join(reversed(parts))] = _dns_name(data, pos)[0]
            pos += rdlen
    except (struct.error, IndexError):
        return out
    return out


def mdns_names(ips, local_ip="", timeout=1.5):
    """Ask the local network (multicast DNS, 224.0.0.251) for each address's name. Apple devices and many
    printers answer with their .local name. Local network only; {} when nothing answers."""
    if not ips:
        return {}
    found = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            if local_ip:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local_ip))
            s.settimeout(0.25)
            for i, ip in enumerate(ips):
                s.sendto(mdns_query(ip, i + 1), ("224.0.0.251", 5353))
            deadline = time.time() + timeout
            while time.time() < deadline and len(found) < len(ips):
                try:
                    data, _ = s.recvfrom(9000)
                except socket.timeout:
                    continue
                found.update({k: v for k, v in parse_mdns_ptr(data).items() if k in ips})
    except OSError:
        pass
    return found


def merge_known(known, seen, now, first_sweep):
    """Update the known-devices map with this sweep. Returns (known, new_macs).

    `seen` is mac -> {"ip", "name"}. On the first sweep everything present is known; later, an unseen MAC is
    added with "new": True until the person marks it known.
    """
    new = []
    for mac, s in seen.items():
        k = known.get(mac)
        if k is None:
            k = known[mac] = {"first_seen": now, "new": not first_sweep}
            if not first_sweep:
                new.append(mac)
        elif k.get("new"):
            new.append(mac)
        k["last_seen"] = now
        k["ip"] = s["ip"]
        if s.get("name"):
            k["auto_name"] = s["name"]
    return known, new


def display_name(k, ip="", router="", me=False):
    if me:
        return "This Mac"
    if k.get("name"):
        return k["name"]
    if router and ip == router:
        return "Router"
    return k.get("auto_name") or "Unknown device"


# The widget -----------------------------------------------------------------------

class Devices(Widget):
    id = "devices"
    title = "On your network"
    category = "network"
    description = "The devices on your local network, and a heads-up when a new one joins."
    interval = 600
    delay = 5
    size = "m"
    alert_rules = [{"key": "new-device", "label": "A new device joined your network"}]

    def __init__(self, app):
        super().__init__(app)
        self.last = None

    def available(self):
        return bool(default_route()["interface"])

    def nudge_permission(self, router):
        """An in-process connection to the router makes macOS ask for Local Network access for this app.
        Until it's allowed, ping and arp (run as child processes) can't see the network."""
        if not router or not re.match(r"\d+\.\d+\.\d+\.\d+$", router):
            return
        try:
            with socket.socket() as s:
                s.settimeout(1)
                s.connect_ex((router, 80))
        except OSError:
            pass

    def sweep(self, targets):
        with concurrent.futures.ThreadPoolExecutor(32) as pool:
            list(pool.map(lambda ip: core.run(["ping", "-c", "1", "-t", "1", ip], timeout=3), targets))

    def scan(self, dev, net, targets, router, local_ip=""):
        """mac -> {"ip", "name"} for every other device on the subnet."""
        self.nudge_permission(router)
        if targets:
            self.sweep(targets)
        mine = parse_ethers(core.run(["ifconfig"], timeout=5))
        seen = {}
        for rip, mac, _, permanent in parse_arp(core.run(["arp", "-an"], timeout=10), dev):
            if in_subnet(rip, net) and not permanent and mac not in mine:
                seen.setdefault(mac, {"ip": rip})
        ips = [s["ip"] for s in seen.values()]
        names = mdns_names(ips, local_ip)
        rest = [ip for ip in ips if ip not in names]
        names.update({ip: n for ip, n in resolver().wait(rest, timeout=1.5).items() if n})
        for s in seen.values():
            s["name"] = short_host(names.get(s["ip"], ""))
        return seen

    def collect(self):
        now = time.time()
        route = default_route()
        dev, router = route["interface"], route["gateway"]
        ip, prefix = parse_netmask(core.run(["ifconfig", dev], timeout=5)) if dev else (None, None)
        if not ip:
            return {"summary": "Not connected", "state": "off", "devices": [], "note": "This Mac isn't on a local network right now."}
        net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        targets = sweep_targets(ip, prefix)
        # A rename or mark-as-known collects again; reuse a sweep from the last two minutes instead of pinging again.
        if self.last and now - self.last[0] < 120 and self.last[1] == (dev, str(net)):
            seen = self.last[2]
        else:
            seen = self.scan(dev, net, targets, router, ip)
            self.last = (now, (dev, str(net)), seen)

        known = self.state.get("devices.known")
        first = known is None
        known = known or {}
        known, new = merge_known(known, seen, now, first and bool(seen))
        if seen or not first:
            self.state.set("devices.known", known)

        out = [{"mac": "", "ip": ip, "name": "This Mac", "online": True, "me": True}]
        for mac, k in known.items():
            online = mac in seen
            if not online and (k.get("new") or now - k.get("last_seen", 0) > FORGET_AFTER):
                continue
            kip = seen[mac]["ip"] if online else k.get("ip", "")
            out.append({"mac": mac, "ip": kip, "name": display_name(k, kip, router), "online": online,
                        "new": bool(k.get("new")), "renamed": bool(k.get("name")), "private": private_mac(mac),
                        "router": kip == router, "last_seen": k.get("last_seen"), "first_seen": k.get("first_seen")})

        def order(d):
            if d.get("me"):
                return (0, 0)
            try:
                last = int(d["ip"].split(".")[-1])
            except (ValueError, IndexError, AttributeError):
                last = 999
            return (1 if d["online"] else 2, last)
        out.sort(key=order)

        alerts = []
        for mac in new:
            if mac in seen:
                k = known[mac]
                who = k.get("name") or k.get("auto_name") or seen[mac]["ip"]
                alerts.append({"key": f"new-device:{mac}", "level": "warn",
                               "text": f"A new device joined your network: {who}", "short": f"New device: {who}"})
        online = sum(1 for d in out if d["online"])
        lonely = len(seen) == 0
        note = ("iPhones, iPads and Macs use private Wi-Fi addresses that can change, so a familiar device can show up as new.")
        if lonely:
            note = ("No other devices found. If you expect some, allow Local Network access for Sightline in System Settings "
                    "› Privacy & Security › Local Network. " + note)
        return {
            "summary": f"{online} online" + (f" · {len(new)} new" if new else ""),
            "state": "warn" if new else "ok",
            "subnet": str(net), "swept": bool(targets), "router": router, "interface": dev,
            "devices": out, "new_count": len(new),
            "note": note if targets else "Your network is big, so Sightline only lists devices this Mac has talked to recently. " + note,
            "alerts": alerts,
        }

    # Actions -----------------------------------------------------------------------

    def actions(self):
        return {"rename": self.rename, "forget": self.forget, "mark_known": self.mark_known}

    def _update(self, mac, fn):
        mac = norm_mac(str(mac or ""))
        known = self.state.get("devices.known") or {}
        if mac not in known:
            return None
        fn(known[mac])
        self.state.set("devices.known", known)
        return known[mac]

    def rename(self, params):
        name = str(params.get("name") or "").strip()[:60]

        def apply(k):
            if name:
                k["name"] = name
            else:
                k.pop("name", None)
            k["new"] = False
        k = self._update(params.get("mac"), apply)
        if k is None:
            return {"ok": False, "message": "That device isn't in the list any more."}
        return {"ok": True, "message": f"Renamed to {name}." if name else "Name cleared."}

    def mark_known(self, params):
        k = self._update(params.get("mac"), lambda k: k.update(new=False))
        if k is None:
            return {"ok": False, "message": "That device isn't in the list any more."}
        return {"ok": True, "message": "Marked as known."}

    def forget(self, params):
        mac = norm_mac(str(params.get("mac") or ""))
        known = self.state.get("devices.known") or {}
        if known.pop(mac, None) is None:
            return {"ok": False, "message": "That device isn't in the list any more."}
        self.state.set("devices.known", known)
        return {"ok": True, "message": "Forgotten. If it comes back, it will show up as new."}
