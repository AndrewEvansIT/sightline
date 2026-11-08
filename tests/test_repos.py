"""Repos: status parsing, repo discovery, and real (temporary) git repos with loose ends."""

import os
import shutil
import subprocess
import tempfile
import unittest

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline.widgets import repos  # noqa: E402

GIT_ENV = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_NAME="Test",
               GIT_COMMITTER_EMAIL="test@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def git(cwd, *args):
    subprocess.run(["git"] + list(args), cwd=cwd, env=GIT_ENV, check=True, capture_output=True)


def touch(path, text="x\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class ParseStatusTest(unittest.TestCase):
    def test_upstream_ahead_behind(self):
        st = repos.parse_status("## main...origin/main [ahead 2, behind 1]\n M a.py\nA  b.py\n?? c.txt\n?? d/\n")
        self.assertEqual((st["branch"], st["upstream"], st["ahead"], st["behind"]), ("main", "origin/main", 2, 1))
        self.assertEqual((st["changed"], st["untracked"]), (2, 2))

    def test_no_upstream(self):
        st = repos.parse_status("## feature/x\n")
        self.assertEqual((st["branch"], st["upstream"], st["ahead"]), ("feature/x", "", 0))

    def test_behind_only_and_gone(self):
        self.assertEqual(repos.parse_status("## main...origin/main [behind 4]\n")["behind"], 4)
        self.assertTrue(repos.parse_status("## main...origin/main [gone]\n").get("upstream_gone"))

    def test_detached_and_new(self):
        self.assertTrue(repos.parse_status("## HEAD (no branch)\n")["detached"])
        self.assertEqual(repos.parse_status("## No commits yet on main\n?? a\n")["branch"], "main")

    def test_renames_count_once(self):
        self.assertEqual(repos.parse_status("## main\nR  old.py -> new.py\n")["changed"], 1)


class GitReposTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sightline-repos-")
        self.root = os.path.join(self.tmp, "Developer")
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_repo(self, *parts):
        path = os.path.join(self.root, *parts)
        os.makedirs(path)
        git(path, "init", "-q", "-b", "main")
        touch(os.path.join(path, "README.md"))
        git(path, "add", ".")
        git(path, "commit", "-q", "-m", "first")
        return path

    def test_find_skips_and_depth(self):
        a = self.make_repo("a")
        b = self.make_repo("group", "b")
        self.make_repo("node_modules", "pkg")
        self.make_repo("one", "two", "three", "too-deep")
        os.makedirs(os.path.join(a, "inner"))
        found = repos.find_repos([self.root])
        self.assertEqual(sorted(found), sorted([a, b]))

    def test_cap(self):
        for i in range(5):
            self.make_repo(f"r{i}")
        self.assertEqual(len(repos.find_repos([self.root], cap=3)), 3)

    def test_clean_repo_has_no_loose_ends(self):
        r = repos.inspect(self.make_repo("clean"))
        self.assertFalse(r["loose"])
        self.assertEqual((r["changed"], r["untracked"], r["stashes"], r["remote"]), (0, 0, 0, False))
        self.assertIsNotNone(r["last_commit"])

    def test_changes_untracked_and_stash(self):
        path = self.make_repo("busy")
        touch(os.path.join(path, "README.md"), "changed\n")
        git(path, "stash", "-q")
        touch(os.path.join(path, "README.md"), "changed again\n")
        touch(os.path.join(path, "new.txt"))
        r = repos.inspect(path)
        self.assertEqual((r["changed"], r["untracked"], r["stashes"], r["branch"]), (1, 1, 1, "main"))
        self.assertTrue(r["loose"])

    def test_ahead_of_upstream_and_unpushed_branch(self):
        origin = os.path.join(self.tmp, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], env=GIT_ENV, check=True)
        src = self.make_repo("src")
        git(src, "remote", "add", "origin", origin)
        git(src, "push", "-q", "-u", "origin", "main")
        touch(os.path.join(src, "b.txt"))
        git(src, "add", ".")
        git(src, "commit", "-q", "-m", "second")
        r = repos.inspect(src)
        self.assertEqual((r["upstream"], r["ahead"], r["unpushed"]), ("origin/main", 1, 0))
        git(src, "checkout", "-q", "-b", "local-only")
        touch(os.path.join(src, "c.txt"))
        git(src, "add", ".")
        git(src, "commit", "-q", "-m", "third")
        r = repos.inspect(src)
        self.assertEqual((r["branch"], r["upstream"], r["unpushed"]), ("local-only", "", 2))
        self.assertTrue(r["loose"])

    def test_inspect_does_not_write(self):
        path = self.make_repo("quiet")
        touch(os.path.join(path, "README.md"), "changed\n")
        index = os.path.join(path, ".git", "index")

        def snapshot():
            with open(index, "rb") as f:
                return os.stat(index).st_mtime_ns, f.read()

        before = snapshot()
        repos.inspect(path)
        self.assertEqual(before, snapshot())

    def test_collect_shows_only_loose(self):
        self.make_repo("clean")
        busy = self.make_repo("busy")
        touch(os.path.join(busy, "new.txt"))

        class FakeConfig:
            def widget(self, wid):
                return {}

        class FakeHistory:
            def add(self, *a):
                pass

        class FakeApp:
            config, history = FakeConfig(), FakeHistory()

        w = repos.Repos(FakeApp())
        w.roots = lambda: [self.root]
        d = w.collect()
        self.assertEqual((d["count"], d["loose"]), (2, 1))
        self.assertEqual([r["name"] for r in d["repos"]], ["busy"])


if __name__ == "__main__":
    unittest.main()
