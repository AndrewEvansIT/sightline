"""System: processor, graphics, heat and fans, power mode, uptime. The Mac's vital signs."""

import json
import os
import re
import time

from .. import core
from . import Widget


def parse_top_cpu(out):
    """Busy % from the last 'CPU usage' line of `top -l 2` (the first sample is since boot)."""
    lines = [l for l in out.splitlines() if l.startswith("CPU usage")]
    if not lines:
        return None
    m = re.search(r"([\d.]+)% idle", lines[-1])
    return round(100 - float(m.group(1))) if m else None


def parse_gpu_util(out):
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    return int(m.group(1)) if m else None


def chip_info():
    brand = core.run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    cores = core.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"]).strip()
    eff = core.run(["sysctl", "-n", "hw.perflevel1.physicalcpu"]).strip()
    mem = int(core.run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    gpu = re.search(r'"gpu-core-count"\s*=\s*(\d+)', core.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"]))
    model = core.run(["sysctl", "-n", "hw.model"]).strip()
    return {"chip": brand or "Mac", "model": model, "p_cores": int(cores) if cores.isdigit() else None,
            "e_cores": int(eff) if eff.isdigit() else None, "gpu_cores": int(gpu.group(1)) if gpu else None,
            "memory_gb": round(mem / 2**30)}


def boot_time():
    m = re.search(r"sec = (\d+)", core.run(["sysctl", "-n", "kern.boottime"]))
    return int(m.group(1)) if m else None


class System(Widget):
    id = "system"
    title = "System"
    category = "system"
    description = "Processor and graphics load, chip temperature, fans, power mode and uptime."
    interval = 5
    size = "l"

    def __init__(self, app):
        super().__init__(app)
        self.info = None

    def collect(self):
        if not self.info:
            self.info = chip_info()
        cpu = parse_top_cpu(core.run(["top", "-l", "2", "-n", "0", "-s", "1"]))
        gpu = parse_gpu_util(core.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"]))
        heat = {}
        smc = core.helper("smc")
        if smc:
            try:
                heat = json.loads(core.run([smc], timeout=5, stderr=False) or "{}")
            except ValueError:
                heat = {}
        temp = round(heat["cpu_max_c"]) if heat.get("cpu_max_c") else None
        self.record("cpu", cpu)
        self.record("gpu", gpu)
        self.record("temp", temp)
        therm = core.run(["pmset", "-g", "therm"])
        limited = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", therm)
        throttled = bool(limited and int(limited.group(1)) < 100)
        mode = re.search(r"^\s*powermode\s+(\d)", core.run(["pmset", "-g"]), re.M)
        boot = boot_time()
        fans = heat.get("fans") or []
        alerts = []
        if temp and temp >= 100:
            alerts.append({"key": "hot", "level": "warn", "text": f"Chip at {temp}°C", "short": f"Hot: {temp}°C"})
        if throttled:
            alerts.append({"key": "throttled", "level": "warn", "text": "macOS is slowing the processor to cool it down.",
                           "short": "Processor slowed for heat"})
        return {
            "summary": f"{self.info['chip']} · up {core.ago(boot).replace(' ago', '') if boot else '?'}",
            "cpu": cpu, "gpu": gpu, "temp": temp,
            "cpu_c": round(heat["cpu_c"]) if heat.get("cpu_c") else None,
            "gpu_c": round(heat["gpu_c"]) if heat.get("gpu_c") else None,
            "fans": [{"rpm": f.get("rpm"), "max": f.get("max")} for f in fans],
            "power_mode": {"0": "Automatic", "1": "Low Power", "2": "High Power"}.get(mode.group(1)) if mode else None,
            "throttled": throttled, "boot": boot, "uptime_s": time.time() - boot if boot else None,
            "info": self.info,
            "alerts": alerts,
        }
