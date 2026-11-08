Sightline.register("system", {
  render(d, c) {
    const fanAvg = d.fans && d.fans.length ? Math.round(d.fans.reduce((a, f) => a + (f.rpm || 0), 0) / d.fans.length) : null;
    const tempPct = d.temp != null ? Math.min(100, (d.temp / 105) * 100) : null;
    const gauges = [
      { label: "Processor", pct: d.cpu, color: "var(--cpu)", sub: d.info && d.info.p_cores ? `${d.info.p_cores + (d.info.e_cores || 0)} cores` : "" },
      { label: "Graphics", pct: d.gpu, color: "var(--gpu)", sub: d.info && d.info.gpu_cores ? `${d.info.gpu_cores} cores` : "" },
      { label: "Heat", pct: tempPct, color: d.temp >= 95 ? "var(--bad)" : d.temp >= 85 ? "var(--warn)" : "var(--ok)", text: d.temp != null ? `${d.temp}°` : "–",
        sub: d.cpu_c != null ? `avg ${d.cpu_c}°` : "" },
    ];
    const g = gauges.map((x) => `<div style="display:flex;flex-direction:column;align-items:center;gap:2px;min-width:0">
        ${c.ring(x.pct, { color: x.color, label: x.text, size: 70 })}
        <div style="font-size:11px;color:var(--dim)">${c.h(x.label)}</div><div style="font-size:10px;color:var(--faint)">${c.h(x.sub || "")}</div></div>`).join("");
    const chips = [];
    if (fanAvg != null) chips.push(`<span class="chip" title="${c.h(d.fans.map((f, i) => `Fan ${i + 1}: ${f.rpm} rpm of ${f.max}`).join("\n"))}">Fans ${fanAvg} rpm</span>`);
    if (d.power_mode) chips.push(`<span class="chip ${d.power_mode === "High Power" ? "warn" : ""}">${c.h(d.power_mode)}</span>`);
    if (d.throttled) chips.push(`<span class="chip bad">Slowed for heat</span>`);
    if (d.uptime_s) chips.push(`<span class="chip">Up ${c.h(c.dur(d.uptime_s))}</span>`);
    const spark = (key, color, label, max) => `<div style="min-width:0"><div style="display:flex;justify-content:space-between;font-size:11px;color:var(--faint)"><span>${label}</span><span>10 min</span></div>${c.spark(c.recent("system." + key), { color, max })}</div>`;
    return `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px">${g}</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0">${chips.join("")}</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">${spark("cpu", "var(--cpu)", "Processor", 100)}${spark("gpu", "var(--gpu)", "Graphics", 100)}${spark("temp", "var(--mem)", "Heat", 110)}</div>`;
  },
});
