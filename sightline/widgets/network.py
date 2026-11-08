"""Network: is the internet up, how fast, and how this Mac is connected (Wi-Fi, Ethernet, VPN, DNS).

Network calls: a ping to 1.1.1.1, Apple's networkQuality speed test, and Cloudflare's trace page on
1.1.1.1 for your public IP. Each one has a setting. The shared helpers here (default route, hardware
ports, reverse DNS) are also used by the devices and connections widgets.
"""

import concurrent.futures
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.request

from .. import core
from . import Widget

WELL_KNOWN_DNS = {
    "1.1.1.1": "Cloudflare", "1.0.0.1": "Cloudflare", "2606:4700:4700::1111": "Cloudflare", "2606:4700:4700::1001": "Cloudflare",
    "8.8.8.8": "Google", "8.8.4.4": "Google", "2001:4860:4860::8888": "Google", "2001:4860:4860::8844": "Google",
    "9.9.9.9": "Quad9", "149.112.112.112": "Quad9", "2620:fe::fe": "Quad9", "2620:fe::9": "Quad9",
    "208.67.222.222": "OpenDNS", "208.67.220.220": "OpenDNS",
}
VPN_PREFIXES = ("utun", "ipsec", "ppp", "tun", "tap", "wg")
LAN_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")]


# Parsers (pure) ---------------------------------------------------------------------

def parse_ping(out):
    """(average ms or None, loss %) from `ping -q` output."""
    loss = re.search(r"([\d.]+)% packet loss", out or "")
    avg = re.search(r"= [\d.]+/([\d.]+)/", out or "")
    return (round(float(avg.group(1)), 1) if avg else None), (float(loss.group(1)) if loss else 100.0)


def parse_route(out):
    """{"gateway", "interface"} from `route -n get <dest>`."""
    gw = re.search(r"^\s*gateway:\s*(\S+)", out or "", re.M)
    dev = re.search(r"^\s*interface:\s*(\S+)", out or "", re.M)
    return {"gateway": gw.group(1) if gw else "", "interface": dev.group(1) if dev else ""}


def parse_hardware_ports(out):
    """device -> hardware port name, from `networksetup -listallhardwareports`."""
    return {dev: name.strip() for name, dev in re.findall(r"Hardware Port: (.+)\nDevice: (\S+)", out or "")}


def connection_type(dev, ports):
    """Wi-Fi | Ethernet | Thunderbolt | iPhone | VPN | Other, from the device name and its hardware port."""
    if dev.startswith(VPN_PREFIXES):
        return "VPN"
    port = ports.get(dev, "")
    if port == "Wi-Fi" or "AirPort" in port:
        return "Wi-Fi"
    if "iPhone" in port or "iPad" in port:
        return "iPhone"
    if "Thunderbolt" in port and "Ethernet" not in port:
        return "Thunderbolt"
    if "Ethernet" in port or "LAN" in port or "USB" in port:
        return "Ethernet"
    if dev.startswith("bridge"):
        return "Thunderbolt"
    return "Other" if port else "Unknown"


def parse_dns(out):
    """Nameservers of the first resolver in `scutil --dns` (the ones normal lookups use)."""
    block = re.split(r"\nresolver #2\b|\nDNS configuration \(for scoped", out or "")[0]
    return re.findall(r"nameserver\[\d+\]\s*:\s*(\S+)", block)


def dns_label(server, router=""):
    if server in WELL_KNOWN_DNS:
        return WELL_KNOWN_DNS[server]
    if router and server == router:
        return "your router"
    if is_lan(server):
        return "local network"
    return ""


def parse_vpn_services(out):
    """Names of connected VPN services in `scutil --nc list`."""
    return re.findall(r"^\*?\s*\(Connected\).*?\"(.+?)\"", out or "", re.M)


def parse_trace(text):
    """key -> value from Cloudflare's /cdn-cgi/trace."""
    return dict(l.split("=", 1) for l in (text or "").splitlines() if "=" in l)


