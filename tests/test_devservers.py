"""Dev servers: lsof parsing, which listeners count, grouping workers, and the guarded Stop."""

import os
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline.widgets import devservers  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
ME = "501"


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class ParseTest(unittest.TestCase):
    def test_listeners(self):
        l = devservers.parse_listeners(fixture("devservers_lsof.txt"))
        self.assertEqual(sorted(l), ["4101", "4202", "4303", "4404"])
        self.assertEqual(l["4101"]["ports"], [5173])  # IPv4 and IPv6 on one port count once
        self.assertEqual(l["4202"]["ports"], [8000])
        self.assertEqual(l["4101"]["command"], "node")

    def test_looks_dev(self):
        ok = devservers.looks_dev
        self.assertTrue(ok("/opt/homebrew/bin/node", "node server.js"))
        self.assertTrue(ok("/opt/homebrew/Cellar/python@3.12/3.12.1/Frameworks/Python.framework/Versions/3.12/Resources/"
                           "Python.app/Contents/MacOS/Python", "python manage.py runserver"))
        self.assertTrue(ok("/var/folders/x/T/go-build123/b001/exe/main", "main"))
        self.assertFalse(ok("/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/ControlCenter", ""))
        self.assertFalse(ok("/Applications/Docker.app/Contents/MacOS/com.docker.backend", "com.docker.backend"))
        self.assertFalse(ok("/opt/homebrew/bin/ollama", "ollama serve"))
        self.assertFalse(ok("/usr/bin/python3", "python3 -m mlx_lm.server --port 8080"))
        self.assertFalse(ok("/usr/bin/python3", "/usr/bin/python3 -m sightline --port 47800"))
        # A project folder that happens to contain a model word is still a dev server.
        self.assertTrue(ok("/opt/homebrew/bin/node", "node /Users/x/Developer/llama-chat/node_modules/.bin/vite"))

    def test_tool_name(self):
        self.assertEqual(devservers.tool_name("python manage.py runserver 8000"), "Django")
        self.assertEqual(devservers.tool_name("x", "vite"), "Vite")
        self.assertEqual(devservers.tool_name("python3 -m http.server 9000"), "Python web server")
        self.assertEqual(devservers.tool_name("node index.js"), "Node")


class GroupTest(unittest.TestCase):
    def test_vite_under_npm_from_a_shell(self):
        procs = {
            "100": ("1", ME, "/bin/zsh"),
            "200": ("100", ME, "/opt/homebrew/bin/node"),        # npm run dev
            "300": ("200", ME, "/bin/sh"),
            "400": ("300", ME, "/opt/homebrew/bin/node"),        # vite
            "401": ("400", ME, "/x/node_modules/@esbuild/darwin-arm64/bin/esbuild"),
            "500": ("1", ME, "/usr/libexec/rapportd"),
            "600": ("1", "0", "/opt/homebrew/bin/node"),         # someone else's
        }
        args = {"200": "node /opt/homebrew/bin/npm run dev", "300": "sh -c vite",
                "400": "node /Users/x/shop/node_modules/.bin/vite --port 5173", "401": "esbuild --service",
                "600": "node server.js"}
        listeners = {"400": {"ports": [5173], "command": "node"}, "500": {"ports": [55062], "command": "rapportd"},
                     "600": {"ports": [3000], "command": "node"}}
        servers = devservers.find_servers(listeners, procs, args, ME, "999")
        self.assertEqual(len(servers), 1)
        s = servers[0]
        self.assertEqual((s["root"], s["ports"], s["tool"]), ("400", [5173], "Vite"))
        self.assertEqual(sorted(s["pids"]), ["400", "401"])

    def test_reloader_parent_joins_and_agents_stop_the_climb(self):
        procs = {"10": ("1", ME, "/x/claude"), "11": ("10", ME, "/bin/zsh"),
                 "20": ("11", ME, "/usr/bin/python3"), "21": ("20", ME, "/usr/bin/python3"),
                 "30": ("1", ME, "/usr/bin/python3"), "31": ("30", ME, "/usr/bin/python3")}
        args = {"10": "claude", "20": "python3 -m uvicorn app:app --reload", "21": "python3 -c from multiprocessing",
                "30": "python3 /x/bin/aider", "31": "python3 -m http.server 9000"}
        listeners = {"21": {"ports": [8000], "command": "Python"}, "31": {"ports": [9000], "command": "Python"}}
        servers = {s["root"]: s for s in devservers.find_servers(listeners, procs, args, ME, "999")}
        self.assertEqual(sorted(servers), ["20", "31"])  # uvicorn's reloader leads; Aider isn't taken as a server
        self.assertEqual(servers["20"]["tool"], "Uvicorn")


class StopTest(unittest.TestCase):
    """Stop only signals our own processes when their command still matches. Uses a child we start here."""

    class FakeApp:
        pass

    def setUp(self):
        self.child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.w = devservers.DevServers(self.FakeApp())
        # Right after starting, the command line can still change (fork, then exec; Xcode's python3 re-execs
        # itself as Python.app), so wait until two reads agree.
        pid, cmd, prev = str(self.child.pid), "", None
        for _ in range(50):
            cmd = subprocess.run(["ps", "-o", "args=", "-p", pid], capture_output=True, text=True).stdout.strip()
            if "time.sleep(60)" in cmd and cmd == prev:
                break
            prev = cmd
            time.sleep(0.1)
        self.pid, self.cmd = pid, cmd

    def tearDown(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait()

    def test_unknown_pid_refused(self):
        self.assertFalse(self.w.stop({"pid": 1})["ok"])

    def test_changed_command_refused(self):
        self.w.targets = {self.pid: {self.pid: self.cmd + " something else"}}
        self.assertFalse(self.w.stop({"pid": self.pid})["ok"])
        time.sleep(0.2)
        self.assertIsNone(self.child.poll())

    def test_stops_matching_process(self):
        self.w.targets = {self.pid: {self.pid: self.cmd}}
        r = self.w.stop({"pid": int(self.pid)})
        now = subprocess.run(["ps", "-o", "pid=,uid=,args=", "-p", self.pid], capture_output=True, text=True).stdout
        self.assertTrue(r["ok"], (r, self.cmd, now))
        self.child.wait(timeout=5)
        self.assertIsNotNone(self.child.poll())


if __name__ == "__main__":
    unittest.main()
