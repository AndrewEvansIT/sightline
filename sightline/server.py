"""HTTP server: the page, its static files, and a small JSON API.

Listens on 127.0.0.1 only, unless the user turns on the read-only network view (Settings > Sharing),
which also listens on the LAN and asks for a token there. Changes (POST) are only accepted from this Mac.
"""

import base64
import json
import mimetypes
import os
import re
import secrets
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import core

WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def make_handler(app):
    port = app.config.get("port")

    class Handler(BaseHTTPRequestHandler):
        server_version = "Sightline"

        # Plumbing ------------------------------------------------------------------

        def local(self):
            return self.client_address[0] in LOOPBACK

        def send(self, body, kind, code=200):
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data, code=200):
            self.send(json.dumps(data, default=str).encode(), "application/json", code)

        def static(self, rel):
            path = os.path.realpath(os.path.join(WEB, rel))
            if not path.startswith(os.path.realpath(WEB) + os.sep) or not os.path.isfile(path):
                return self.send_error(404)
            kind = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if kind.startswith("text/") or kind in ("application/javascript",):
                kind += "; charset=utf-8"
            with open(path, "rb") as f:
                self.send(f.read(), kind)

        def allowed(self, query):
            """LAN visitors need the token; this Mac never does."""
            if self.local():
                return True
            lan = app.config.get("lan", {})
            token = (query.get("token") or [""])[0]
            return lan.get("enabled") and lan.get("token") and secrets.compare_digest(token, lan["token"])

        # GET ---------------------------------------------------------------------

        def do_GET(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if not self.allowed(query):
                return self.send_error(403)
            p = url.path
            if p in ("/", "/index.html"):
                return self.static("index.html")
            if p.startswith("/static/"):
                return self.static(p[len("/static/"):])
            if p == "/api/status":
                snap = app.snapshot()
                snap["readonly"] = not self.local()
                return self.send_json(snap)
            if p == "/api/widgets":
                return self.send_json({"widgets": app.meta()})
            if p == "/api/renderers":
                folder = os.path.join(WEB, "widgets")
                names = sorted(f[:-3] for f in os.listdir(folder) if f.endswith(".js")) if os.path.isdir(folder) else []
                return self.send_json({"renderers": names})
            if p == "/api/config":
                conf = app.config.snapshot()
                if not self.local():
                    conf.pop("lan", None)
                return self.send_json(conf)
            if p == "/api/history":
                key = (query.get("key") or [""])[0]
                secs = int((query.get("range") or ["600"])[0])
                return self.send_json({"key": key, "points": app.history.series(key, secs)})
            if p == "/api/history/keys":
                return self.send_json({"keys": app.history.keys()})
            if p == "/api/runs":
                kind = (query.get("kind") or ["speed"])[0]
                return self.send_json({"runs": app.history.runs(kind, (query.get("subject") or [None])[0])})
            if p == "/api/alerts/next" and self.local():
                return self.send_json({"notes": app.alerts.take()})
            if p == "/api/profile" and self.local():
                return self.send_json(profile_export(app))
            if p == "/api/lan" and self.local():
                return self.send_json(lan_info(app))
            return self.send_error(404)

        # POST ----------------------------------------------------------------------

        def do_POST(self):
            if not self.local():
                return self.send_error(403)
            origin = self.headers.get("Origin")
            if origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}", "null"):
                return self.send_error(403)
            length = int(self.headers.get("Content-Length") or 0)
            if length > 20 * 2**20:
                return self.send_error(413)
            try:
                params = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send_error(400)
            p = urlparse(self.path).path
            if p == "/api/action":
                return self.send_json(app.action(params.get("widget", ""), params.get("action", ""), params.get("params")))
            if p == "/api/config":
                changes = params.get("changes") or {}
                changes.pop("port", None)
                conf = app.config.update(changes)
                for wid in (changes.get("widgets") or {}):
                    app.poke(wid)
                return self.send_json({"ok": True, "config": conf})
            if p == "/api/profile":
                return self.send_json(profile_import(app, params.get("profile") or {}))
            if p == "/api/card":
                return self.send_json(save_card(params.get("png", ""), params.get("name", "card")))
            if p == "/api/lan":
                return self.send_json(lan_set(app, bool(params.get("enabled"))))
            if p == "/api/open":
                return self.send_json(open_target(params.get("target", "")))
            return self.send_error(404)

        def log_message(self, *args):
            pass

    return Handler


# Profiles: layout and widget settings in one file someone else can import. -----------------

