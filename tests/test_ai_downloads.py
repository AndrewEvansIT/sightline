import json
import os
import shutil
import tempfile
import time
import unittest

from sightline.widgets import ai_downloads as D


def touch(path, size=0, data=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if data is not None else b"\0" * size)


def grow(path, n):
    with open(path, "ab") as f:
        f.write(b"\0" * n)


SHA = {i: f"{i:x}" * 64 for i in range(1, 6)}


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def widget(self, wid):
        return self.values


class FakeApp:
    def __init__(self, values=None):
        self.config = FakeConfig(values)


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.cache = os.path.join(self.root, "hub")
        self.repo = os.path.join(self.cache, "models--org--Big-Model")
        os.makedirs(os.path.join(self.repo, "snapshots", "c0ffee"))
        self.ollama = os.path.join(self.root, "ollama")
        os.makedirs(os.path.join(self.ollama, "blobs"))
        self.saved = D.ollama_roots
        D.ollama_roots = lambda: [self.ollama]

    def tearDown(self):
        D.ollama_roots = self.saved
        shutil.rmtree(self.root)

    def widget(self, values=None):
        w = D.AIDownloads(FakeApp(values if values is not None else {"ask_hf_for_sizes": False}))
        w.cache_list = (time.time() + 3600, [self.cache])
        return w

    def tree(self, files):
        """A newer cache's trees/<commit>.json: {path: (size, sha256)}."""
        touch(os.path.join(self.repo, "trees", "c0ffee.json"), data=json.dumps({"format_version": 1, "files": {
            p: {"size": size, "blob_id": "x" * 40, "lfs_sha256": sha} for p, (size, sha) in files.items()}}).encode())


class Finding(Base):
    def test_hf_partials_in_repo_and_shared_store(self):
        touch(os.path.join(self.repo, "blobs", SHA[1] + ".incomplete"), 100)
        touch(os.path.join(self.repo, "blobs", SHA[2]), 50)                      # finished: not a partial
        shared = os.path.join(self.cache, "blobs", SHA[3][:2], SHA[3])
        touch(shared + ".incomplete", 70)
        touch(shared + ".refs", data=b"models--org--Big-Model/blobs/" + SHA[3].encode() + b"\n")
        found = sorted(D.hf_partials(self.cache), key=lambda p: p["bytes"])
        self.assertEqual([(p["repo"], p["hash"], p["bytes"]) for p in found],
                         [("org/Big-Model", SHA[3], 70), ("org/Big-Model", SHA[1], 100)])
        self.assertEqual(found[0]["repo_dir"], self.repo)

    def test_ollama_partial_pieces(self):
        b = os.path.join(self.ollama, "blobs")
        touch(os.path.join(b, "sha256-" + SHA[1] + "-partial"), 500)
        touch(os.path.join(b, "sha256-" + SHA[1] + "-partial-0"), data=json.dumps({"N": 0, "Offset": 0, "Size": 400, "Completed": 300}).encode())
        touch(os.path.join(b, "sha256-" + SHA[1] + "-partial-1"), data=json.dumps({"N": 1, "Offset": 400, "Size": 600, "Completed": 100}).encode())
        touch(os.path.join(b, "sha256-" + SHA[2]), 10)
        found = D.ollama_partials(self.ollama)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["bytes"], found[0]["total"]), (400, 1000))

    def test_pull_names(self):
        args = {"1": "/usr/local/bin/ollama pull qwen3:8b", "2": "ollama serve", "3": "python3 x.py pull"}
        self.assertEqual(D.ollama_pull_names(args), ["qwen3:8b"])

    def test_api_tree(self):
        data = [{"type": "directory", "path": "sub"},
                {"type": "file", "path": "config.json", "size": 900, "oid": "a" * 40},
                {"type": "file", "path": "model.safetensors", "size": 135, "oid": "b" * 40,
                 "lfs": {"oid": SHA[1], "size": 5000, "pointerSize": 135}}]
        self.assertEqual(D.parse_api_tree(data), {"config.json": (900, "a" * 40), "model.safetensors": (5000, SHA[1])})
        self.assertEqual(D.parse_api_tree(None), {})


