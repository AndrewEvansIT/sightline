Sightline.register("ai_runtimes", {
  render(d, c) {
    const models = d.models || [];
    const mem = c.data("memory") || {};
    const total = d.total_gb || 0;
    // Header: memory in models vs what's still available for more.
    let head = "";
    if (models.length) {
      const avail = mem.available_gb;
      const pct = mem.total_gb ? Math.min(100, (total / mem.total_gb) * 100) : null;
      head = `<div class="meter"><div class="line"><span>${c.num(total, total >= 10 ? 0 : 1)} GB in models</span>
          <span>${avail != null ? `${c.num(avail, 0)} GB more available` : ""}${mem.total_gb ? ` · of ${mem.total_gb} GB` : ""}</span></div>
        ${pct != null ? `<div class="track"><i style="width:${pct}%;background:var(--model)"></i></div>` : ""}</div>`;
    }
    if (!models.length) {
      const found = (d.runtimes || []).map((r) => `<span class="chip ${r.running ? "ok" : ""}">${c.h(r.name)}${r.version ? " " + c.h(r.version) : ""}</span>`).join("");
      return `<div class="empty">${c.h(d.empty_text || "No local models are loaded.")}</div>
        ${found ? `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">${found}</div>` : ""}`;
    }
    const rows = models.map((m) => {
      const state = m.state === "loaded" ? "ok" : m.state === "loading" ? "warn" : "off";
      const name = `${c.h(m.name)}${m.quant ? ` <span class="t-faint">${c.h(m.quant)}</span>` : ""}`;
      const subParts = [m.runtime, m.who && m.who !== m.runtime ? `started by ${m.who}` : null, m.port ? `port ${m.port}` : null,
                        m.state === "idle" ? "no model in memory yet" : null, m.note].filter(Boolean);
      const title = [m.model, m.served && m.served.length > 1 ? `Serves: ${m.served.join(", ")}` : null, m.pid ? `Process ${m.pid}` : null].filter(Boolean).join("\n");
      const stop = m.can_stop && !c.readonly()
        ? `<button class="btn quiet" data-action="stop" data-params="${c.attr({ runtime: m.runtime, id: m.id })}" data-confirm title="Unload this model and free its memory">Stop</button>`
        : "";
      return `<div class="row"${title ? ` title="${c.h(title)}"` : ""}>${c.dot(state)}
        <span class="stack"><span class="lbl">${name}</span><span class="sub">${c.h(subParts.join(" · "))}</span></span>
        <span class="val">${m.gb != null ? c.num(m.gb, m.gb >= 10 ? 0 : 1) + " GB" : "–"}</span>${stop}</div>`;
    }).join("");
    const others = (d.runtimes || []).filter((r) => r.running && !models.some((m) => m.runtime === r.name));
    const note = others.length ? `<div class="note">${c.h(others.map((r) => r.name).join(", "))} running with nothing loaded.</div>` : "";
    return head + rows + note;
  },
});
