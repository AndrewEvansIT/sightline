Sightline.register("batteries", {
  render(d, c) {
    const color = (s) => (s === "bad" ? "var(--bad)" : s === "warn" ? "var(--warn)" : "var(--ok)");
    const bar = (pct, state) => `<div class="track" style="width:54px;height:5px;flex:none"><i style="width:${Math.max(0, Math.min(100, pct || 0))}%;background:${color(state)}"></i></div>`;
    const list = (d.devices || []).map((x) => {
      const parts = x.parts && x.parts.length
        ? `<div style="display:flex;gap:10px;padding:0 0 3px 0;font-size:11px;color:var(--faint)">${x.parts.map((p) =>
            `<span style="display:flex;align-items:center;gap:4px">${c.h(p.label)} ${bar(p.pct, p.pct <= 15 ? "bad" : p.pct <= 30 ? "warn" : "ok").replace("width:54px", "width:28px")} ${c.h(p.pct)}%</span>`).join("")}</div>`
        : "";
      const val = x.pct == null ? "–" : `${x.pct}%`;
      return `<div class="row" title="${c.h(x.kind || "")}"><span class="lbl">${c.h(x.name)}${x.charging ? ` <span class="t-faint">· charging</span>` : ""}</span>
          <span style="margin-left:auto;display:flex;align-items:center;gap:6px">${parts ? "" : bar(x.pct, x.state)}<span class="val" style="padding-left:0;min-width:32px;text-align:right">${c.h(val)}</span></span></div>${parts}`;
    }).join("");
    return list || `<div class="empty">No accessories report a battery.</div>`;
  },
});