PROFILE_KEYS = ("layout", "screen", "strip_height", "column_width", "theme", "density", "order", "widgets", "alerts", "custom")


def profile_export(app):
    conf = app.config.snapshot()
    return {"sightline_profile": 1, "exported": time.strftime("%Y-%m-%d"),
            "settings": {k: conf[k] for k in PROFILE_KEYS if k in conf}}


def profile_import(app, profile):
    settings = profile.get("settings") if isinstance(profile, dict) else None
    if not profile.get("sightline_profile") or not isinstance(settings, dict):
        return {"ok": False, "message": "That file isn't a Sightline profile."}
    clean = {k: v for k, v in settings.items() if k in PROFILE_KEYS}
    # Script tiles run commands: never import them silently. They arrive switched off.
    for tile in clean.get("custom", []) or []:
        if isinstance(tile, dict):
            tile["enabled"] = False
    app.config.update(clean)
    for wid in app.widgets:
        app.poke(wid)
    n = len(clean.get("custom") or [])
    return {"ok": True, "message": "Profile imported." + (f" {n} script tile(s) came in switched off: check them in "
                                                           "Settings before turning them on." if n else "")}


# Share cards: the page draws a PNG; we save it to Pictures/Sightline and show it in Finder. --------

def save_card(data_url, name):
    m = re.match(r"data:image/png;base64,(.+)$", data_url or "", re.S)
    if not m:
        return {"ok": False, "message": "No picture to save."}
    folder = os.path.expanduser("~/Pictures/Sightline")
    os.makedirs(folder, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "-", name).strip("-")[:60] or "card"
    path = os.path.join(folder, f"{safe}-{time.strftime('%Y%m%d-%H%M%S')}.png")
    with open(path, "wb") as f:
        f.write(base64.b64decode(m.group(1)))
    subprocess.run(["open", "-R", path])
    return {"ok": True, "message": f"Saved to Pictures › Sightline.", "path": path}


# LAN view -------------------------------------------------------------------------------

def lan_ip():
    for dev in ("en0", "en1", "en2", "en3", "en4", "en5", "en6", "en7", "en8"):
        ip = core.run(["ipconfig", "getifaddr", dev]).strip()
        if ip:
            return ip
    return ""


def lan_info(app):
    lan = app.config.get("lan", {})
    ip = lan_ip()
    url = f"http://{ip}:{app.config.get('port')}/?token={lan.get('token')}" if lan.get("enabled") and ip else ""
    return {"enabled": bool(lan.get("enabled")), "url": url, "restart": bool(getattr(app, "lan_restart", False))}


def lan_set(app, enabled):
    lan = app.config.get("lan", {})
    token = lan.get("token") or secrets.token_urlsafe(12)
    app.config.update({"lan": {"enabled": enabled, "token": token}})
    app.lan_restart = enabled != getattr(app, "lan_bound", False)
    info = lan_info(app)
    info["message"] = ("Restart Sightline to apply." if app.lan_restart else
                       ("Phones and tablets on your network can open the link below." if enabled else "Network view is off."))
    return info


# Opening things in macOS (System Settings panes, Finder, Console). Allow-listed. ---------------

OPEN_TARGETS = {
    "firewall": ["open", "x-apple.systempreferences:com.apple.Network-Settings.extension?Firewall"],
    "privacy": ["open", "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension"],
    "login-items": ["open", "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"],
    "updates": ["open", "x-apple.systempreferences:com.apple.Software-Update-Settings.extension"],
    "time-machine": ["open", "x-apple.systempreferences:com.apple.Time-Machine-Settings.extension"],
    "battery": ["open", "x-apple.systempreferences:com.apple.Battery-Settings.extension"],
    "data-folder": ["open", core.HOME],
    "cards": ["open", os.path.expanduser("~/Pictures/Sightline")],
}


def open_target(target):
    if target.startswith("reveal:"):
        path = os.path.expanduser(target[len("reveal:"):])
        if os.path.exists(path):
            subprocess.run(["open", "-R", path])
            return {"ok": True}
        return {"ok": False, "message": "It isn't there any more."}
    cmd = OPEN_TARGETS.get(target)
    if not cmd:
        return {"ok": False, "message": "Unknown target."}
    if target == "cards":
        os.makedirs(cmd[1], exist_ok=True)
    subprocess.run(cmd)
    return {"ok": True}


def serve(app, port=None, lan=False):
    port = port or app.config.get("port")
    host = "0.0.0.0" if lan else "127.0.0.1"
    app.lan_bound = lan
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd
