Sightline.register("power", {
  render(d, c) {
    const key = (d.label || "").startsWith("Whole Mac") ? "system" : d.label === "Graphics" ? "gpu" : null;
    const series = key ? c.recent("power." + key) : [];
    const peak = series.length ? Math.max(...series) : null;
    return `${c.bigs(d.big)}
      ${series.length > 1 ? `<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--faint)"><span>${c.h(d.label)}, last 10 min</span><span>peak ${c.num(peak, 0)} W</span></div>${c.spark(series, { color: "var(--mem)" })}` : ""}
      ${d.note ? `<div class="note">${c.h(d.note)}</div>` : ""}`;
  },
});
