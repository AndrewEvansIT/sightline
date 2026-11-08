"""AI coding agents: process matching, sessions, prices, and the incremental token scan (synthetic logs only)."""

import datetime
import os
import shutil
import tempfile
import time
import unittest

os.environ.setdefault("SIGHTLINE_HOME", tempfile.mkdtemp(prefix="sightline-test-"))

from sightline.widgets import agents  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
# Tests run at noon on 2026-03-10 in Denver. Fixture lines are at 12:00Z that day, and one two days before.


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(text)


def read(name):
    with open(os.path.join(FIX, name)) as f:
        return f.read()


class TzMixin:
    def setUp(self):
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "America/Denver"
        time.tzset()
        self.now = time.mktime(datetime.datetime(2026, 3, 10, 12, 0, 0).timetuple())
        self.tmp = tempfile.mkdtemp(prefix="sightline-agents-")

    def tearDown(self):
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()
        shutil.rmtree(self.tmp, ignore_errors=True)


class MatchTest(unittest.TestCase):
    def test_executables(self):
        self.assertEqual(agents.agent_of("/Users/x/.local/bin/claude", "claude"), "claude")
        self.assertEqual(agents.agent_of("/opt/homebrew/bin/codex", "codex exec"), "codex")
        self.assertEqual(agents.agent_of("/x/cursor-agent", "cursor-agent"), "cursor")
        self.assertEqual(agents.agent_of("/x/goose", "goose session"), "goose")

    def test_node_and_python_scripts(self):
        self.assertEqual(agents.agent_of("/opt/homebrew/bin/node",
                                         "node /opt/homebrew/lib/node_modules/@anthropic-ai/claude-code/cli.js"), "claude")
        self.assertEqual(agents.agent_of("/opt/homebrew/bin/node", "node /opt/homebrew/bin/gemini --yolo"), "gemini")
        self.assertEqual(agents.agent_of("/usr/bin/python3", "/usr/bin/python3 /Users/x/.local/bin/aider --model x"), "aider")
        self.assertEqual(agents.agent_of("/opt/homebrew/bin/node", "node /x/node_modules/@github/copilot/index.js"), "copilot")

    def test_not_agents(self):
        self.assertIsNone(agents.agent_of("/Applications/Claude.app/Contents/MacOS/Claude", "Claude"))
        self.assertIsNone(agents.agent_of("/Applications/Claude.app/Contents/Helpers/disclaimer",
                                          "disclaimer --pgroup -- /x/claude-code/1/claude"))
        self.assertIsNone(agents.agent_of("/opt/homebrew/bin/node", "node /x/node_modules/.bin/vite"))
        self.assertIsNone(agents.agent_of("/bin/zsh", "zsh -c claude"))

    def test_sessions_top_most_only(self):
        procs = {"10": ("1", "501", "/opt/homebrew/bin/node"), "11": ("10", "501", "/x/codex"),
                 "20": ("1", "501", "/x/claude"), "21": ("20", "501", "/bin/zsh"), "22": ("21", "501", "/x/claude"),
                 "30": ("1", "0", "/x/claude")}
        args = {"10": "node /x/node_modules/@openai/codex/bin/codex.js", "11": "codex", "20": "claude", "21": "zsh",
                "22": "claude -p hi"}
        found = sorted(agents.find_sessions(procs, args, "501"))
        self.assertEqual(found, [("10", "codex"), ("20", "claude")])

    def test_etime(self):
        self.assertEqual(agents.parse_etime("05:07"), 307)
        self.assertEqual(agents.parse_etime("02:00:01"), 7201)
        self.assertEqual(agents.parse_etime("3-01:00:00"), 3 * 86400 + 3600)
        self.assertIsNone(agents.parse_etime(""))


class PriceTest(unittest.TestCase):
    def test_lookup(self):
        self.assertEqual(agents.price_of("claude-opus-5-5")["input"], 4.0)
        self.assertEqual(agents.price_of("claude-opus-4-6-20260101"), agents.PRICES["claude-opus-4-6"])
        self.assertEqual(agents.price_of("claude-opus-5-5-20260401"), agents.PRICES["claude-opus-5-5"])
        self.assertIsNone(agents.price_of("some-unknown-model"))

    def test_cost(self):
        u = {"input": 1_000_000, "output": 1_000_000, "cache_write": 0, "cache_write_1h": 0, "cache_read": 1_000_000}
        self.assertAlmostEqual(agents.cost("claude-opus-5-5", u), 4.0 + 20.0 + 0.2)
        self.assertIsNone(agents.cost("nope", u))

    def test_every_price_has_every_field(self):
        for name, row in agents.PRICES.items():
            self.assertEqual(set(row), set(agents.FIELDS), name)


