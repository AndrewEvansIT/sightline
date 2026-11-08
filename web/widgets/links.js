Sightline.register("links", {
  render(d, c) {
    const list = (d.rows || []).map((r) => {
      if (r.sep) return `<div class="sep">${c.h(r.sep)}</div>`;
      const pad = (r.depth || 0) * 14;
      const branch = r.depth ? `<span class="t-faint" style="flex:none">└</span>` : "";
      const label = r.sub
        ? `<span class="stack"><span class="lbl">${c.h(r.label)}</span><span class="sub">${c.h(r.sub)}</span></span>`
        : `<span class="lbl">${c.h(r.label)}</span>`;
      const val = r.state === "bad" ? `<span class="val t-bad">${c.h(r.value || "")}</span>` : `<span class="val">${c.h(r.value || "")}</span>`;
      return `<div class="row" style="padding-left:${pad}px">${branch}${r.state ? c.dot(r.state) : ""}${label}${val}</div>`;
    }).join("");
    return (list || `<div class="empty">Nothing plugged in.</div>`) + (d.note ? `<div class="note">${c.h(d.note)}</div>` : "");
  },
});