class Progress(Base):
    def test_split_model_counts_every_part(self):
        tree = {"model-00001-of-00003.safetensors": (1000, SHA[1]), "model-00002-of-00003.safetensors": (1000, SHA[2]),
                "model-00003-of-00003.safetensors": (500, SHA[3]), "README.md": (10, SHA[4])}
        blob = os.path.join(self.repo, "blobs", SHA[1])
        touch(blob, 1000)
        os.symlink(blob, os.path.join(self.repo, "snapshots", "c0ffee", "model-00001-of-00003.safetensors"))
        items = [{"hash": SHA[2], "bytes": 250}]
        self.assertEqual(D.repo_progress(items, tree, self.repo), (1250, 2500))
        self.assertEqual(D.repo_progress([{"hash": SHA[5], "bytes": 40}], tree, self.repo), (40, None))

    def test_speed(self):
        self.assertEqual(D.speed_of([(0, 0), (10, 1000)]), 100)
        self.assertEqual(D.speed_of([(0, 0)]), 0)


class Collect(Base):
    def test_download_then_done(self):
        self.tree({"model.safetensors": (10000, SHA[1]), "config.json": (10, SHA[2])})
        part = os.path.join(self.repo, "blobs", SHA[1] + ".incomplete")
        touch(part, 2000)
        w = self.widget()
        self.assertTrue(w.available())
        d = w.collect()
        self.assertEqual(len(d["downloads"]), 1)
        row = d["downloads"][0]
        self.assertEqual((row["name"], row["source"], row["pct"]), ("org/Big-Model", "Hugging Face", 20.0))
        time.sleep(0.05)
        grow(part, 3000)
        row = w.collect()["downloads"][0]
        self.assertEqual(row["pct"], 50.0)
        self.assertGreater(row["speed"], 0)
        self.assertIsNotNone(row["eta"])
        self.assertEqual(w.collect()["alerts"], [])
        os.rename(part, part[:-len(".incomplete")])
        d = w.collect()
        self.assertTrue(d["idle"])
        self.assertEqual(d["finished"][0]["name"], "org/Big-Model")
        self.assertEqual(d["alerts"], [{"key": "download-done:org/Big-Model", "level": "info",
                                        "text": "org/Big-Model finished downloading", "short": "Big-Model downloaded"}])
        self.assertTrue(w.available())  # the card stays 10 minutes so the finished row can show
        w.finished = [dict(f, at=time.time() - 601) for f in w.finished]
        self.assertFalse(w.available())

    def test_old_leftovers_are_ignored(self):
        part = os.path.join(self.repo, "blobs", SHA[1] + ".incomplete")
        touch(part, 2000)
        old = time.time() - 3600
        os.utime(part, (old, old))
        w = self.widget()
        self.assertFalse(w.available())
        self.assertEqual(w.collect()["downloads"], [])
        os.remove(part)                       # a cancelled download going away isn't "finished"
        self.assertEqual(w.collect()["finished"], [])

    def test_cancelled_download_isnt_finished(self):
        part = os.path.join(self.repo, "blobs", SHA[1] + ".incomplete")
        touch(part, 2000)
        w = self.widget()
        w.collect()
        os.remove(part)
        self.assertEqual(w.collect()["alerts"], [])

    def test_ollama_row(self):
        b = os.path.join(self.ollama, "blobs")
        part = os.path.join(b, "sha256-" + SHA[1] + "-partial")
        touch(part, 10)
        touch(part + "-0", data=json.dumps({"Size": 1000, "Completed": 250}).encode())
        D.ollama_pull_names, saved = (lambda args=None: ["llama3.2:3b"]), D.ollama_pull_names
        try:
            w = self.widget()
            row = w.collect()["downloads"][0]
            self.assertEqual((row["name"], row["source"], row["pct"]), ("llama3.2:3b", "Ollama", 25.0))
            os.rename(part, part[:-len("-partial")])
            os.remove(part + "-0")
            d = w.collect()
            self.assertEqual(d["alerts"][0]["key"], "download-done:llama3.2:3b")
        finally:
            D.ollama_pull_names = saved

    def test_asks_hugging_face_only_when_allowed(self):
        touch(os.path.join(self.repo, "blobs", SHA[1] + ".incomplete"), 2000)
        calls = []
        saved = D.core.http_json
        D.core.http_json = lambda url, **kw: calls.append(url) or [
            {"type": "file", "path": "model.safetensors", "size": 1, "lfs": {"oid": SHA[1], "size": 8000}}]
        try:
            w = self.widget({"ask_hf_for_sizes": False})
            self.assertIsNone(w.collect()["downloads"][0]["total_gb"])
            self.assertEqual(calls, [])
            w = self.widget({"ask_hf_for_sizes": True})
            w.collect()
            for _ in range(50):
                if w.remote:
                    break
                time.sleep(0.02)
            self.assertEqual(calls, ["https://huggingface.co/api/models/org/Big-Model/tree/main?recursive=true"])
            self.assertEqual(w.collect()["downloads"][0]["pct"], 25.0)
        finally:
            D.core.http_json = saved


if __name__ == "__main__":
    unittest.main()