class ClaudeScanTest(TzMixin, unittest.TestCase):
    def scanner(self):
        return agents.TokenScanner(claude_dirs=os.path.join(self.tmp, "projects"), codex_dir=os.path.join(self.tmp, "sessions"))

    def test_today_dedup_and_models(self):
        write(os.path.join(self.tmp, "projects", "-tmp-example", "s-1.jsonl"), read("agents_claude.jsonl"))
        s = self.scanner()
        s.scan(self.now)
        totals = s.totals
        opus = totals[("claude", "claude-opus-5-5")]
        # msg_A appears on two lines but counts once; msg_OLD (two days ago) and <synthetic> don't count.
        self.assertEqual(opus, {"input": 10, "output": 200, "cache_write": 0, "cache_write_1h": 1000, "cache_read": 5000})
        haiku = totals[("claude", "claude-haiku-5-5")]
        self.assertEqual(haiku, {"input": 3, "output": 50, "cache_write": 400, "cache_write_1h": 0, "cache_read": 0})
        self.assertEqual(len(totals), 2)
        r = s.report()
        self.assertEqual(r["total"], 6210 + 453)
        expected = (10 * 4 + 200 * 20 + 1000 * 8 + 5000 * 0.2 + 3 * 0.1 + 50 * 0.5 + 400 * 0.125) / 1e6
        self.assertAlmostEqual(r["usd"], round(expected, 2))
        self.assertEqual(r["unpriced"], [])

    def test_keeps_no_text(self):
        write(os.path.join(self.tmp, "projects", "p", "s.jsonl"), read("agents_claude.jsonl"))
        s = self.scanner()
        s.scan(self.now)
        blob = repr(s.__dict__) + repr(s.report())
        for secret in ("PROMPT TEXT", "REPLY TEXT", "TOOL OUTPUT", "TOOL INPUT", "/tmp/example"):
            self.assertNotIn(secret, blob)

    def test_incremental_and_partial_lines(self):
        path = os.path.join(self.tmp, "projects", "p", "s.jsonl")
        lines = read("agents_claude.jsonl").splitlines(True)
        write(path, "".join(lines[:3]))
        s = self.scanner()
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 6210)
        # Half a line (still being written) is left for later; the rest of the file is read once.
        rest = "".join(lines[3:])
        cut = rest.index("claude-haiku-5-5") + 5
        write(path, rest[:cut])
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 6210)
        write(path, rest[cut:])
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 6210 + 453)
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 6210 + 453)

    def test_subagent_files_and_new_day(self):
        write(os.path.join(self.tmp, "projects", "p", "s-1", "subagents", "agent-1.jsonl"), read("agents_claude.jsonl"))
        s = self.scanner()
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 6663)
        s.scan(self.now + 86400)  # the next day: totals start again; old lines aren't counted again
        self.assertEqual(s.report()["total"], 0)

    def test_old_files_skipped(self):
        path = os.path.join(self.tmp, "projects", "p", "s.jsonl")
        write(path, read("agents_claude.jsonl"))
        os.utime(path, (self.now - 3 * 86400, self.now - 3 * 86400))
        s = self.scanner()
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 0)
        self.assertEqual(s.files, {})


class CodexScanTest(TzMixin, unittest.TestCase):
    def scanner(self):
        return agents.TokenScanner(claude_dirs=os.path.join(self.tmp, "projects"), codex_dir=os.path.join(self.tmp, "sessions"))

    def test_running_total_growth(self):
        write(os.path.join(self.tmp, "sessions", "2026", "03", "10", "rollout-x.jsonl"), read("agents_codex.jsonl"))
        s = self.scanner()
        s.scan(self.now)
        u = s.totals[("codex", "gpt-6.1-sol")]
        # Final running total: 2500 input of which 1800 cached, 250 output. The repeated event adds nothing.
        self.assertEqual(u, {"input": 700, "output": 250, "cache_write": 0, "cache_write_1h": 0, "cache_read": 1800})
        self.assertEqual(s.report()["total"], 2750)
        self.assertEqual([w["minutes"] for w in s.limits["windows"]], [300, 10080])
        self.assertNotIn("PROMPT TEXT", repr(s.__dict__))

    def test_events_before_today_count_as_baseline(self):
        text = read("agents_codex.jsonl").replace("2026-03-10T12:00:05.000Z", "2026-03-08T12:00:05.000Z")
        write(os.path.join(self.tmp, "sessions", "2026", "03", "08", "rollout-y.jsonl"), text)
        s = self.scanner()
        s.scan(self.now)
        # The first call happened two days ago; only today's growth (2750 - 1100) counts.
        self.assertEqual(s.report()["total"], 1650)

    def test_reset_total_counts_last_call(self):
        lines = read("agents_codex.jsonl").splitlines(True)
        reset = lines[6].replace('"total_token_usage":{"input_tokens":2500,"cached_input_tokens":1800',
                                 '"total_token_usage":{"input_tokens":10,"cached_input_tokens":0') \
                        .replace('"output_tokens":250,"reasoning_output_tokens":40,"total_tokens":2750',
                                 '"output_tokens":5,"reasoning_output_tokens":0,"total_tokens":15')
        write(os.path.join(self.tmp, "sessions", "2026", "03", "10", "r.jsonl"), "".join(lines[:6]) + reset)
        s = self.scanner()
        s.scan(self.now)
        self.assertEqual(s.report()["total"], 1100 + 1650)


class WindowTest(TzMixin, unittest.TestCase):
    def test_day_window_in_utc(self):
        label, lo, hi = agents.day_window(self.now)
        self.assertEqual(label, "2026-03-10")
        self.assertEqual((lo, hi), ("2026-03-10T06:00:00", "2026-03-11T06:00:00"))  # Denver is on summer time (UTC-6)

    def test_utc_key(self):
        self.assertEqual(agents.utc_key("2026-03-10T12:00:05.123Z"), "2026-03-10T12:00:05")
        self.assertEqual(agents.utc_key("2026-03-10T06:00:05-06:00"), "2026-03-10T12:00:05")
        self.assertEqual(agents.utc_key("nope"), "")
        self.assertEqual(agents.utc_key(None), "")


if __name__ == "__main__":
    unittest.main()
