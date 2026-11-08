"""python3 -m sightline [--port N] [--open]

Starts the collectors and the server. Open http://127.0.0.1:47800/ in a browser, or run the Mac app.
"""

import argparse
import faulthandler
import resource
import signal
import subprocess
import sys

from . import __version__
from .app import App
from .server import serve


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sightline", description="A desk panel for Macs that run local AI.")
    ap.add_argument("--port", type=int, help="port to listen on (default 47800, or the one in config.json)")
    ap.add_argument("--open", action="store_true", help="open the panel in your browser")
    ap.add_argument("--version", action="version", version=f"sightline {__version__}")
    args = ap.parse_args(argv)

    faulthandler.register(signal.SIGUSR1, all_threads=True)  # kill -USR1 <pid> prints every thread's stack
    # The network sweep opens many pipes at once; launchd's default of 256 open files runs out.
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))

    app = App()
    if args.port:
        app.config.data["port"] = args.port
    port = app.config.get("port")
    httpd = serve(app, port, lan=bool(app.config.get("lan", {}).get("enabled")))
    print(f"Sightline {__version__} on http://127.0.0.1:{port}/", file=sys.stderr, flush=True)
    if args.open:
        subprocess.run(["open", f"http://127.0.0.1:{port}/"])
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