def parse_speed(text):
    """{"down", "up" (Mbps), "rpm", "rtt_ms"} from `networkQuality -c`, or None."""
    try:
        d = json.loads(text)
        out = {"down": round(d["dl_throughput"] / 1e6), "up": round(d["ul_throughput"] / 1e6)}
    except (ValueError, KeyError, TypeError):
        return None
    if d.get("responsiveness"):
        out["rpm"] = round(d["responsiveness"])
    if d.get("base_rtt"):
        out["rtt_ms"] = round(d["base_rtt"])
    return out


def wifi_generation(phy, band=""):
    if "be" in phy:
        return "Wi-Fi 7"
    if "ax" in phy:
        return "Wi-Fi 6E" if band.startswith("6") else "Wi-Fi 6"
    if "ac" in phy:
        return "Wi-Fi 5"
    if "n" in phy:
        return "Wi-Fi 4"
    return ""


def parse_wifi(data, dev=""):
    """Current Wi-Fi network from `system_profiler SPAirPortDataType -json`, or None when not connected."""
    try:
        ifaces = data["SPAirPortDataType"][0]["spairport_airport_interfaces"]
    except (KeyError, IndexError, TypeError):
        return None
    for i in ifaces:
        if dev and i.get("_name") != dev:
            continue
        cur = i.get("spairport_current_network_information") or {}
        if "spairport_network_channel" not in cur:
            continue
        out = {"device": i.get("_name", "")}
        name = cur.get("_name", "")
        if name and name != "<redacted>":
            out["ssid"] = name
        m = re.match(r"(\d+)\s*\(([\d.]+)GHz(?:,\s*(\d+)MHz)?\)", cur.get("spairport_network_channel", ""))
        if m:
            out["channel"] = int(m.group(1))
            out["band"] = f"{m.group(2)} GHz"
            if m.group(3):
                out["width_mhz"] = int(m.group(3))
        sn = re.match(r"(-?\d+) dBm / (-?\d+) dBm", cur.get("spairport_signal_noise", ""))
        if sn:
            out["signal"], out["noise"] = int(sn.group(1)), int(sn.group(2))
            out["quality"] = "good" if out["signal"] >= -60 else "fair" if out["signal"] >= -70 else "weak"
        phy = cur.get("spairport_network_phymode", "")
        if phy:
            out["phy"] = phy
            out["generation"] = wifi_generation(phy, out.get("band", ""))
        if cur.get("spairport_network_rate"):
            out["rate_mbps"] = cur["spairport_network_rate"]
        return out
    return None


def parse_netmask(ifconfig_out):
    """(ip, prefix length) of the first IPv4 address in `ifconfig <dev>` output."""
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)", ifconfig_out or "")
    if not m:
        return None, None
    return m.group(1), bin(int(m.group(2), 16)).count("1")


def is_lan(ip):
    """True for private-network addresses (home and office networks, CGNAT, IPv6 ULA)."""
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if getattr(a, "ipv4_mapped", None):
        a = a.ipv4_mapped
    return any(a in n for n in LAN_NETS if n.version == a.version)


# Small live readers (shared) ------------------------------------------------------

def default_route():
    return parse_route(core.run(["route", "-n", "get", "default"], timeout=5))


def hardware_ports():
    return parse_hardware_ports(core.run(["networksetup", "-listallhardwareports"], timeout=10))


