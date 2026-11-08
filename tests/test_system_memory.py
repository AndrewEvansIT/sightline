"""System and Memory parsers, fed saved command output."""

import unittest

from sightline.widgets import memory, system

TOP = """Processes: 800 total
CPU usage: 4.10% user, 3.00% sys, 92.90% idle
SharedLibs: 1G resident
CPU usage: 20.50% user, 9.50% sys, 70.00% idle
"""

IOREG_GPU = '| "PerformanceStatistics" = {"In use system memory"=123,"Device Utilization %"=37,"Renderer Utilization %"=30}'

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                             1000.
Pages active:                           5000.
Pages purgeable:                         100.
File-backed pages:                      2000.
Anonymous pages:                        6000.
Pages wired down:                       1500.
Pages occupied by compressor:            500.
"""


def fake_run(outputs):
    def run(cmd, timeout=20, env=None, stderr=True):
        key = " ".join(cmd)
        for k, v in outputs.items():
            if key.startswith(k):
                return v
        return ""
    return run


class SystemTest(unittest.TestCase):
    def test_cpu_uses_second_sample(self):
        self.assertEqual(system.parse_top_cpu(TOP), 30)

    def test_cpu_missing(self):
        self.assertIsNone(system.parse_top_cpu("nothing"))

    def test_gpu(self):
        self.assertEqual(system.parse_gpu_util(IOREG_GPU), 37)
        self.assertIsNone(system.parse_gpu_util(""))


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.run = fake_run({
            "sysctl -n hw.memsize": str(16 * 2**30),
            "vm_stat": VM_STAT,
            "sysctl -n vm.page_reusable_count": "200",
            "sysctl -n vm.page_booter_count": "50",
            "sysctl -n vm.swapusage": "total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)",
            "sysctl -n kern.memorystatus_vm_pressure_level": "2",
        })

    def test_counts(self):
        total, used, wired, reclaim, free, swap = memory.mem_counts(self.run)
        page = 16384
        self.assertEqual(total, 16 * 2**30)
        # app = anonymous − purgeable − reclaimable; used = app + wired(+booter) + compressor
        self.assertEqual(used, ((6000 - 100 - 200) + (1500 + 50) + 500) * page)
        self.assertEqual(reclaim, 200 * page)
        self.assertEqual(free[0], (1000 * page, "Empty"))
        self.assertEqual(swap, 512 * 2**20)

    def test_pressure(self):
        self.assertEqual(memory.pressure(self.run), "warn")

    def test_pie_adds_up(self):
        g = lambda name, gb, kind="app": {"name": name, "bytes": gb * 2**30, "kind": kind}
        ranked = [g("A", 10), g("B", 5), g("macOS services", 4, "macos"), g("C", 3), g("D", 2), g("E", 1), g("tiny", 0.1)]
        used, reclaim = 30 * 2**30, 2 * 2**30
        parts = memory.pie_parts(ranked, used, reclaim)
        self.assertEqual([p["name"] for p in parts[:4]], ["A", "B", "C", "D"])
        self.assertAlmostEqual(sum(p["gb"] for p in parts), 32, delta=0.3)

    def test_project_and_script_folder(self):
        self.assertEqual(memory.script_folder("node /Users/x/app/node_modules/.bin/vite --port 5173"), "/Users/x/app")
        self.assertEqual(memory.script_folder("node --inspect"), "")

    def test_dev_roots(self):
        procs = {"10": ("1", "501", "/usr/local/bin/node"), "11": ("10", "501", "/bin/zsh")}
        args = {"10": "node /p/node_modules/.bin/vite", "11": 'zsh -c "node_modules/.bin/tsc"'}
        self.assertEqual(memory.dev_roots_of(args, procs, "501"), {"10": "vite"})


if __name__ == "__main__":
    unittest.main()
