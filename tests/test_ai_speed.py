"""Speed test: parsers, the numbers, and a full run against a fake OpenAI-compatible server started here.
Never talks to a real model server."""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from sightline.history import History
from sightline.widgets import ai_speed as sp

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class FakeServer:
    """A tiny streaming /v1/chat/completions on a random port. usage=False leaves out the usage chunk."""

    def __init__(self, tokens=40, usage=True, delay=0.002, first_delay=0.05):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append((self.path, body))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                time.sleep(first_delay)
                for i in range(tokens):
                    chunk = {"choices": [{"index": 0, "delta": {"content": f" w{i}"}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(delay)
                if usage:
                    u = {"choices": [], "usage": {"prompt_tokens": 498, "completion_tokens": tokens, "total_tokens": 498 + tokens}}
                    self.wfile.write(f"data: {json.dumps(u)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class Clock:
    def __init__(self, times):
        self.times = list(times)

    def __call__(self):
        return self.times.pop(0)


class Parsers(unittest.TestCase):
    def test_prompt_is_fixed(self):
        self.assertEqual(sp.build_prompt(), sp.build_prompt())
        self.assertEqual(sp.PROMPT, sp.build_prompt())
        words = len(sp.PROMPT.split())
        self.assertTrue(330 < words < 450, words)  # about 500 tokens

    def test_is_local(self):
        self.assertTrue(sp.is_local("http://127.0.0.1:11434/v1"))
        self.assertTrue(sp.is_local("http://localhost:1234/v1"))
        self.assertTrue(sp.is_local("http://[::1]:8080/v1"))
        self.assertFalse(sp.is_local("http://192.168.1.5:8080/v1"))
        self.assertFalse(sp.is_local("https://api.example.com/v1"))
        self.assertFalse(sp.is_local("http://127.0.0.1.example.com/v1"))
        self.assertFalse(sp.is_local(""))

    def test_parse_stream_with_usage(self):
        clock = Clock([10.0, 10.1, 10.2, 10.3, 11.0])
        s = sp.parse_stream(fixture("ai_sse_usage.txt").splitlines(), clock=clock)
        self.assertEqual(s["first"], 10.0)
        self.assertEqual(s["chunks"], 4)
        self.assertEqual(s["usage"]["prompt_tokens"], 512)
        self.assertEqual(s["text"], "Once upon a time")

    def test_parse_stream_without_usage(self):
        s = sp.parse_stream([l.encode() for l in fixture("ai_sse_nousage.txt").splitlines()])
        self.assertEqual(s["chunks"], 3)
        self.assertIsNone(s["usage"])

    def test_chunk_text_reasoning_and_completions(self):
        self.assertEqual(sp.chunk_text({"choices": [{"delta": {"reasoning_content": "hm"}}]}), "hm")
        self.assertEqual(sp.chunk_text({"choices": [{"text": "hi"}]}), "hi")
        self.assertEqual(sp.chunk_text({"choices": []}), "")

    def test_compute(self):
        stream = {"first": 101.0, "end": 106.0, "chunks": 256, "usage": {"prompt_tokens": 500, "completion_tokens": 256}}
        r = sp.compute(100.0, stream)
        self.assertEqual(r["ttft_s"], 1.0)
        self.assertEqual(r["prompt_tps"], 500.0)
        self.assertEqual(r["gen_tps"], 51.0)     # 255 tokens after the first, in 5 s
        self.assertEqual(r["total_s"], 6.0)
        self.assertFalse(r["approx"])

    def test_compute_approx(self):
        stream = {"first": 100.5, "end": 102.5, "chunks": 101, "usage": None}
        r = sp.compute(100.0, stream, prompt_chars=2000)
        self.assertTrue(r["approx"])
        self.assertEqual(r["prompt_tokens"], 500)
        self.assertEqual(r["gen_tps"], 50.0)
        self.assertIsNone(sp.compute(100.0, {"first": None, "end": 101, "chunks": 0, "usage": None}))

    def test_energy(self):
        self.assertEqual(sp.energy([100, 200, None, 150], 36.0), (1.5, 150.0))
        self.assertEqual(sp.energy([], 10), (None, None))
        self.assertEqual(sp.energy([None], 10), (None, None))


class FakeConfig:
    def widget(self, wid):
        return {}


class FakeApp:
    def __init__(self, history, datas):
        self.config = FakeConfig()
        self.history = history
        self.datas = datas
        self.poked = threading.Event()

    def data(self, wid):
        return self.datas.get(wid, {})

    def poke(self, wid):
        self.poked.set()


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = History(path=os.path.join(self.tmp.name, "h.db"))

    def tearDown(self):
        self.history.db.close()
        self.tmp.cleanup()

    def app(self, server, power=False):
        datas = {"system": {"info": {"chip": "Apple M3 Ultra", "memory_gb": 512}},
                 "ai_runtimes": {"models": [{"name": "Fake-7B-4bit", "runtime": "MLX", "state": "loaded",
                                             "base_url": server.url, "api_model": "org/Fake-7B-4bit"},
                                            {"name": "Script", "runtime": "PyTorch", "state": "loaded", "base_url": None}]}}
        if power:
            datas["power"] = {"system_w": 100}
        return FakeApp(self.history, datas)

    def wait(self, app):
        self.assertTrue(app.poked.wait(15), "speed test didn't finish")

    def test_full_run_with_usage(self):
        srv = FakeServer(tokens=40, usage=True)
        try:
            app = self.app(srv)
            w = sp.SpeedTest(app)
            d = w.collect()
            self.assertEqual([t["model"] for t in d["targets"]], ["org/Fake-7B-4bit"])
            t = d["targets"][0]
            r = w.run({"base_url": t["base_url"], "model": t["model"], "runtime": t["runtime"], "name": t["name"]})
            self.assertTrue(r["ok"], r)
            self.assertFalse(w.run({"base_url": t["base_url"], "model": t["model"]})["ok"])  # one at a time
            self.wait(app)
            d = w.collect()
            last = d["last"]
            self.assertIsNone(d["running"])
            self.assertFalse(last["approx"])
            self.assertEqual(last["completion_tokens"], 40)
            self.assertEqual(last["prompt_tokens"], 498)
            self.assertGreater(last["gen_tps"], 0)
            self.assertGreaterEqual(last["ttft_s"], 0.05)
            self.assertEqual(last["chip"], "Apple M3 Ultra")
            self.assertEqual(last["memory_gb"], 512)
            self.assertIsNone(last["wh"])
            for k in ("model", "runtime", "chip", "memory_gb", "ttft_s", "prompt_tps", "gen_tps", "wh", "approx", "ts"):
                self.assertIn(k, last)
            self.assertEqual(len(d["history"]), 1)
            self.assertEqual(d["history"][0]["subject"], "org/Fake-7B-4bit")
            path, body = srv.requests[0]
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(body["max_tokens"], 256)
            self.assertEqual(body["temperature"], 0)
            self.assertTrue(body["stream"])
            self.assertEqual(body["stream_options"], {"include_usage": True})
            self.assertEqual(body["messages"][0]["content"], sp.PROMPT)
        finally:
            srv.close()

    def test_run_without_usage_is_approx(self):
        srv = FakeServer(tokens=20, usage=False)
        try:
            app = self.app(srv)
            w = sp.SpeedTest(app)
            w.run({"base_url": srv.url, "model": "org/Fake-7B-4bit"})
            self.wait(app)
            last = w.collect()["last"]
            self.assertTrue(last["approx"])
            self.assertEqual(last["completion_tokens"], 20)
        finally:
            srv.close()

    def test_power_sampled(self):
        srv = FakeServer(tokens=30, usage=True, delay=0.01)
        try:
            app = self.app(srv, power=True)
            w = sp.SpeedTest(app)
            with mock.patch.object(sp.core, "helper", lambda name: "/fake/power"), \
                    mock.patch.object(sp, "watts_now", lambda helper: (time.sleep(0.02), 120.0)[1]):
                w.run({"base_url": srv.url, "model": "org/Fake-7B-4bit"})
                self.wait(app)
            last = w.collect()["last"]
            self.assertEqual(last["avg_w"], 120.0)
            self.assertAlmostEqual(last["wh"], round(120.0 * last["total_s"] / 3600, 4), places=4)
        finally:
            srv.close()

    def test_refuses_remote_and_reports_errors(self):
        srv = FakeServer()
        srv.close()  # nothing listening there now
        app = self.app(srv)
        w = sp.SpeedTest(app)
        self.assertFalse(w.run({"base_url": "http://10.0.0.2:8080/v1", "model": "x"})["ok"])
        self.assertFalse(w.run({"base_url": "", "model": "x"})["ok"])
        self.assertTrue(w.run({"base_url": srv.url, "model": "x"})["ok"])
        self.wait(app)
        last = w.collect()["last"]
        self.assertIn("error", last)
        self.assertEqual(self.history.runs("speed"), [])


if __name__ == "__main__":
    unittest.main()