class Resolver:
    """Reverse DNS with a cache and a 1 s budget per lookup, on background threads. Never blocks the caller."""

    def __init__(self, workers=8, ttl=3600):
        self.pool = concurrent.futures.ThreadPoolExecutor(workers, thread_name_prefix="rdns")
        self.cache = {}       # ip -> (name or "", time)
        self.pending = {}     # ip -> future
        self.lock = threading.Lock()
        self.ttl = ttl

    @staticmethod
    def _lookup(ip):
        try:
            return socket.gethostbyaddr(ip)[0]
        except (OSError, UnicodeError):
            return ""

    def name(self, ip):
        """The cached name ("" if none yet); starts a lookup if it isn't cached."""
        now = time.time()
        with self.lock:
            hit = self.cache.get(ip)
            if hit and now - hit[1] < self.ttl:
                return hit[0]
            fut = self.pending.get(ip)
            if fut is None:
                self.pending[ip] = self.pool.submit(self._lookup, ip)
                return hit[0] if hit else ""
            if fut.done():
                del self.pending[ip]
                name = fut.result() or ""
                self.cache[ip] = (name, now)
                return name
            return hit[0] if hit else ""

    def wait(self, ips, timeout=1.0):
        """Resolve a few addresses now, waiting at most `timeout` seconds in total."""
        for ip in ips:
            self.name(ip)
        with self.lock:
            futs = [self.pending[ip] for ip in ips if ip in self.pending]
        if futs:
            concurrent.futures.wait(futs, timeout=timeout)
        return {ip: self.name(ip) for ip in ips}


RESOLVER = None


def resolver():
    global RESOLVER
    if RESOLVER is None:
        RESOLVER = Resolver()
    return RESOLVER


# The widget -----------------------------------------------------------------------

