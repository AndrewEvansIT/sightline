Sightline.register("storage", {
  render(d, c) {
    const drives = (d.drives || []).map((x) => {
      const eject = x.ejectable && !c.readonly()
        ? `<button class="btn quiet" data-action="eject" data-params="${c.attr({ mount: x.mount })}" data-confirm title="Eject ${c.h(x.name)} so it's safe to unplug">Eject</button>`
        : "";
      return `<div class="meter"><div class="line"><span class="stack" style="min-width:0"><span class="lbl">${c.h(x.name)}</span>
          <span class="sub" style="font-size:11px;color:var(--faint)">${c.h(x.sub || "")}</span></span>
          <span style="display:flex;align-items:center;gap:6px">${c.h(x.text || "")}${eject}</span></div>
        <div class="track"><i class="${c.h(x.state || "off")}" style="width:${Math.max(0, Math.min(100, x.used_pct || 0))}%"></i></div></div>`;
    }).join("");
    const rows = (d.rows || []).length ? `<div class="sep">Backups and dropouts</div>${c.rows(d.rows)}` : "";
    return (drives || `<div class="empty">No drives found.</div>`) + rows;
  },
});
