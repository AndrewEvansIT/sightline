Sightline.register("custom", {
  render(d, c) {
    const tiles = d.tiles || [];
    if (!tiles.length) return `<div class="empty">No script tiles switched on. Add one in Settings.</div>`;
    return tiles.map((t) => {
      const when = t.ran ? c.ago(t.ran) : t.running ? "running…" : "not run yet";
      const head = `<div class="sep" style="display:flex;gap:6px">${t.ok === false ? c.dot("bad") : ""}<span>${c.h(t.title)}</span>
        ${t.data && t.data.summary ? `<span class="t-dim" style="text-transform:none;letter-spacing:0">${c.h(t.data.summary)}</span>` : ""}
        <span style="margin-left:auto;text-transform:none;letter-spacing:0">${c.h(when)}</span></div>`;
      let body = "";
      if (t.data) body = c.generic(t.data);
      else if (t.text) body = `<div class="t-dim" style="white-space:pre-wrap;font:11px/1.4 ui-monospace,Menlo,monospace;max-height:9em;overflow:auto">${c.h(t.text)}</div>`;
      else if (!t.error && t.ran) body = `<div class="empty">No output.</div>`;
      if (t.error) body += `<div class="t-bad" style="font-size:11px">${c.h(t.error)}</div>`;
      return head + body;
    }).join("");
  },
});
