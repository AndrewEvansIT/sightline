import json
import os
import signal
import unittest
from unittest import mock

from sightline import core
from sightline.widgets import ai_runtimes as rt

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


def procs_fixture():
    d = json.loads(fixture("ai_processes.json"))
    procs = {k: tuple(v) for k, v in d["procs"].items()}
    return procs, d["args"], d["fp"]


class Parsers(unittest.TestCase):
    def test_quant(self):
        cases = {
            "mlx-community/Qwen3-32B-4bit": "4bit",
            "mlx-community/Llama-3.3-70B-Instruct-8bit-DWQ": "8bit",
            "gemma-3-27b-it-Q4_K_M": "Q4_K_M",
            "gemma-3-27b-it-Q4_K_M.gguf": "Q4_K_M",
            "phi-4-Q8_0": "Q8_0",
            "llama3.2:3b-instruct-q8_0": "Q8_0",
            "Qwen2.5-7B-IQ3_XS": "IQ3_XS",
            "DeepSeek-R1-Distill-bf16": "BF16",
            "qwen3:32b": None,
            "Qwen3-32B": None,
            "": None,
            None: None,
        }
        for name, want in cases.items():
            self.assertEqual(rt.parse_quant(name), want, name)

    def test_runtime_of(self):
        procs, args, _ = procs_fixture()
        got = {pid: rt.runtime_of(procs[pid][2], args.get(pid, "")) for pid in procs}
        self.assertEqual(got["708"], "oMLX")        # the real server (its title is rewritten to omlx-server)
        self.assertIsNone(got["706"])               # timeout wraps it: names oMLX but holds nothing
        self.assertIsNone(got["707"])               # caffeinate too
        self.assertIsNone(got["709"])               # a client that names a model in its arguments
        self.assertEqual(got["801"], "MLX")         # an app's own MLX helper
        self.assertEqual(got["902"], "MLX")
        self.assertEqual(got["903"], "llama.cpp")
        self.assertIsNone(got["904"])               # hf download
        self.assertIsNone(got["905"])               # a plain script: found from its open files instead
        self.assertIsNone(got["1001"])              # ollama serve: Ollama is read from its API
        self.assertEqual(got["1002"], "Ollama")     # the runner holding the model
        self.assertEqual(got["1100"], "llama.cpp")
        self.assertIsNone(got["703"])

    def test_runtime_of_more(self):
        self.assertEqual(rt.runtime_of("/x/python3", "python3 -m mlx_vlm.server --model mlx-community/gemma-3-4bit"), "MLX")
        self.assertEqual(rt.runtime_of("/x/python3", "python -m mlx_lm.generate --model foo --prompt hi"), "MLX")
        self.assertEqual(rt.runtime_of("/x/python3", "python -m vllm_mlx.serve --model foo"), "vllm-mlx")
        self.assertEqual(rt.runtime_of("/usr/local/bin/local-ai", "local-ai run"), "LocalAI")
        self.assertEqual(rt.runtime_of("/Applications/Jan.app/Contents/Resources/bin/llama-server", "llama-server -m x"), "Jan")
        self.assertIsNone(rt.runtime_of("/Applications/LM Studio.app/Contents/MacOS/LM Studio", ""))
        self.assertIsNone(rt.runtime_of("/x/python3", "huggingface-cli download mlx-community/foo"))
        self.assertIsNone(rt.runtime_of("/x/python3", "python -c from huggingface_hub import snapshot_download"))
        self.assertIsNone(rt.runtime_of("/bin/zsh", "zsh -c mlx_lm.server --model foo"))

    def test_model_from_args(self):
        self.assertEqual(rt.model_from_args("python -m mlx_lm.server --model mlx-community/Qwen3-32B-4bit --port 8080"),
                         "mlx-community/Qwen3-32B-4bit")
        self.assertEqual(rt.model_from_args("llama-server -m /Users/me/models/gemma-3-27b-it-Q4_K_M.gguf --port 8081"),
                         "gemma-3-27b-it-Q4_K_M")
        self.assertEqual(rt.model_from_args("llama-server -hf ggml-org/gemma-3-4b-it-GGUF"), "ggml-org/gemma-3-4b-it-GGUF")
        self.assertEqual(rt.model_from_args("python x.py /hub/models--mlx-community--Foo-4bit/snapshots/abc"),
                         "mlx-community/Foo-4bit")
        self.assertEqual(rt.model_from_args("python -m mlx_lm.server --model=/models/Bar-8bit/"), "Bar-8bit")
        self.assertIsNone(rt.model_from_args("omlx-server"))
        self.assertIsNone(rt.model_from_args(""))

    def test_port_from_args(self):
        self.assertEqual(rt.port_from_args("llama-server -m x --port 8081 -ngl 99"), 8081)
        self.assertEqual(rt.port_from_args("mlx_lm.server --port=9000"), 9000)
        self.assertIsNone(rt.port_from_args("mlx_lm.server --model x"))

    def test_listen_ports(self):
        self.assertEqual(rt.parse_listen_ports(fixture("ai_lsof_listen.txt")), [8301, 8302])
        self.assertEqual(rt.parse_listen_ports(""), [])

    def test_footprint(self):
        self.assertEqual(rt.parse_footprint(fixture("ai_footprint.txt")), 2110 * 2**20)
        self.assertEqual(rt.parse_footprint(fixture("ai_footprint_gb.txt")), 291 * 2**30)
        self.assertIsNone(rt.parse_footprint("footprint: Unable to find pid"))

    def test_framework(self):
        self.assertEqual(rt.framework_in(fixture("ai_lsof_files_mlx.txt")), "MLX")
        self.assertEqual(rt.framework_in(fixture("ai_lsof_files_torch.txt")), "PyTorch")
        self.assertIsNone(rt.framework_in(fixture("ai_lsof_files_plain.txt")))

    def test_who_started(self):
        procs, args, _ = procs_fixture()
        self.assertEqual(rt.who_started("708", procs, args), "Claude Code")
        self.assertEqual(rt.who_started("801", procs, args), "FluidVoice")
        self.assertEqual(rt.who_started("902", procs, args), "Visual Studio Code")
        self.assertEqual(rt.who_started("1002", procs, args), "Ollama")
        self.assertIsNone(rt.who_started("1100", procs, args))
        self.assertIsNone(rt.who_started("99999", procs, args))

    def test_agent_name(self):
        self.assertEqual(rt.agent_name("/usr/local/bin/node", "node /usr/local/bin/codex exec"), "Codex")
        self.assertEqual(rt.agent_name("node", "node /opt/homebrew/lib/node_modules/@google/gemini-cli/dist/index.js"), "Gemini CLI")
        self.assertEqual(rt.agent_name("/Users/me/.local/bin/claude", "claude"), "Claude Code")
        self.assertEqual(rt.agent_name("/x/python3", "python3 /x/bin/aider --model y"), "Aider")
        self.assertIsNone(rt.agent_name("/bin/zsh", "zsh"))

    def test_app_name(self):
        self.assertEqual(rt.app_name("/Applications/LM Studio.app/Contents/MacOS/LM Studio"), "LM Studio")
        self.assertEqual(rt.app_name("/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"), "Terminal")
        self.assertIsNone(rt.app_name("/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"))
        self.assertIsNone(rt.app_name("/opt/homebrew/bin/llama-server"))

    def test_ollama_ps(self):
        rows = rt.parse_ollama_ps(json.loads(fixture("ai_ollama_ps.json")))
        self.assertEqual([r["name"] for r in rows], ["qwen3:32b", "llama3.2:3b-instruct-q8_0"])
        self.assertEqual(rows[0]["quant"], "Q4_K_M")
        self.assertEqual(rows[0]["gb"], 25.0)
        self.assertEqual(rows[1]["quant"], "Q8_0")  # empty quantization_level: parsed from the name
        self.assertEqual(rows[0]["base_url"], "http://127.0.0.1:11434/v1")
        self.assertEqual(rt.parse_ollama_ps({}), [])
        self.assertEqual(rt.parse_ollama_ps(None), [])

    def test_lmstudio(self):
        rows = rt.parse_lmstudio(json.loads(fixture("ai_lmstudio_models.json")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "qwen/qwen3-30b-a3b")
        self.assertEqual(rows[0]["quant"], "4bit")
        self.assertEqual(rows[0]["base_url"], "http://127.0.0.1:1234/v1")

    def test_served_ids(self):
        self.assertEqual(rt.served_ids(json.loads(fixture("ai_v1_models.json"))), ["mlx-community/Qwen3-32B-4bit"])
        self.assertEqual(rt.served_ids({"error": "nope"}), [])
        self.assertEqual(rt.served_ids(None), [])
        self.assertEqual(rt.served_ids([1, 2]), [])

    def test_model_label(self):
        self.assertEqual(rt.model_label("mlx-community/Qwen3-32B-4bit", None, "/x/python3", ""), "Qwen3-32B-4bit")
        self.assertEqual(rt.model_label(None, ["org/Big-4bit"], "omlx-server", "omlx-server"), "Big-4bit")
        self.assertEqual(rt.model_label(None, ["a", "b", "c"], "omlx-server", "omlx-server"), "3 models served")
        self.assertEqual(rt.model_label(None, None, "/Applications/FluidVoice.app/Contents/Helpers/x-mlx", ""), "FluidVoice model")
        self.assertEqual(rt.model_label(None, None, "/x/python3", "/x/python3 -u make_pictures.py --steps 4"),
                         "Model in make_pictures.py")
        self.assertEqual(rt.model_label(None, None, "omlx-server", "omlx-server"), "Model in omlx-server")

    def test_summary_and_empty_text(self):
        self.assertEqual(rt.summarize([]), ("nothing loaded", 0))
        s, total = rt.summarize([{"state": "loaded", "gb": 25.0}, {"state": "loaded", "gb": 16.4}])
        self.assertEqual((s, total), ("2 models · 41 GB", 41.4))
        self.assertEqual(rt.runtimes_text([{"name": "Ollama", "installed": True, "running": True}]),
                         "Ollama is running, nothing loaded.")
        self.assertEqual(rt.runtimes_text([{"name": "Ollama", "running": True}, {"name": "LM Studio", "installed": True},
                                           {"name": "llama.cpp", "installed": True}]),
                         "Ollama is running, nothing loaded. LM Studio and llama.cpp are installed, not running.")
        self.assertEqual(rt.runtimes_text([]), "No local AI runtime found.")


class StopProcess(unittest.TestCase):
    def test_only_same_command_and_owner(self):
        kills = []
        kill = lambda pid, sig: kills.append((pid, sig))
        same = lambda cmd, **kw: "  501 python -m mlx_lm.server --model x\n"
        r = rt.stop_process(902, "python -m mlx_lm.server --model x", "Qwen", run=same, kill=kill, uid=501)
        self.assertTrue(r["ok"])
        self.assertEqual(kills, [(902, signal.SIGTERM)])
        kills.clear()
        other = lambda cmd, **kw: "501 /usr/bin/vim notes.txt\n"
        r = rt.stop_process(902, "python -m mlx_lm.server --model x", "Qwen", run=other, kill=kill, uid=501)
        self.assertFalse(r["ok"])
        self.assertEqual(kills, [])
        root = lambda cmd, **kw: "0 python -m mlx_lm.server --model x\n"
        r = rt.stop_process(902, "python -m mlx_lm.server --model x", "Qwen", run=root, kill=kill, uid=501)
        self.assertFalse(r["ok"])
        self.assertEqual(kills, [])
        gone = lambda cmd, **kw: ""
        r = rt.stop_process(902, "x", "Qwen", run=gone, kill=kill, uid=501)
        self.assertTrue(r["ok"])
        self.assertEqual(kills, [])
        r = rt.stop_process(902, "", "Qwen", run=same, kill=kill, uid=501)  # never seen: never signalled
        self.assertFalse(r["ok"])
        self.assertEqual(kills, [])


class FakeConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def widget(self, wid):
        return dict(self.values)


class FakeApp:
    def __init__(self, values=None):
        self.config = FakeConfig(values)
        self.datas = {}

    def data(self, wid):
        return self.datas.get(wid, {})


class Collect(unittest.TestCase):
    """The whole collect against the fixture process list, with every command and HTTP call faked."""

    def setUp(self):
        self.procs, self.args, self.fp = procs_fixture()
        self.http_calls = []

        def run(cmd, timeout=20, env=None, stderr=True):
            if cmd[0] == "lsof" and "-iTCP" in cmd:
                pid = cmd[-2]
                return {"902": "p902\nf5\nn127.0.0.1:8080\n", "903": "p903\nf3\nn*:8081\n", "708": fixture("ai_lsof_listen.txt")}.get(pid, "")
            if cmd[0] == "lsof":
                return {"905": fixture("ai_lsof_files_torch.txt"), "904": fixture("ai_lsof_files_plain.txt")}.get(cmd[2], "")
            if cmd[0] == "footprint":
                return ""
            if cmd[0] == "ps":
                return ""
            return ""

        def http_json(url, data=None, timeout=3, method=None, headers=None):
            self.http_calls.append((url, data))
            if url.endswith("/api/version"):
                return json.loads(fixture("ai_ollama_version.json"))
            if url.endswith("/api/ps"):
                return json.loads(fixture("ai_ollama_ps.json"))
            if url.endswith("/api/generate"):
                return {"done": True}
            if url == "http://127.0.0.1:8080/v1/models":
                return json.loads(fixture("ai_v1_models.json"))
            if url == "http://127.0.0.1:8301/v1/models":
                return {"data": [{"id": "org/Giant-oQ4e"}]}
            return None

        self.patches = [mock.patch.object(core, "processes", lambda: dict(self.procs)),
                        mock.patch.object(core, "process_args", lambda: dict(self.args)),
                        mock.patch.object(core, "footprints", lambda: dict(self.fp)),
                        mock.patch.object(core, "run", run),
                        mock.patch.object(core, "http_json", http_json),
                        mock.patch.object(rt, "installed_runtimes", lambda: ["Ollama", "llama.cpp"]),
                        mock.patch.object(rt, "lms_path", lambda: None),
                        mock.patch.object(rt.os, "getuid", lambda: 501)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_collect(self):
        w = rt.AIRuntimes(FakeApp())
        d = w.collect()
        by = {m["name"]: m for m in d["models"]}
        self.assertIn("qwen3:32b", by)
        self.assertEqual(by["qwen3:32b"]["who"], "Ollama")
        self.assertEqual(by["Qwen3-32B-4bit"]["runtime"], "MLX")
        self.assertEqual(by["Qwen3-32B-4bit"]["port"], 8080)
        self.assertEqual(by["Qwen3-32B-4bit"]["quant"], "4bit")
        self.assertEqual(by["Qwen3-32B-4bit"]["who"], "Visual Studio Code")
        self.assertEqual(by["Qwen3-32B-4bit"]["base_url"], "http://127.0.0.1:8080/v1")
        self.assertEqual(by["Giant-oQ4e"]["runtime"], "oMLX")          # name from its /v1/models
        self.assertEqual(by["Giant-oQ4e"]["who"], "Claude Code")
        self.assertEqual(by["Giant-oQ4e"]["port"], 8301)
        self.assertEqual(by["gemma-3-27b-it-Q4_K_M"]["quant"], "Q4_K_M")
        self.assertEqual(by["Model in make_pictures.py"]["runtime"], "PyTorch")
        self.assertFalse(by["FluidVoice model"]["can_stop"])           # an app's helper: quit the app instead
        self.assertFalse(by["phi-4-Q8_0"]["can_stop"])                 # another user's process
        names = set(by)
        self.assertFalse(any("download" in n for n in names))
        # The Memory widget's map: every model process, the Ollama runner included.
        self.assertEqual(set(d["pids"]), {"708", "801", "902", "903", "905", "1002", "1003", "1100"})
        self.assertEqual(d["pids"]["708"], "Giant-oQ4e · Claude Code")
        self.assertTrue(d["pids"]["1002"].startswith("qwen3:32b"))
        self.assertTrue(d["pids"]["1003"].startswith("llama3.2"))
        self.assertTrue(d["summary"].startswith(f"{len(d['models'])} models · "))
        self.assertNotIn("cmd", by["Qwen3-32B-4bit"])                  # command lines stay inside Sightline

    def test_ask_servers_off(self):
        w = rt.AIRuntimes(FakeApp({"ask_servers": False}))
        d = w.collect()
        self.assertFalse(any("/v1/models" in u for u, _ in self.http_calls))
        names = {m["name"] for m in d["models"]}
        self.assertIn("Model in omlx-server", names)

    def test_stop_actions(self):
        w = rt.AIRuntimes(FakeApp())
        w.collect()
        r = w.stop({"runtime": "Ollama", "id": "qwen3:32b"})
        self.assertTrue(r["ok"])
        self.assertIn(("http://127.0.0.1:11434/api/generate", {"model": "qwen3:32b", "keep_alive": 0}), self.http_calls)
        self.assertFalse(w.stop({"runtime": "MLX", "id": "12345"})["ok"])            # not one of ours: refused
        self.assertFalse(w.stop({"runtime": "llama.cpp", "id": "1100"})["ok"])       # another user's
        with mock.patch.object(rt, "stop_process", return_value={"ok": True, "message": "x"}) as sp:
            r = w.stop({"runtime": "MLX", "id": "902"})
            sp.assert_called_once_with(902, self.args["902"], "Qwen3-32B-4bit")
        with mock.patch.object(rt, "stop_process", return_value={"ok": True, "message": "x"}) as sp:
            r = w.stop_all({})
            stopped = sorted(c.args[0] for c in sp.call_args_list)
            self.assertEqual(stopped, [708, 902, 903, 905])
            self.assertIn("Couldn't stop", r["message"])


if __name__ == "__main__":
    unittest.main()
