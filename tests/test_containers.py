"""Containers: parsing saved docker ps / docker stats output, and collect with a stand-in docker command."""

import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline.widgets import containers  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class ParseTest(unittest.TestCase):
    def test_ps(self):
        cs = containers.parse_ps(fixture("containers_docker_ps.jsonl"))
        self.assertEqual([c["name"] for c in cs], ["shop-db-1", "shop-cache-1", "scratch"])
        db = cs[0]
        self.assertEqual((db["state"], db["image"], db["project"], db["health"]), ("running", "postgres:17", "shop", "healthy"))
        self.assertEqual(db["ports"], ["5432"])
        self.assertEqual(cs[1]["ports"], ["16379→6379"])
        self.assertEqual((cs[2]["state"], cs[2]["ports"], cs[2]["project"]), ("exited", [], ""))

    def test_stats(self):
        st = containers.parse_stats(fixture("containers_docker_stats.jsonl"))
        self.assertEqual(st["a1b2c3d4e5f6"]["mem"], int(91.5 * 2**20))
        self.assertEqual(st["shop-cache-1"]["cpu"], 0.10)

    def test_sizes_and_garbage(self):
        self.assertEqual(containers.parse_size("1.5GiB"), 1.5 * 2**30)
        self.assertEqual(containers.parse_size("12kB"), 12000)
        self.assertEqual(containers.parse_size("0B"), 0)
        self.assertEqual(containers.parse_size(""), 0)
        self.assertEqual(containers.parse_ps("Cannot connect to the Docker daemon\n"), [])
        self.assertIsNone(containers.parse_pct("--"))

    def test_podman(self):
        text = ('[{"Id":"abcdef0123456789","Names":["web"],"Image":"nginx:1","State":"running","Status":"Up 1 hour",'
                '"Ports":[{"host_ip":"","container_port":80,"host_port":8080,"range":1,"protocol":"tcp"}],'
                '"Labels":{"com.docker.compose.project":"site"}}]')
        cs = containers.parse_podman_ps(text)
        self.assertEqual(cs[0]["id"], "abcdef012345")
        self.assertEqual((cs[0]["name"], cs[0]["ports"], cs[0]["project"]), ("web", ["8080→80"], "site"))
        self.assertEqual(containers.parse_podman_ps("not json"), [])


FAKE_DOCKER = """#!/bin/sh
case "$1" in
  info) [ -f "{dir}/down" ] && exit 1; echo '"29.0.0"';;
  ps) cat "{fix}/containers_docker_ps.jsonl";;
  stats) cat "{fix}/containers_docker_stats.jsonl";;
  stop|start|restart) echo "$1 $2" >> "{dir}/calls"; echo "$2";;
esac
"""


class CollectTest(unittest.TestCase):
    class FakeHistory:
        def add(self, *a):
            pass

    class FakeApp:
        pass

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="sightline-docker-")
        self.cli = os.path.join(self.dir, "docker")
        with open(self.cli, "w") as f:
            f.write(FAKE_DOCKER.replace("{dir}", self.dir).replace("{fix}", FIX))
        os.chmod(self.cli, os.stat(self.cli).st_mode | stat.S_IEXEC)
        app = self.FakeApp()
        app.history = self.FakeHistory()
        self.w = containers.Containers(app)
        self.patch = mock.patch.object(containers, "find_cli", return_value=(self.cli, "docker"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_collect(self):
        d = self.w.collect()
        self.assertTrue(d["running"])
        self.assertEqual(d["summary"], "2 of 3 running")
        names = [c["name"] for c in d["containers"]]
        self.assertEqual(names[-1], "scratch")  # stopped ones last
        db = next(c for c in d["containers"] if c["name"] == "shop-db-1")
        self.assertEqual((db["mem"], db["cpu"]), (int(91.5 * 2**20), 1.25))
        self.assertEqual(d["mem"], int(91.5 * 2**20) + int(4.2 * 2**20))

    def test_engine_down_is_quiet(self):
        open(os.path.join(self.dir, "down"), "w").close()
        d = self.w.collect()
        self.assertFalse(d["running"])
        self.assertEqual(d["note"], "Docker isn't running.")
        self.assertEqual(d["containers"], [])

    def test_actions_only_on_known_containers(self):
        self.assertFalse(self.w.act("stop", {"id": "a1b2c3d4e5f6"})["ok"])  # nothing collected yet
        self.w.collect()
        r = self.w.act("stop", {"id": "a1b2c3d4e5f6"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"], "Stopped shop-db-1.")
        self.assertFalse(self.w.act("restart", {"id": "; rm -rf /"})["ok"])
        with open(os.path.join(self.dir, "calls")) as f:
            self.assertEqual(f.read().split("\n")[:1], ["stop a1b2c3d4e5f6"])


if __name__ == "__main__":
    unittest.main()
