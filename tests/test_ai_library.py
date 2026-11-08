import json
import os
import shutil
import struct
import tempfile
import unittest

from sightline.widgets import ai_library as L

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
GB = 2**30


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


# A tiny GGUF writer, enough for headers ----------------------------------------------

def _s(text):
    b = text.encode()
    return struct.pack("<Q", len(b)) + b


def _val(v):
    if isinstance(v, bool):
        return struct.pack("<I", 7) + struct.pack("<?", v)
    if isinstance(v, int):
        return struct.pack("<I", 4) + struct.pack("<I", v)
    if isinstance(v, float):
        return struct.pack("<I", 6) + struct.pack("<f", v)
    if isinstance(v, str):
        return struct.pack("<I", 8) + _s(v)
    if isinstance(v, list) and v and isinstance(v[0], str):
        return struct.pack("<I", 9) + struct.pack("<IQ", 8, len(v)) + b"".join(_s(x) for x in v)
    if isinstance(v, list):
        return struct.pack("<I", 9) + struct.pack("<IQ", 5, len(v)) + b"".join(struct.pack("<i", x) for x in v)
    raise TypeError(v)


def write_gguf(path, kv, pad=0):
    body = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv)) + b"".join(_s(k) + _val(v) for k, v in kv.items())
    with open(path, "wb") as f:
        f.write(body + b"\0" * pad)


LLAMA_GGUF = {
    "general.architecture": "llama", "general.name": "Tiny Llama", "general.file_type": 15,
    "llama.block_count": 32, "llama.context_length": 131072, "llama.embedding_length": 4096,
    "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8,
    "tokenizer.chat_template": "x" * 5000,
    "tokenizer.ggml.tokens": [f"tok{i}" for i in range(6000)],
    "tokenizer.ggml.token_type": [1] * 6000,
    "llama.rope.freq_base": 500000.0,
}


def touch(path, size=0, data=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if data is not None else b"\0" * size)


def make_hf_repo(cache, repo, files, commit="abc123", config=None, incomplete=0):
    """A Hugging Face cache repo: blobs/<hash>, snapshots/<commit>/<file> -> ../../blobs/<hash>, refs/main."""
    d = os.path.join(cache, "models--" + repo.replace("/", "--"))
    snap = os.path.join(d, "snapshots", commit)
    os.makedirs(snap, exist_ok=True)
    os.makedirs(os.path.join(d, "refs"), exist_ok=True)
    with open(os.path.join(d, "refs", "main"), "w") as f:
        f.write(commit)
    if config is not None:
        files = dict(files, **{"config.json": json.dumps(config).encode()})
    for i, (name, content) in enumerate(sorted(files.items())):
        blob = os.path.join(d, "blobs", f"{i:064x}")
        touch(blob, data=content if isinstance(content, bytes) else None, size=content if isinstance(content, int) else 0)
        link = os.path.join(snap, name)
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink(os.path.relpath(blob, os.path.dirname(link)), link)
    for i in range(incomplete):
        touch(os.path.join(d, "blobs", f"{i + 100:064x}.incomplete"), 1000)
    return d


