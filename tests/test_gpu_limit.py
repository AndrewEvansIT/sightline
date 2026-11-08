"""GPU memory: steps, warnings, the helper's allowed list, the installer's shell math, and the set/install flows.
All with mocks: nothing here changes the real limit or installs anything."""

import os
import subprocess
import tempfile
import unittest
from unittest import mock

from sightline.widgets import gpu_limit as gl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(ROOT, "scripts", "install-gpu-helper.sh")
TEMPLATE = os.path.join(ROOT, "scripts", "gpu-helper.sh")
MB512 = 512 * 1024


class Steps(unittest.TestCase):
    def test_512gb(self):
        self.assertEqual(gl.step_values(MB512),
                         [(0, "Normal", 75), (419430, "80%", 80), (445644, "85%", 85), (471859, "90%", 90), (483328, "Max", 92)])

    def test_max_never_100_and_leaves_room(self):
        for gb in (24, 32, 36, 48, 64, 96, 128, 192, 256, 512):
            total = gb * 1024
            vals = [mb for mb, _, _ in gl.step_values(total)]
            self.assertEqual(vals[0], 0)
            self.assertEqual(vals, sorted(vals), gb)
            for mb in vals[1:]:
                self.assertGreater(mb, total * 75 // 100)
                self.assertGreaterEqual(total - mb, gl.MIN_LEFT_MB, gb)

    def test_small_macs(self):
        self.assertEqual(gl.step_values(16 * 1024), [(0, "Normal", 75)])
        self.assertEqual([n for _, n, _ in gl.step_values(24 * 1024)], ["Normal", "80%", "Max"])
        self.assertEqual([n for _, n, _ in gl.step_values(128 * 1024)], ["Normal", "80%", "85%", "90%", "Max"])

    def test_rows(self):
        rows = gl.steps(MB512)
        self.assertEqual(rows[0], {"mb": 0, "name": "Normal", "pct": 75, "gb": 384, "leaves_gb": 128})
        self.assertEqual(rows[-1]["gb"], 472)
        self.assertEqual(rows[-1]["leaves_gb"], 40)

    def test_rows_from_installed_helper(self):
        rows = gl.steps(MB512, [0, 419430, 445644, 471859, 483328])
        self.assertEqual([r["name"] for r in rows], ["Normal", "80%", "85%", "90%", "Max"])
        # A helper installed when the Mac had other values (or by an older version): named by percent, last is Max.
        rows = gl.steps(MB512, [0, 400000, 480000])
        self.assertEqual([r["name"] for r in rows], ["Normal", "76%", "Max"])

    def test_shell_matches_python(self):
        for gb in (8, 16, 24, 32, 36, 48, 64, 96, 128, 192, 256, 512):
            total = gb * 1024
            out = subprocess.run(["/bin/sh", INSTALLER, "--print-steps", str(total)], capture_output=True, text=True,
                                 check=True).stdout.split()
            self.assertEqual([int(v) for v in out], [mb for mb, _, _ in gl.step_values(total)], gb)


class Warnings(unittest.TestCase):
    def test_only_above_90(self):
        self.assertEqual(gl.warnings_for(471859, MB512, ["Qwen"], 300), [])
        self.assertEqual(gl.warnings_for(0, MB512, ["Qwen"], 300), [])

    def test_models_and_memory(self):
        w = gl.warnings_for(483328, MB512, ["Qwen3-32B-4bit"], 20)
        self.assertEqual(w, ["Model running: Qwen3-32B-4bit."])
        w = gl.warnings_for(483328, MB512, [], 120)
        self.assertEqual(w, ["120 GB is in use; this step leaves about 40 GB for macOS and apps."])
        self.assertEqual(gl.warnings_for(483328, MB512, [], 30), [])
        self.assertEqual(gl.warnings_for(483328, MB512, [], None), [])


class HelperFiles(unittest.TestCase):
    def test_parse_allowed(self):
        with open(TEMPLATE) as f:
            text = f.read().replace("@ALLOWED@", "0 419430 445644 471859 483328")
        self.assertEqual(gl.parse_allowed(text), [0, 419430, 445644, 471859, 483328])
        self.assertEqual(gl.parse_allowed('ALLOWED="@ALLOWED@"'), [])
        self.assertEqual(gl.parse_allowed(""), [])

    def test_build_and_refusals(self):
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run(["/bin/sh", INSTALLER, "--build", d, "someone"], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            with open(os.path.join(d, "sudoers")) as f:
                sudoers = f.read()
            steps = subprocess.run(["/bin/sh", INSTALLER, "--print-steps"], capture_output=True, text=True).stdout.split()
            for v in steps:
                self.assertIn(f"{gl.HELPER} {v}", sudoers)
            self.assertIn("someone ALL = (root) NOPASSWD: SIGHTLINE_GPU_LIMIT", sudoers)
            self.assertNotIn("*", sudoers.split("Cmnd_Alias", 1)[1])  # no wildcards: only the exact commands
            with open(os.path.join(d, "helper")) as f:
                helper = f.read()
            self.assertEqual(gl.parse_allowed(helper), [int(v) for v in steps])
            # The helper refuses anything that isn't a step, before touching sysctl.
            for bad in (["12345"], ["0;id"], ["-1"], [], ["0", "1"]):
                r = subprocess.run(["/bin/sh", os.path.join(d, "helper")] + bad, capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, bad)
            bad_user = subprocess.run(["/bin/sh", INSTALLER, "--build", d, "x;rm"], capture_output=True, text=True)
            self.assertEqual(bad_user.returncode, 2)

    def test_installer_needs_root(self):
        if os.getuid() == 0:
            self.skipTest("running as root")
        p = subprocess.run(["/bin/sh", INSTALLER, "someone"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("root", p.stderr)

    def test_applescript_quoting(self):
        s = gl.applescript_install('/Apps/My "Side" Panel.app/scripts/install-gpu-helper.sh', "me")
        self.assertIn('quoted form of "/Apps/My \\"Side\\" Panel.app/scripts/install-gpu-helper.sh"', s)
        self.assertTrue(s.endswith("with administrator privileges"))


class FakeConfig:
    def widget(self, wid):
        return {}


class FakeApp:
    def __init__(self, datas=None):
        self.config = FakeConfig()
        self.datas = datas or {}

    def data(self, wid):
        return self.datas.get(wid, {})


ALLOWED = [0, 419430, 445644, 471859, 483328]


class Flows(unittest.TestCase):
    def widget(self, models=(), used=20):
        app = FakeApp({"memory": {"used_gb": used},
                       "ai_runtimes": {"models": [{"name": n, "state": "loaded"} for n in models]}})
        return gl.GPULimit(app)

    def test_set_without_helper(self):
        w = self.widget()
        with mock.patch.object(w, "allowed", lambda: []):
            r = w.set({"mb": 0})
        self.assertFalse(r["ok"])
        self.assertIn("Install", r["message"])

    def test_set_refuses_other_values(self):
        w = self.widget()
        with mock.patch.object(w, "allowed", lambda: ALLOWED), mock.patch.object(w, "apply") as apply:
            self.assertFalse(w.set({"mb": 524288})["ok"])
            self.assertFalse(w.set({"mb": "lots"})["ok"])
            apply.assert_not_called()

    def test_set_max_needs_confirm_with_models(self):
        w = self.widget(models=["Qwen3-32B-4bit"])
        with mock.patch.object(w, "allowed", lambda: ALLOWED), mock.patch.object(gl, "total_mb_of", lambda run=None: MB512), \
                mock.patch.object(w, "apply", return_value={"ok": True, "message": "done"}) as apply:
            r = w.set({"mb": 483328})
            self.assertFalse(r["ok"])
            self.assertTrue(r["confirm"])
            self.assertIn("Qwen3-32B-4bit", r["message"])
            apply.assert_not_called()
            r = w.set({"mb": 483328, "confirm": True})
            self.assertTrue(r["ok"])
            apply.assert_called_once_with(483328)
            apply.reset_mock()
            self.assertTrue(w.set({"mb": 471859})["ok"])  # 90%: no warning needed
            apply.assert_called_once_with(471859)

    def test_apply_reads_back(self):
        w = self.widget()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(gl, "read_mb", lambda run=None: 471859), mock.patch.object(gl, "total_mb_of", lambda run=None: MB512):
            r = w.apply(471859, run=fake_run)
            self.assertTrue(r["ok"])
            self.assertEqual(calls, [["sudo", "-n", gl.HELPER, "471859"]])
            r = w.apply(483328, run=fake_run)  # reads back something else
            self.assertFalse(r["ok"])
            self.assertIn("Didn't take", r["message"])

        def needs_password(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, "", "sudo: a password is required")

        with mock.patch.object(gl, "read_mb", lambda run=None: 0), mock.patch.object(gl, "total_mb_of", lambda run=None: MB512):
            r = w.apply(483328, run=needs_password)
            self.assertFalse(r["ok"])
            self.assertIn("password", r["message"])

    def test_install_cancelled(self):
        w = self.widget()
        cancelled = subprocess.CompletedProcess([], 1, "", "execution error: User canceled. (-128)")
        with mock.patch.object(gl.subprocess, "run", return_value=cancelled) as run:
            r = w.install({})
        self.assertFalse(r["ok"])
        self.assertEqual(r["message"], "Install cancelled.")
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[:2], ["osascript", "-e"])
        self.assertIn("with administrator privileges", cmd[2])
        self.assertIn("install-gpu-helper.sh", cmd[2])

    def test_collect(self):
        w = self.widget(models=["Big-4bit"], used=300)
        with mock.patch.object(gl, "total_mb_of", lambda run=None: MB512), mock.patch.object(gl, "read_mb", lambda run=None: 483328), \
                mock.patch.object(w, "allowed", lambda: []):
            d = w.collect()
        self.assertEqual(d["step"], "Max")
        self.assertEqual(d["gb"], 472)
        self.assertFalse(d["installed"])
        self.assertEqual(list(d["warnings"]), ["483328"])
        self.assertIn("password", d["note"])
        with mock.patch.object(gl, "total_mb_of", lambda run=None: MB512), mock.patch.object(gl, "read_mb", lambda run=None: 0), \
                mock.patch.object(w, "allowed", lambda: []):
            d = w.collect()
        self.assertEqual((d["step"], d["gb"]), ("Normal", 384))


if __name__ == "__main__":
    unittest.main()
