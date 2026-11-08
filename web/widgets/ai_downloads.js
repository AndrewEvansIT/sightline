(function () {
  Sightline.register("ai_downloads", {
    render(d, c) {
      const rows = (d.downloads || []).map((r) => {
        const amount = r.total_gb ? `${c.num(r.done_gb, 1)} of ${c.num(r.total_gb, 1)} GB` : `${c.num(r.done_gb, 1)} GB so far`;
        const pace = r.paused ? "paused" : r.speed ? `${c.bytes(r.speed)}/s` : "starting…";
        const left = r.paused ? "" : r.eta != null ? `${c.dur(r.eta)} left` : r.total_gb ? "" : "size not known";
        const pct = r.pct != null ? r.pct : 0;
        return `<div class="meter" style="margin:6px 0">
          <div class="line"><span class="lbl" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${c.h(r.name)}">${c.h(r.name)}</span>
            <span>${r.pct != null ? Math.floor(r.pct) + "%" : ""}</span></div>
          <div class="track"><i class="${r.paused ? "warn" : "ok"}" style="width:${Math.max(0, Math.min(100, pct))}%${r.pct == null ? ";opacity:.35;width:100%" : ""}"></i></div>
          <div class="line" style="font-size:11px;color:var(--faint)"><span>${c.h(r.source)} · ${c.h(amount)}${r.files > 1 ? ` · ${r.files} files` : ""}</span>
            <span>${c.h([pace, left].filter(Boolean).join(" · "))}</span></div></div>`;
      }).join("");
      const done = (d.finished || []).map((f) => `<div class="row" style="padding:1px 0;font-size:12px">${c.dot("ok")}<span class="lbl">${c.h(f.name)}</span><span class="val">finished ${c.ago(f.at)}</span></div>`).join("");
      if (!rows && !done) return `<div class="empty">Nothing downloading right now.</div>`;
      return (rows || `<div class="empty">Nothing downloading right now.</div>`) + (done ? `<div class="sep">Finished</div>${done}` : "");
    },
  });
})();
