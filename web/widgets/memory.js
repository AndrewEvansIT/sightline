(function () {
  const COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
  let open = null; // the group whose details are showing

  document.addEventListener("click", (ev) => {
    const r = ev.target.closest("[data-memgroup]");
    if (!r || ev.target.closest("[data-action]")) return;
    open = open === r.dataset.memgroup ? null : r.dataset.memgroup;
    Sightline.refresh();
  });

  Sightline.register("memory", {
    render(d, c) {
      let ci = 0;
      const parts = (d.parts || []).map((p) => {
        const color = p.macos ? "var(--fill)" : p.untracked ? "var(--warn)" : p.reclaimable ? "#3d4651" : p.other ? "var(--s6)" : COLORS[ci++ % COLORS.length];
        return Object.assign({ color }, p);
      });
      const freeGb = d.free_gb || 0;
      const pieSvg = c.pie(parts.map((p) => ({ value: p.gb, color: p.color, thin: p.reclaimable })).concat([{ value: freeGb, color: "#252a30", thin: true }]),
        { total: d.total_gb, label: `${Math.round(d.used_gb)} GB`, sub: `of ${d.total_gb} GB`, size: 108 });
      const legend = parts.map((p) => `<div class="row" style="padding:1px 0;font-size:12px"><span class="dot" style="background:${p.color}"></span><span class="lbl">${c.h(p.name)}</span><span class="val">${c.num(p.gb, 1)} GB</span></div>`).join("") +
        `<div class="row" style="padding:1px 0;font-size:12px"><span class="dot" style="background:#252a30;box-shadow:inset 0 0 0 1px var(--line-2)"></span><span class="lbl">Free</span><span class="val">${c.num(freeGb, 1)} GB</span></div>`;
      const chips = [`<span class="chip">${c.num(d.available_gb, 0)} GB available</span>`];
      if (d.swap_gb > 0.05) chips.push(`<span class="chip ${d.swap_gb > 4 ? "warn" : ""}">Swap ${c.num(d.swap_gb, 1)} GB</span>`);
      if (d.pressure && d.pressure !== "normal") chips.push(`<span class="chip ${d.pressure === "critical" ? "bad" : "warn"}">Pressure ${c.h(d.pressure)}</span>`);
      const list = (d.groups || []).map((g) => {
        const can = !c.readonly() && ["app", "agent", "process", "model"].includes(g.kind);
        const verb = g.kind === "app" ? "Quit" : "Stop";
        const detail = open === g.name ? `<div style="padding:2px 0 6px 16px;font-size:11px;color:var(--faint)">${c.h(g.about || "")}
            ${(g.procs || []).slice(0, 8).map((p) => `<div class="row" style="padding:0"><span class="lbl">${c.h(p.name)}</span><span class="val">${p.mb >= 1024 ? c.num(p.mb / 1024, 1) + " GB" : p.mb + " MB"}</span></div>`).join("")}</div>` : "";
        return `<div class="row clickable" data-memgroup="${c.h(g.name)}"><span class="lbl">${c.h(g.name)}${g.count > 1 ? ` <span class="t-faint">· ${g.count}</span>` : ""}</span>
            <span class="val">${c.num(g.gb, g.gb >= 10 ? 0 : 1)} GB</span>${can ? `<button class="btn quiet" data-action="stop" data-params="${c.attr({ name: g.name })}" data-confirm>${verb}</button>` : ""}</div>${detail}`;
      }).join("");
      return `<div style="display:flex;gap:12px;align-items:center">${pieSvg}<div style="flex:1;min-width:0">${legend}</div></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 6px">${chips.join("")}</div>
        <div class="sep">Using memory</div><div>${list}</div>`;
    },
  });
})();