class Tree(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = os.path.join(self.root, "home")
        self.vols = os.path.join(self.root, "Volumes")
        os.makedirs(self.home)
        os.makedirs(self.vols)

    def tearDown(self):
        shutil.rmtree(self.root)


class Discovery(Tree):
    def test_finds_caches_once_each(self):
        home_cache = os.path.join(self.home, ".cache", "huggingface", "hub")
        ext = os.path.join(self.vols, "Ext", "HuggingFace", "hub")
        odd = os.path.join(self.vols, "Ext", "Stuff", "hub")      # found by the shallow search
        deep = os.path.join(self.vols, "Ext", "a", "b", "c", "hub")  # too deep: not looked for
        for c in (home_cache, ext, odd, deep):
            make_hf_repo(c, "org/model", {"model.safetensors": 10})
        os.symlink(ext, os.path.join(self.root, "alias"))
        caches = L.hf_caches(env={"HF_HUB_CACHE": os.path.join(self.root, "alias")}, home=self.home, volumes=self.vols)
        self.assertEqual(sorted(os.path.realpath(c) for c, _ in caches),
                         sorted(os.path.realpath(c) for c in (home_cache, ext, odd)))
        drives = {os.path.realpath(c): d for c, d in caches}
        self.assertEqual(drives[os.path.realpath(home_cache)], "This Mac")
        self.assertEqual(drives[os.path.realpath(odd)], "Ext")

    def test_not_a_hub_without_model_folders(self):
        os.makedirs(os.path.join(self.vols, "Ext", "HuggingFace", "hub", "datasets--x--y"))
        self.assertEqual(L.hf_caches(env={}, home=self.home, volumes=self.vols), [])
        self.assertFalse(L.any_location(env={}, home=self.home, volumes=self.vols))

    def test_hf_sizes_count_newest_snapshot_once(self):
        cache = os.path.join(self.home, ".cache", "huggingface", "hub")
        d = make_hf_repo(cache, "mlx-community/Qwen2.5-7B-Instruct-4bit",
                         {"model.safetensors": 3000, "tokenizer.json": 100}, config=fixture("library_qwen.json"), incomplete=2)
        # An older snapshot pointing at the same blob, plus a second link to it: still counted once.
        old = os.path.join(d, "snapshots", "old000")
        os.makedirs(old)
        blob = os.path.realpath(os.path.join(d, "snapshots", "abc123", "model.safetensors"))
        os.symlink(blob, os.path.join(old, "model.safetensors"))
        os.utime(old, (1, 1))
        os.symlink(blob, os.path.join(d, "snapshots", "abc123", "copy.safetensors"))
        m = L.scan_hf_repo(d, "This Mac", L.Tally())
        cfg_size = len(json.dumps(fixture("library_qwen.json")).encode())
        self.assertEqual(m["bytes"], 3000 + 100 + cfg_size)
        self.assertEqual(m["own"], m["bytes"])
        self.assertEqual(m["name"], "mlx-community/Qwen2.5-7B-Instruct-4bit")
        self.assertEqual(m["format"], "MLX")
        self.assertEqual(m["quant"], "4bit")
        self.assertEqual(m["partial"], 2)
        self.assertEqual(m["kv"]["full"], 57344)

    def test_shared_blob_store_isnt_own(self):
        cache = os.path.join(self.home, ".cache", "huggingface", "hub")
        d = make_hf_repo(cache, "org/shared", {"small.json": 10})
        big = os.path.join(cache, "blobs", "ab", "ab" * 32)
        touch(big, 5000)
        os.symlink(big, os.path.join(d, "snapshots", "abc123", "model.safetensors"))
        m = L.scan_hf_repo(d, "This Mac", L.Tally())
        self.assertEqual(m["bytes"], 5010)
        self.assertEqual(m["own"], 10)

    def test_ollama_lmstudio_and_loose(self):
        ol = os.path.join(self.home, ".ollama", "models")
        weights = os.path.join(ol, "blobs", "sha256-" + "a" * 64)
        os.makedirs(os.path.dirname(weights))
        write_gguf(weights, LLAMA_GGUF, pad=2000)
        touch(os.path.join(ol, "blobs", "sha256-" + "b" * 64), data=json.dumps({"file_type": "Q4_K_M"}).encode())
        touch(os.path.join(ol, "manifests", "registry.ollama.ai", "library", "tiny", "latest"), data=json.dumps({
            "config": {"mediaType": "application/vnd.docker.container.image.v1+json", "digest": "sha256:" + "b" * 64},
            "layers": [{"mediaType": "application/vnd.ollama.image.model", "digest": "sha256:" + "a" * 64, "size": 1}]}).encode())
        lms = os.path.join(self.home, ".lmstudio", "models")
        touch(os.path.join(lms, "lmstudio-community", "Tiny-GGUF", "Tiny-Q4_K_M.gguf"), 4000)
        touch(os.path.join(lms, "lmstudio-community", "Tiny-GGUF", "Tiny-Q8_0.gguf"), 8000)
        touch(os.path.join(lms, "lmstudio-community", "Tiny-GGUF", "mmproj-Tiny-f16.gguf"), 50)
        touch(os.path.join(lms, "mlx-community", "Small-4bit", "model.safetensors"), 2500)
        touch(os.path.join(lms, "mlx-community", "Small-4bit", "config.json"), data=json.dumps(fixture("library_llama.json")).encode())
        touch(os.path.join(self.home, "models", "Tiny-Q4_K_M.gguf"), 4000)
        touch(os.path.join(self.home, "models", "Split-Q4_K_M-00001-of-00002.gguf"), 600)
        touch(os.path.join(self.home, "models", "Split-Q4_K_M-00002-of-00002.gguf"), 400)
        touch(os.path.join(self.home, "models", "notes.txt"), 5)
        r = L.scan_all(env={}, home=self.home, volumes=self.vols, downloads=False)
        by = {(m["store"], m["name"]): m for m in r["models"]}
        tiny = by[("Ollama", "tiny:latest")]
        self.assertEqual(tiny["quant"], "Q4_K_M")
        self.assertEqual(tiny["kv"]["full"], 131072)
        self.assertEqual(tiny["kv"]["max"], 131072)
        self.assertFalse(tiny["trashable"])
        self.assertIn(("LM Studio", "lmstudio-community/Tiny-GGUF Q4_K_M"), by)
        self.assertIn(("LM Studio", "lmstudio-community/Tiny-GGUF Q8_0"), by)
        small = by[("LM Studio", "mlx-community/Small-4bit")]
        self.assertEqual((small["format"], small["quant"]), ("MLX", "4bit"))
        split = by[("Models folder", "Split-Q4_K_M")]
        self.assertEqual(split["bytes"], 1000)
        self.assertFalse(split["trashable"])  # parts of a split model aren't moved one at a time
        # The loose Tiny-Q4_K_M.gguf matches LM Studio's by name and size.
        self.assertEqual(len(r["duplicates"]), 1)
        g = r["duplicates"][0]
        self.assertEqual({r["models"][i]["store"] for i in g["members"]}, {"LM Studio", "Models folder"})
        self.assertEqual(g["reclaim"], 4000)
        self.assertEqual(len(r["models"]), 6)


class Duplicates(Tree):
    def test_same_repo_in_two_caches_and_lmstudio(self):
        a = os.path.join(self.home, ".cache", "huggingface", "hub")
        b = os.path.join(self.vols, "Ext", "huggingface", "hub")
        make_hf_repo(a, "mlx-community/Model-4bit", {"model.safetensors": 3000})
        make_hf_repo(b, "mlx-community/Model-4bit", {"model.safetensors": 3000})
        make_hf_repo(b, "other/Thing", {"model.safetensors": 10})
        touch(os.path.join(self.home, ".lmstudio", "models", "mlx-community", "Model-4bit", "model.safetensors"), 2900)
        r = L.scan_all(env={}, home=self.home, volumes=self.vols, downloads=False)
        self.assertEqual(len(r["duplicates"]), 1)
        g = r["duplicates"][0]
        self.assertEqual(len(g["members"]), 3)
        self.assertEqual(g["reclaim"], 3000 + 2900)
        self.assertEqual(r["total"], 3000 + 3000 + 2900 + 10)
        self.assertEqual(len(r["trashable"]), 4)

    def test_hard_links_arent_reclaimable(self):
        models = [L._model(name="a", path="/a", bytes=100, keys=[("repo", "x")], inodes={(1, 1): 100}),
                  L._model(name="a", path="/b", bytes=100, keys=[("repo", "x")], inodes={(1, 1): 100})]
        self.assertEqual(L.find_duplicates(models)[0]["reclaim"], 0)


class Context(unittest.TestCase):
    def test_llama(self):
        kv = L.kv_from_config(fixture("library_llama.json"))
        self.assertEqual(kv["full"], 2 * 32 * 8 * 128 * 2)
        self.assertEqual(L.context_costs(kv), [{"tokens": 8192, "gb": 1.0}, {"tokens": 32768, "gb": 4.0},
                                               {"tokens": 131072, "gb": 16.0}])

    def test_qwen_gqa_capped_at_max(self):
        kv = L.kv_from_config(fixture("library_qwen.json"))
        self.assertEqual(kv["full"], 2 * 28 * 4 * 128 * 2)
        self.assertEqual([c["tokens"] for c in L.context_costs(kv)], [8192, 32768])
        self.assertEqual(L.context_costs(kv)[1]["gb"], 1.75)

    def test_gemma_text_config_and_sliding_window(self):
        kv = L.kv_from_config(fixture("library_gemma3.json"))
        per_layer = 2 * 8 * 256 * 2
        self.assertEqual(kv["full"], per_layer * 8)
        self.assertEqual(kv["sliding"], per_layer * 40)
        self.assertEqual(kv["window"], 1024)
        self.assertEqual(L.kv_bytes(kv, 8192), per_layer * (8 * 8192 + 40 * 1024))

    def test_hybrid_layers_and_mla(self):
        cfg = {"text_config": {"num_hidden_layers": 6, "num_attention_heads": 16, "num_key_value_heads": 4, "hidden_size": 2048,
                               "head_dim": 128, "sliding_window": 256, "max_position_embeddings": 128000,
                               "layer_types": ["conv", "full_attention", "conv", "sliding_attention", "linear_attention", "full_attention"]}}
        kv = L.kv_from_config(cfg)
        self.assertEqual((kv["full"], kv["sliding"]), (2 * 2 * 4 * 128 * 2, 2 * 4 * 128 * 2))
        mla = L.kv_from_config({"num_hidden_layers": 61, "num_attention_heads": 128, "hidden_size": 7168,
                                "kv_lora_rank": 512, "qk_rope_head_dim": 64, "max_position_embeddings": 163840})
        self.assertEqual(mla["full"], 61 * (512 + 64) * 2)
        nxt = L.kv_from_config({"num_hidden_layers": 48, "num_attention_heads": 16, "num_key_value_heads": 2,
                                "hidden_size": 2048, "head_dim": 256, "full_attention_interval": 4})
        self.assertEqual(nxt["full"], 12 * 2 * 2 * 256 * 2)

    def test_not_a_language_model(self):
        self.assertIsNone(L.kv_from_config({"_class_name": "FluxPipeline"}))
        self.assertEqual(L.context_costs(None), [])


class GGUF(Tree):
    def test_header(self):
        p = os.path.join(self.root, "m.gguf")
        write_gguf(p, dict(LLAMA_GGUF, **{"llama.attention.head_count_kv": [8] * 30 + [0, 0]}), pad=4096)
        meta = L.read_gguf_header(p)
        self.assertEqual(meta["general.architecture"], "llama")
        self.assertEqual(meta["general.file_type"], 15)
        self.assertNotIn("tokenizer.ggml.tokens", meta)      # big arrays are skipped
        self.assertNotIn("tokenizer.chat_template", meta)    # and long strings
        self.assertAlmostEqual(meta["llama.rope.freq_base"], 500000.0)
        kv = L.kv_from_gguf(meta)
        self.assertEqual(kv["full"], 30 * 8 * (128 + 128) * 2)
        self.assertEqual(kv["max"], 131072)

    def test_bad_files(self):
        p = os.path.join(self.root, "bad.gguf")
        touch(p, data=b"GGML" + b"\0" * 64)
        self.assertIsNone(L.read_gguf_header(p))
        touch(p, data=b"GGUF" + struct.pack("<IQQ", 3, 0, 5) + _s("general.name"))  # cut short
        self.assertIsNone(L.read_gguf_header(p))
        write_gguf(p, LLAMA_GGUF)
        self.assertIsNone(L.read_gguf_header(p, limit=1000))  # bigger than allowed


class Names(unittest.TestCase):
    def test_quant(self):
        cases = {"Qwen3-4B-Instruct-2507-4bit": "4bit", "Llama-3.1-8B-Instruct-Q4_K_M.gguf": "Q4_K_M",
                 "model-IQ2_XXS-00001-of-00003.gguf": "IQ2_XXS", "Kimi-mlx-DQ3_K_M-q8": "DQ3_K_M",
                 "gemma-3-12b-it-bf16": "bf16", "Mistral-7B-v0.3": "", "MiMo-Q8_0.gguf": "Q8_0", "phi-4-8-bit": "8bit"}
        for name, want in cases.items():
            self.assertEqual(L.parse_quant(name), want, name)

    def test_norm(self):
        self.assertEqual(L.norm("mlx-community/Qwen3-4B-4bit"), L.norm("/x/hub/models--mlx-community--Qwen3-4B-4bit/snapshots/abc"))
        self.assertEqual(L.norm("qwen3:latest"), "qwen3")
        self.assertEqual(L.norm("/m/Big-Q4_K_M-00001-of-00002.gguf"), "bigq4km")
        self.assertEqual(L.repo_of("models--gpt2"), "gpt2")


class Verdicts(unittest.TestCase):
    def test_verdicts(self):
        loaded = [("small", 10 * GB), ("big", 40 * GB)]
        self.assertEqual(L.verdict(20 * GB, 30 * GB, 400 * GB, loaded), ("fits now", "ok"))
        self.assertEqual(L.verdict(60 * GB, 30 * GB, 400 * GB, loaded), ("fits if you stop big", "warn"))
        self.assertEqual(L.verdict(75 * GB, 30 * GB, 400 * GB, loaded), ("fits if you stop big and small", "warn"))
        self.assertEqual(L.verdict(90 * GB, 30 * GB, 400 * GB, loaded), ("would push into swap", "bad"))
        self.assertEqual(L.verdict(500 * GB, 600 * GB, 400 * GB, []), ("too big for this Mac", "bad"))
        self.assertEqual(L.verdict(500 * GB, 0, 400 * GB, [], is_loaded=True), ("loaded now", "ok"))

    def test_needs_and_ceiling(self):
        m = L._model(bytes=10 * GB, kv=L.kv_from_config(fixture("library_llama.json")))
        self.assertEqual(L.needs(m), int(11 * GB) + GB)
        fake = {"hw.memsize": str(64 * GB), "iogpu.wired_limit_mb": "0"}
        self.assertEqual(L.gpu_ceiling(lambda cmd: fake[cmd[-1]]), 48 * GB)
        fake["iogpu.wired_limit_mb"] = "57344"
        self.assertEqual(L.gpu_ceiling(lambda cmd: fake[cmd[-1]]), 56 * GB)

    def test_running_match(self):
        rt = {"models": [{"runtime": "mlx", "name": "mlx-community/Qwen3-4B-4bit", "memory_gb": 3.0},
                         {"runtime": "ollama", "name": "tiny:latest", "gb": 1.5}, "junk"]}
        running = L.running_models(rt)
        self.assertEqual([(n, b) for n, b, _, _ in running], [("mlx-community/Qwen3-4B-4bit", 3 * GB), ("tiny:latest", int(1.5 * GB))])
        hf = L._model(path="/h/models--mlx-community--Qwen3-4B-4bit", match=[L.norm("mlx-community/Qwen3-4B-4bit")])
        other = L._model(path="/h/models--x--Qwen3-4B-8bit", match=[L.norm("x/Qwen3-4B-8bit")])
        self.assertTrue(L.is_running(hf, running))
        self.assertFalse(L.is_running(other, running))
        self.assertEqual(L.running_models({}), [])


class FakeConfig:
    def widget(self, wid):
        return {}


class FakeHistory:
    def add(self, key, value):
        pass


class FakeApp:
    def __init__(self, data=None):
        self.config = FakeConfig()
        self.history = FakeHistory()
        self.pokes = 0
        self._data = data or {}

    def poke(self, wid):
        self.pokes += 1

    def data(self, wid):
        return self._data.get(wid, {})


class Widget(Tree):
    def setUp(self):
        super().setUp()
        self.saved = (L.available_bytes, L.gpu_ceiling)
        L.available_bytes = lambda: 40 * GB
        L.gpu_ceiling = lambda: 100 * GB

    def tearDown(self):
        L.available_bytes, L.gpu_ceiling = self.saved
        super().tearDown()

    def widget(self, data=None):
        w = L.AILibrary(FakeApp(data))
        w.scan = L.scan_all(env={}, home=self.home, volumes=self.vols, downloads=False)
        w.scan["models"][0]["bytes"] = 50 * GB  # pretend the biggest one is 50 GB
        return w

    def test_collect_and_trash_guard(self):
        cache = os.path.join(self.home, ".cache", "huggingface", "hub")
        repo = make_hf_repo(cache, "org/Big-4bit", {"model.safetensors": 3000}, config=fixture("library_llama.json"))
        make_hf_repo(cache, "org/Small-4bit", {"model.safetensors": 1000})
        w = self.widget({"ai_runtimes": {"models": [{"name": "loaded-thing", "memory_gb": 30}]}})
        d = w.collect()
        self.assertEqual(d["count"], 2)
        big, small = d["models"]
        self.assertEqual(big["name"], "org/Big-4bit")
        self.assertEqual(big["verdict"], "fits if you stop loaded-thing")   # 55 + 1 GB needed, 32 free + 30
        self.assertEqual(small["verdict"], "fits now")
        self.assertEqual([c["tokens"] for c in big["context"]], [8192, 32768, 131072])
        calls = []

        class Done:
            returncode, stderr = 0, ""

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return Done()

        res = w.trash({"path": os.path.join(self.home, ".cache")}, run=fake_run)
        self.assertFalse(res["ok"])
        res = w.trash({"path": repo + "/../models--org--Big-4bit/snapshots"}, run=fake_run)
        self.assertFalse(res["ok"])
        self.assertEqual(calls, [])
        w.start_scan = lambda force=False: True
        res = w.trash({"path": repo}, run=fake_run)
        self.assertTrue(res["ok"])
        self.assertEqual(calls[0][0], "osascript")
        self.assertEqual(calls[0][-1], os.path.realpath(repo))   # the path goes in as an argument, never into the script
        self.assertNotIn(repo, " ".join(calls[0][:-1]))
        self.assertTrue(os.path.exists(repo))                     # nothing was really moved
        self.assertFalse(w.trash({"path": repo}, run=fake_run)["ok"])  # once only, until the next scan

    def test_first_collect_scans_in_background(self):
        w = L.AILibrary(FakeApp())
        w.start_scan = lambda force=False: True
        self.assertTrue(w.collect()["scanning"])


if __name__ == "__main__":
    unittest.main()