class Network(Widget):
    id = "network"
    title = "Network"
    category = "network"
    description = "Internet up or down, speed, and how this Mac is connected: Wi-Fi or cable, VPN, DNS, public IP."
    interval = 30
    size = "m"
    settings = [
        {"key": "ping", "label": "Check the internet every 30 seconds", "type": "bool", "default": True,
         "help": "Sends a tiny ping to Cloudflare (1.1.1.1)."},
        {"key": "speed_test_hours", "label": "Run a speed test every (hours)", "type": "int", "default": 3,
         "help": "Uses Apple's networkQuality, which downloads and uploads for about 15 seconds. 0 turns it off."},
        {"key": "public_ip", "label": "Show my public IP address", "type": "bool", "default": True,
         "help": "Asks Cloudflare (1.1.1.1) every 15 minutes which address the internet sees."},
    ]
    alert_rules = [{"key": "offline", "label": "The internet is down"}]

    def __init__(self, app):
        super().__init__(app)
        self.fails = 0
        self.ports = {}
        self.ports_at = 0
        self.wifi = None
        self.wifi_at = 0
        self.wifi_busy = False
        self.public = None
        self.public_at = 0
        self.speed_busy = False
        self.started = time.time()

    def available(self):
        return bool(core.which("ping"))

    # Background jobs ---------------------------------------------------------------

    def _wifi_scan(self, dev):
        try:
            self.wifi = parse_wifi(core.run_json(["system_profiler", "SPAirPortDataType", "-json"], timeout=40), dev)
        finally:
            self.wifi_at = time.time()
            self.wifi_busy = False

    def _speed_test(self):
        try:
            res = parse_speed(core.run(["networkQuality", "-c"], timeout=120, stderr=False))
            if res:
                res["at"] = time.time()
                self.state.set("network.speed", res)
                self.app.history.add_run("speed", "internet", res)
                self.record("down", res["down"])
                self.record("up", res["up"])
        finally:
            self.speed_busy = False
            self.app.poke(self.id)

    def start_speed_test(self):
        if self.speed_busy:
            return False
        if not core.which("networkQuality"):
            return False
        self.speed_busy = True
        threading.Thread(target=self._speed_test, daemon=True, name="network:speedtest").start()
        return True

    def fetch_public_ip(self):
        try:
            with urllib.request.urlopen("https://1.1.1.1/cdn-cgi/trace", timeout=4) as r:
                t = parse_trace(r.read().decode("utf-8", "replace"))
            self.public = {"ip": t.get("ip", ""), "country": t.get("loc", ""), "colo": t.get("colo", "")} if t.get("ip") else None
        except Exception:
            self.public = None
        self.public_at = time.time()

    # Collect -----------------------------------------------------------------------

    def collect(self):
        now = time.time()
        alerts = []
        ms = loss = None
        if self.setting("ping"):
            ms, loss = parse_ping(core.run(["ping", "-c", "3", "-q", "-t", "6", "1.1.1.1"], timeout=10))
            self.fails = 0 if ms is not None else self.fails + 1
            self.record("ping", ms)
            if self.fails >= 3:
                alerts.append({"key": "offline", "level": "bad", "text": "The internet is down: 1.1.1.1 hasn't answered for "
                               f"{self.fails} checks in a row.", "short": "Internet down"})
        online = self.fails == 0

        if not self.ports or now - self.ports_at > 600:
            self.ports, self.ports_at = hardware_ports(), now
        route = default_route()
        dev, gateway = route["interface"], route["gateway"]
        kind = connection_type(dev, self.ports) if dev else ""
        local_ip = core.run(["ipconfig", "getifaddr", dev], timeout=5).strip() if dev else ""

        # VPN: a connected VPN service, or the internet routed through a tunnel interface.
        services = parse_vpn_services(core.run(["scutil", "--nc", "list"], timeout=5))
        inet_dev = parse_route(core.run(["route", "-n", "get", "1.1.1.1"], timeout=5))["interface"]
        tunnel = next((d for d in (dev, inet_dev) if d and d.startswith(VPN_PREFIXES)), "")
        vpn = {"on": bool(services or tunnel), "name": ", ".join(services) or (tunnel and f"tunnel {tunnel}") or ""}

        dns = parse_dns(core.run(["scutil", "--dns"], timeout=5))
        dns_rows = [{"server": s, "label": dns_label(s, gateway)} for s in dns[:3]]

        # Wi-Fi details (system_profiler takes ~10 s, so it runs in the background every 3 minutes).
        wifi_dev = next((d for d, p in self.ports.items() if p == "Wi-Fi"), "")
        wifi_up = bool(wifi_dev and core.run(["ipconfig", "getifaddr", wifi_dev], timeout=5).strip())
        if wifi_up and not self.wifi_busy and now - self.wifi_at > 180:
            self.wifi_busy = True
            threading.Thread(target=self._wifi_scan, args=(wifi_dev,), daemon=True, name="network:wifi").start()
        wifi = dict(self.wifi, primary=(wifi_dev == dev)) if (wifi_up and self.wifi) else None

        if self.setting("public_ip") and online and now - self.public_at > 900:
            self.fetch_public_ip()
        public = self.public if self.setting("public_ip") else None

        hours = self.setting("speed_test_hours") or 0
        speed = self.state.get("network.speed")
        if hours > 0 and online and now - self.started > 120 and (not speed or now - speed.get("at", 0) > hours * 3600):
            self.start_speed_test()

        if not self.setting("ping"):
            summary = kind or "Not connected"
        elif ms is None:
            summary = "Offline"
        else:
            summary = f"Online · {ms:.0f} ms"
        state = "bad" if self.fails >= 3 else "warn" if ms is None and self.setting("ping") else "ok"
        return {
            "summary": summary, "state": state,
            "online": online if self.setting("ping") else None, "ping_ms": ms, "loss": loss, "fails": self.fails,
            "speed": speed, "speed_running": self.speed_busy, "speed_hours": hours,
            "connection": {"type": kind, "device": dev, "port": self.ports.get(dev, ""), "ip": local_ip, "router": gateway},
            "wifi": wifi, "vpn": vpn, "dns": dns_rows, "public": public,
            "alerts": alerts,
        }

    # Actions -----------------------------------------------------------------------

    def actions(self):
        return {"speedtest": self.speedtest}

    def speedtest(self, params):
        if self.speed_busy:
            return {"ok": True, "message": "A speed test is already running."}
        if not self.start_speed_test():
            return {"ok": False, "message": "This Mac doesn't have Apple's speed test (networkQuality)."}
        return {"ok": True, "message": "Speed test started. It takes about 15 seconds."}
